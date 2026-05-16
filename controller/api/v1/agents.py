import asyncio
import hmac
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from controller.config import settings as controller_settings
from controller.database.database import get_db
from controller.database.repositories import AgentRepository, GlobalSettingsRepository
from controller.core.agent_manager import AgentManager
from controller.core.agent_sync import get_agent_host, push_cert_refresh
from controller.core.auth import require_api_token, require_agent_token, generate_token
from shared.models import AgentRegistration, AgentHeartbeat, AgentConfig, AgentStatus

router = APIRouter()


@router.get("/controller-cert", response_class=PlainTextResponse, include_in_schema=False)
def get_controller_cert():
    """Return the controller's public TLS certificate in PEM format.

    Intentionally unauthenticated — agents call this on first registration
    to download and cache the cert (Trust On First Use / TOFU).
    Only the public certificate is returned, never the private key.
    """
    cert_file = controller_settings.ssl_certfile
    if not cert_file:
        raise HTTPException(status_code=404, detail="TLS not configured on this controller")
    cert_path = Path(cert_file)
    if not cert_path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file not found")
    return cert_path.read_text(encoding="utf-8")


class TestPortResponse(BaseModel):
    reachable: bool
    message: str


def _agent_auth(
    agent_id: int,
    x_agent_token: Optional[str] = Header(default=None, alias="X-Agent-Token"),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """FastAPI dependency: validates per-agent token or admin token for agent-scoped endpoints."""
    require_agent_token(agent_id, x_agent_token, x_api_token, authorization, db)


@router.post("/register")
def register_agent(
    registration: AgentRegistration,
    x_agent_token: Optional[str] = Header(default=None, alias="X-Agent-Token"),
    db: Session = Depends(get_db),
):
    """Register a new agent or update existing registration.

    Returns AgentStatus fields plus an 'agent_token' the agent must use for all
    subsequent requests to the controller API.
    """
    gs = GlobalSettingsRepository(db).get()
    agent_secret = (gs.agent_secret if gs and gs.agent_secret else None) or controller_settings.agent_secret
    if agent_secret:
        provided = registration.agent_secret or ""
        if not hmac.compare_digest(provided.encode(), agent_secret.encode()):
            # Allow re-registration when the agent provides its existing valid token.
            # This lets already-known agents re-register after a push-update even if
            # the controller's agent_secret changed since the agent was first installed.
            repo = AgentRepository(db)
            wg_ip = registration.wireguard_ip if registration.wireguard_ip else None
            existing = repo.get_by_wireguard_ip(wg_ip) if wg_ip else repo.get_by_hostname_internal(registration.hostname)
            if not (x_agent_token and existing and existing.agent_token and
                    hmac.compare_digest(x_agent_token, existing.agent_token)):
                raise HTTPException(status_code=401, detail="Invalid or missing agent secret")
    manager = AgentManager(db)
    agent = manager.register_agent(registration)

    # Always issue a fresh token on registration so push-updated agents immediately
    # have a valid token without depending on a stale file from before the update.
    agent.agent_token = generate_token()
    db.commit()
    db.refresh(agent)

    return {
        "id": agent.id,
        "hostname": agent.hostname,
        "wireguard_ip": agent.wireguard_ip,
        "public_ip": agent.public_ip,
        "status": agent.status,
        "last_heartbeat": agent.last_heartbeat,
        "active_connections": agent.active_connections,
        "cpu_percent": agent.cpu_percent,
        "memory_percent": agent.memory_percent,
        "version": agent.version,
        "created_at": agent.created_at,
        "agent_token": agent.agent_token,
    }


@router.post("/{agent_id}/heartbeat", response_model=AgentStatus)
def heartbeat(
    agent_id: int,
    heartbeat_data: AgentHeartbeat,
    db: Session = Depends(get_db),
    _auth: None = Depends(_agent_auth),
):
    """Process agent heartbeat."""
    manager = AgentManager(db)
    agent = manager.process_heartbeat(agent_id, heartbeat_data)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentStatus(
        id=agent.id,
        hostname=agent.hostname,
        wireguard_ip=agent.wireguard_ip,
        public_ip=agent.public_ip,
        status=agent.status,
        last_heartbeat=agent.last_heartbeat,
        active_connections=agent.active_connections,
        cpu_percent=agent.cpu_percent,
        memory_percent=agent.memory_percent,
        version=agent.version,
        created_at=agent.created_at
    )


@router.get("/{agent_id}/config", response_model=AgentConfig)
def get_agent_config(
    agent_id: int,
    db: Session = Depends(get_db),
    _auth: None = Depends(_agent_auth),
):
    """Get configuration for an agent."""
    manager = AgentManager(db)
    config = manager.get_agent_config(agent_id)
    if not config:
        raise HTTPException(status_code=404, detail="Agent not found")
    return config


@router.get("", response_model=list[AgentStatus])
def list_agents(
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_token),
):
    """List all agents."""
    repo = AgentRepository(db)
    agents = repo.get_all()
    return [
        AgentStatus(
            id=a.id,
            hostname=a.hostname,
            wireguard_ip=a.wireguard_ip,
            public_ip=a.public_ip,
            status=a.status,
            last_heartbeat=a.last_heartbeat,
            active_connections=a.active_connections,
            cpu_percent=a.cpu_percent,
            memory_percent=a.memory_percent,
            version=a.version,
            created_at=a.created_at
        )
        for a in agents
    ]


