from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from controller.database.database import get_db
from controller.database.repositories import BlocklistRepository
from controller.core.agent_sync import trigger_sync_all_agents
from controller.core.auth import require_api_token

router = APIRouter()


def _blocklist_report_auth(
    x_agent_token: Optional[str] = Header(default=None, alias="X-Agent-Token"),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Accept either a valid agent token (any registered agent) or admin token."""
    import hmac
    from controller.core.auth import _get_active_api_token
    from controller.database.models import Agent
    from fastapi import HTTPException

    admin_token = x_api_token
    if not admin_token and authorization and authorization.lower().startswith("bearer "):
        admin_token = authorization[7:].strip()
    active_admin = _get_active_api_token(db)
    if active_admin and admin_token and hmac.compare_digest(admin_token, active_admin):
        return

    if x_agent_token:
        agent = db.query(Agent).filter(Agent.agent_token == x_agent_token).first()
        if agent:
            return

    raise HTTPException(status_code=401, detail="Invalid or missing token")


class BlocklistAdd(BaseModel):
    ip: str
    reason: Optional[str] = None


class BlocklistReport(BaseModel):
    """Report from agent: add IP to blocklist and sync to all agents."""
    ip: str
    reason: str
    agent_id: int


class BlocklistEntry(BaseModel):
    id: int
    ip: str
    reason: Optional[str]
    added_at: str


@router.post("", status_code=201)
def add_to_blocklist(entry: BlocklistAdd, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Add an IP to the blocklist."""
    repo = BlocklistRepository(db)

    if repo.is_blocked(entry.ip):
        raise HTTPException(status_code=400, detail="IP already in blocklist")

    repo.add(entry.ip, entry.reason)
    return {"status": "added", "ip": entry.ip}


@router.post("/report", status_code=201)
async def report_block_from_agent(payload: BlocklistReport, db: Session = Depends(get_db), _auth: None = Depends(_blocklist_report_auth)):
    """Agent reports an IP to block (e.g. after local auto-block). Adds to blocklist and triggers sync to all agents."""
    repo = BlocklistRepository(db)

    if repo.is_blocked(payload.ip):
        return {"status": "already_blocked", "ip": payload.ip}

    repo.add(payload.ip, payload.reason, source="agent_report")

    result = await trigger_sync_all_agents(db)
    return {
        "status": "added",
        "ip": payload.ip,
        "synced_agents": result["success"],
        "failed_agents": result["failed"],
    }


@router.get("", response_model=list[BlocklistEntry])
def list_blocklist(db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """List all blocked IPs."""
    repo = BlocklistRepository(db)
    entries = repo.get_all()
    return [
        BlocklistEntry(
            id=e.id,
            ip=e.ip,
            reason=e.reason,
            added_at=e.added_at.isoformat()
        )
        for e in entries
    ]


@router.delete("/{ip}")
def remove_from_blocklist(ip: str, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Remove an IP from the blocklist."""
    repo = BlocklistRepository(db)
    if not repo.remove(ip):
        raise HTTPException(status_code=404, detail="IP not in blocklist")
    return {"status": "removed", "ip": ip}


@router.get("/check/{ip}")
def check_blocked(ip: str, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Check if an IP is blocked."""
    repo = BlocklistRepository(db)
    return {"ip": ip, "blocked": repo.is_blocked(ip)}