@router.get("/{agent_id}", response_model=AgentStatus)
def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_token),
):
    """Get specific agent details."""
    repo = AgentRepository(db)
    agent = repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentStatus(
        id=agent.id,
        hostname=agent.hostname,
        wireguard_ip=agent.wireguard_ip,
        public_ip=agent.public_ip,
        status=agent.status,
        last_heartbeat=agent.last_heartbeat,
        active_connections=agent.active_connections,
        cpu_percent=agent.cpu_percent,
        memory_percent=agent.memory_percent,
        version=agent.version,
        created_at=agent.created_at
    )


@router.delete("/{agent_id}")
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_token),
):
    """Remove an agent."""
    repo = AgentRepository(db)
    if not repo.delete(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted", "agent_id": agent_id}


@router.post("/push-cert-refresh")
async def push_cert_refresh_all(
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_token),
):
    """Push a cert-cache-clear + re-TOFU command to all reachable agents.

    Use this after regenerating the controller's TLS certificate so agents
    pick up the new cert without needing to be rebuilt or manually reconfigured.
    """
    result = await push_cert_refresh(db)
    return result


@router.post("/{agent_id}/push-cert-refresh")
async def push_cert_refresh_one(
    agent_id: int,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_token),
):
    """Push a cert-cache-clear + re-TOFU command to a single agent."""
    result = await push_cert_refresh(db, agent_ids=[agent_id])
    return result


@router.get("/{agent_id}/test-port", response_model=TestPortResponse)
async def test_agent_port(
    agent_id: int,
    port: int,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_token),
):
    """Test TCP connectivity to a port on the agent (e.g. to verify firewall blocking).
    Connects from the controller to agent host:port. Use to confirm a port is blocked or reachable.
    """
    if port < 1 or port > 65535:
        return TestPortResponse(reachable=False, message="Port must be 1-65535")
    repo = AgentRepository(db)
    agent = repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    host = get_agent_host(agent)
    if not host:
        return TestPortResponse(
            reachable=False,
            message="Agent has no WireGuard IP or control_url; cannot determine host to test.",
        )
    try:
        await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
        return TestPortResponse(reachable=True, message=f"Port {port} on {agent.hostname} ({host}) is reachable.")
    except asyncio.TimeoutError:
        return TestPortResponse(reachable=False, message=f"Connection to {host}:{port} timed out (port may be blocked or closed).")
    except ConnectionRefusedError:
        return TestPortResponse(reachable=False, message=f"Connection to {host}:{port} refused (port closed or blocked).")
    except OSError as e:
        return TestPortResponse(reachable=False, message=f"Error: {e!s}")
