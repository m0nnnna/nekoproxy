"""Web dashboard routes using Jinja2 templates."""

import hmac
import httpx
import asyncio
import logging
from typing import Optional

import json

from fastapi import APIRouter, Depends, Request, Form, HTTPException, UploadFile, File, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from controller.config import settings

logger = logging.getLogger(__name__)
from controller.database.database import get_db
from controller.database.repositories import (
    AgentRepository,
    ServiceRepository,
    ServiceAssignmentRepository,
    BlocklistRepository,
    ConnectionStatRepository,
    FirewallRuleRepository,
    AlertRepository,
    EmailConfigRepository,
    EmailUserRepository,
    EmailBlocklistRepository,
    GlobalSettingsRepository,
)
from controller.core.email_manager import EmailManager
from controller.core.agent_sync import trigger_sync_all_agents, get_agent_base_url, get_agent_host
from controller.core.auth import create_session, validate_session, invalidate_session, get_web_password
from controller.core.agent_sync import _controller_token, _agent_api_ssl_verify
from shared.models.common import Protocol, FirewallAction, AlertSeverity, AlertType, EmailBlocklistType

# Ensure templates directory exists
settings.templates_dir.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(settings.templates_dir))

router = APIRouter()


# ---------------------------------------------------------------------------
# Session authentication helpers
# ---------------------------------------------------------------------------

def _require_session(
    neko_session: Optional[str] = Cookie(default=None),
):
    """FastAPI dependency: raise 401 if session cookie is invalid."""
    if not validate_session(neko_session):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login form."""
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Validate password (API token) and issue session cookie."""
    web_password = get_web_password(db)
    if web_password and hmac.compare_digest(password, web_password):
        session_token = create_session()
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            "neko_session",
            session_token,
            httponly=True,
            samesite="strict",
            secure=bool(settings.ssl_certfile),  # Secure flag when HTTPS is enabled
        )
        return response
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid password"},
        status_code=401,
    )


@router.post("/logout")
@router.get("/logout")
async def logout(neko_session: Optional[str] = Cookie(default=None)):
    """Invalidate session and redirect to login."""
    invalidate_session(neko_session)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("neko_session")
    return response


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Main dashboard page."""
    agent_repo = AgentRepository(db)
    stat_repo = ConnectionStatRepository(db)
    blocklist_repo = BlocklistRepository(db)

    agents = agent_repo.get_all()
    stats_summary = stat_repo.get_stats_summary(hours=24)
    stats_summary["auto_blocklist_count"] = blocklist_repo.get_auto_added_count(hours=24)
    recent_connections = stat_repo.get_recent(hours=1, limit=10)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "agents": agents,
        "stats": stats_summary,
        "recent_connections": recent_connections,
        "active_page": "dashboard"
    })


AGENT_BINARY_FILENAME = "nekoproxy-agent"


def _get_uploaded_agent_path():
    """Path to the uploaded agent binary (if any)."""
    d = settings.uploads_agent_dir
    return d / AGENT_BINARY_FILENAME


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Agents management page."""
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_all()
    agent_binary_path = _get_uploaded_agent_path()
    has_uploaded_binary = agent_binary_path.is_file()

    return templates.TemplateResponse("agents.html", {
        "request": request,
        "agents": agents,
        "has_uploaded_binary": has_uploaded_binary,
        "active_page": "agents"
    })


@router.post("/agents/upload", response_class=HTMLResponse)
async def upload_agent_binary(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Store uploaded agent binary for push-update."""
    upload_dir = settings.uploads_agent_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / AGENT_BINARY_FILENAME
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")
        path.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Redirect back to agents page so user sees updated state
    return RedirectResponse(url="/agents", status_code=303)


@router.post("/agents/{agent_id}/push-update", response_class=HTMLResponse)
async def push_agent_update(
    request: Request,
    agent_id: int,
    db: Session = Depends(get_db)
):
    """Stream the uploaded agent binary to the agent's /update-binary endpoint."""
    import httpx
    agent_repo = AgentRepository(db)
    agent = agent_repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    base_url = get_agent_base_url(agent)
    if not base_url:
        return HTMLResponse(
            '<span class="text-amber-600">Agent has no WireGuard IP or control_url: push update not available.</span>',
            status_code=400
        )
    path = _get_uploaded_agent_path()
    if not path.is_file():
        return HTMLResponse(
            '<span class="text-red-500">No agent binary uploaded. Upload one above first.</span>',
            status_code=400
        )
    url = f"{base_url.rstrip('/')}/update-binary"
    _ctrl_headers = {}
    if _controller_token():
        _ctrl_headers['X-Controller-Token'] = _controller_token()
    try:
        with open(path, "rb") as f:
            body = f.read()
        async with httpx.AsyncClient(timeout=120.0, verify=_agent_api_ssl_verify()) as client:
            r = await client.post(url, content=body, headers=_ctrl_headers)
        if r.status_code == 200:
            return HTMLResponse(
                f'<span class="text-green-600">Update pushed to {agent.hostname}. Agent will restart with new binary.</span>'
            )
        return HTMLResponse(
            f'<span class="text-red-500">Agent returned {r.status_code}: {r.text[:200]}</span>',
            status_code=r.status_code
        )
    except Exception as e:
        return HTMLResponse(
            f'<span class="text-red-500">Failed: {e!s}</span>',
            status_code=500
        )


@router.post("/agents/{agent_id}/internal", response_class=HTMLResponse)
async def set_agent_internal(
    request: Request,
    agent_id: int,
    db: Session = Depends(get_db),
):
    """Toggle internal flag for an agent (looser port control: all proxy ports allowed on public)."""
    form = await request.form()
    internal = form.get("internal") in ("on", "1", "true", "yes")
    repo = AgentRepository(db)
    agent = repo.update_internal(agent_id, internal)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await trigger_sync_all_agents(db, agent_ids=[agent_id])
    # Return small fragment for the cell so the row updates
    return HTMLResponse(
        f'<label class="inline-flex items-center gap-1">'
        f'<input type="checkbox" name="internal" value="on" '
        f'hx-post="/agents/{agent_id}/internal" hx-trigger="change" hx-swap="outerHTML" hx-target="closest label" '
        f'{"checked" if agent.internal else ""}> Internal</label>'
    )


@router.post("/agents/{agent_id}/route-via", response_class=HTMLResponse)
async def set_agent_route_via(
    request: Request,
    agent_id: int,
    db: Session = Depends(get_db),
):
    """Set (or clear) which agent this agent routes its outbound connections through."""
    form = await request.form()
    raw = form.get("route_via_agent_id", "")
    route_via_id = int(raw) if raw and str(raw).isdigit() else None

    repo = AgentRepository(db)
    agent = repo.update_route_via(agent_id, route_via_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    all_agents = repo.get_all()
    await trigger_sync_all_agents(db, agent_ids=[agent_id])

    opts = '<option value="">— direct —</option>'
    for a in all_agents:
        if a.id != agent_id:
            sel = "selected" if a.id == route_via_id else ""
            opts += f'<option value="{a.id}" {sel}>{a.hostname}</option>'

    return HTMLResponse(
        f'<select name="route_via_agent_id" '
        f'hx-post="/agents/{agent_id}/route-via" hx-trigger="change" '
        f'hx-swap="outerHTML" hx-target="this" '
        f'class="neko-input text-sm py-0.5 px-1 min-w-[8rem]">'
        f'{opts}</select>'
    )


@router.delete("/agents/{agent_id}", response_class=HTMLResponse)
async def delete_agent_htmx(agent_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete agent via htmx."""
    repo = AgentRepository(db)
    if not repo.delete(agent_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.get("/services")
async def services_page_redirect():
    """Redirect old services page to unified rules page."""
    return RedirectResponse(url="/rules", status_code=302)


@router.post("/services", response_class=HTMLResponse)
async def create_service_htmx(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    listen_port: int = Form(...),
    backend_host: str = Form(...),
    backend_port: int = Form(...),
    protocol: str = Form("tcp"),
    db: Session = Depends(get_db)
):
    """Create service via htmx form."""
    repo = ServiceRepository(db)

    if repo.get_by_name(name):
        return HTMLResponse(
            '<div class="text-red-500">Service with this name already exists</div>',
            status_code=400
        )

    # Check for listen port conflict
    existing_port = repo.get_by_listen_port(listen_port, Protocol(protocol))
    if existing_port:
        return HTMLResponse(
            f'<div class="text-red-500">Listen port {listen_port}/{protocol} already in use</div>',
            status_code=400
        )

    repo.create(
        name=name,
        description=description or None,
        listen_port=listen_port,
        backend_host=backend_host,
        backend_port=backend_port,
        protocol=Protocol(protocol)
    )

    # Return updated services list
    services = repo.get_all()
    return templates.TemplateResponse("partials/services_table.html", {
        "request": request,
        "services": services
    })


@router.delete("/services/{service_id}", response_class=HTMLResponse)
async def delete_service_htmx(service_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete service via htmx."""
    repo = ServiceRepository(db)
    if not repo.delete(service_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.get("/assignments")
async def assignments_page_redirect():
    """Redirect old assignments page to unified rules page."""
    return RedirectResponse(url="/rules", status_code=302)


@router.post("/assignments", response_class=HTMLResponse)
async def create_assignment_htmx(
    request: Request,
    service_id: int = Form(...),
    agent_id: str = Form(""),  # Empty string means all agents
    db: Session = Depends(get_db)
):
    """Create service assignment via htmx form."""
    assign_repo = ServiceAssignmentRepository(db)
    service_repo = ServiceRepository(db)
    agent_repo = AgentRepository(db)

    # Validate service exists
    service = service_repo.get_by_id(service_id)
    if not service:
        return HTMLResponse(
            '<div class="text-red-500">Service not found</div>',
            status_code=400
        )

    # Parse agent_id (empty string = all agents)
    parsed_agent_id = int(agent_id) if agent_id else None

    # Validate agent if specified
    if parsed_agent_id:
        agent = agent_repo.get_by_id(parsed_agent_id)
        if not agent:
            return HTMLResponse(
                '<div class="text-red-500">Agent not found</div>',
                status_code=400
            )

    # Check for duplicate
    if assign_repo.exists(service_id, parsed_agent_id):
        target = "all agents" if parsed_agent_id is None else f"agent {parsed_agent_id}"
        return HTMLResponse(
            f'<div class="text-red-500">Service already assigned to {target}</div>',
            status_code=400
        )

    assign_repo.create(
        service_id=service_id,
        agent_id=parsed_agent_id,
        enabled=True
    )

    # Return updated assignments list
    assignments = assign_repo.get_all()
    services = service_repo.get_all()
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/assignments_table.html", {
        "request": request,
        "assignments": assignments,
        "services": services,
        "agents": agents
    })


@router.delete("/assignments/{assignment_id}", response_class=HTMLResponse)
async def delete_assignment_htmx(assignment_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete assignment via htmx."""
    repo = ServiceAssignmentRepository(db)
    if not repo.delete(assignment_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/assignments/{assignment_id}/toggle", response_class=HTMLResponse)
async def toggle_assignment_htmx(request: Request, assignment_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Toggle assignment enabled status via htmx."""
    assign_repo = ServiceAssignmentRepository(db)
    service_repo = ServiceRepository(db)
    agent_repo = AgentRepository(db)

    assignment = assign_repo.get_by_id(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404)

    assign_repo.update(assignment_id, enabled=not assignment.enabled)

    # Return updated assignments list
    assignments = assign_repo.get_all()
    services = service_repo.get_all()
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/assignments_table.html", {
        "request": request,
        "assignments": assignments,
        "services": services,
        "agents": agents
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Global settings page (geo, lockdown, controller URL, apply to agents)."""
    gs_repo = GlobalSettingsRepository(db)
    gs = gs_repo.get()
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_all()
    # Merge DB settings with env for display (DB overrides)
    settings_dict = {
        "controller_url": (gs.controller_url if gs else None) or getattr(settings, "controller_url", None) or "",
        "geo_mode": (gs.geo_mode if gs else None) or settings.geo_mode or "off",
        "geo_countries": (gs.geo_countries if gs else None) or settings.geo_countries or "",
        "idle_connection_timeout_seconds": gs.idle_connection_timeout_seconds if gs and gs.idle_connection_timeout_seconds is not None else getattr(settings, "idle_connection_timeout_seconds", 0) or 0,
        "paranoid": gs.paranoid if gs and gs.paranoid is not None else getattr(settings, "paranoid", False),
        "agent_secret": (gs.agent_secret if gs else None) or settings.agent_secret or "",
        "forward_proxy_port": gs.forward_proxy_port if gs and gs.forward_proxy_port else 0,
        "forward_proxy_auth": gs.forward_proxy_auth if gs and gs.forward_proxy_auth else "",
        "dns_port": gs.dns_port if gs and gs.dns_port else 0,
        "dns_upstream": gs.dns_upstream if gs and gs.dns_upstream else "1.1.1.1:53",
    }
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings_dict,
        "agents": agents,
        "active_page": "settings"
    })


@router.post("/settings/apply", response_class=HTMLResponse)
async def apply_settings_htmx(
    request: Request,
    controller_url: str = Form(""),
    geo_mode: str = Form("off"),
    geo_countries: str = Form(""),
    idle_connection_timeout_seconds: int = Form(0),
    agent_secret: str = Form(""),
    forward_proxy_port: int = Form(0),
    forward_proxy_auth: str = Form(""),
    dns_port: int = Form(0),
    dns_upstream: str = Form("1.1.1.1:53"),
    apply_to: str = Form("all"),
    db: Session = Depends(get_db)
):
    """Save global settings and push config to selected agents (or all)."""
    form = await request.form()
    agent_ids_raw = form.getlist("agent_ids")
    paranoid = form.get("paranoid") in ("1", "true", "on", "yes")

    gs_repo = GlobalSettingsRepository(db)
    gs_repo.update(
        controller_url=controller_url.strip() or None,
        geo_mode=geo_mode.strip() or None,
        geo_countries=geo_countries.strip() or None,
        idle_connection_timeout_seconds=idle_connection_timeout_seconds if idle_connection_timeout_seconds >= 0 else 0,
        paranoid=paranoid,
        agent_secret=agent_secret.strip() or None,
        forward_proxy_port=forward_proxy_port if forward_proxy_port > 0 else 0,
        forward_proxy_auth=forward_proxy_auth.strip() or None,
        dns_port=dns_port if dns_port > 0 else 0,
        dns_upstream=dns_upstream.strip() or None,
    )
    agent_id_list = None
    if apply_to == "selected" and agent_ids_raw:
        try:
            agent_id_list = [int(a) for a in agent_ids_raw]
        except (ValueError, TypeError):
            agent_id_list = None
    result = await trigger_sync_all_agents(db, agent_ids=agent_id_list)
    return HTMLResponse(
        f'<span class="text-green-600">Settings saved. Synced to {result["success"]} agent(s).'
        + (f' {result["failed"]} failed.</span>' if result["failed"] else ".</span>")
    )


@router.get("/blocklist", response_class=HTMLResponse)
async def blocklist_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """IP Blocklist management page."""
    blocklist_repo = BlocklistRepository(db)
    entries = blocklist_repo.get_all()

    return templates.TemplateResponse("blocklist.html", {
        "request": request,
        "entries": entries,
        "active_page": "blocklist"
    })


@router.post("/blocklist", response_class=HTMLResponse)
async def add_blocklist_htmx(
    request: Request,
    ip: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db)
):
    """Add IP to blocklist via htmx."""
    repo = BlocklistRepository(db)

    if repo.is_blocked(ip):
        return HTMLResponse(
            '<div class="text-red-500">IP already blocked</div>',
            status_code=400
        )

    repo.add(ip, reason or None)

    # Auto-sync blocklist to all agents (firewall-style: push immediately)
    await trigger_sync_all_agents(db)

    # Return updated blocklist
    entries = repo.get_all()
    return templates.TemplateResponse("partials/blocklist_table.html", {
        "request": request,
        "entries": entries
    })


@router.delete("/blocklist/{ip}", response_class=HTMLResponse)
async def remove_blocklist_htmx(ip: str, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Remove IP from blocklist via htmx."""
    repo = BlocklistRepository(db)
    if not repo.remove(ip):
        raise HTTPException(status_code=404)
    # Auto-sync to all agents
    await trigger_sync_all_agents(db)
    return HTMLResponse("")


@router.post("/blocklist/apply", response_class=HTMLResponse)
async def apply_blocklist_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Push config sync to all reachable agents (wireguard_ip or control_url)."""
    agent_repo = AgentRepository(db)
    agents = [a for a in agent_repo.get_all() if get_agent_base_url(a)]

    if not agents:
        return HTMLResponse(
            '<div class="text-yellow-500">No reachable agents to sync (set WireGuard IP or control_url)</div>',
            status_code=200
        )

    async def trigger_agent_sync(agent):
        base = get_agent_base_url(agent)
        url = f"{base.rstrip('/')}/trigger-sync" if base else None
        if not url:
            return False
        _t = _controller_token()
        _h = {'X-Controller-Token': _t} if _t else {}
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=_agent_api_ssl_verify()) as client:
                response = await client.post(url, headers=_h)
                if response.status_code == 200:
                    logger.info(f"Triggered sync on agent {agent.hostname}")
                    return True
                else:
                    logger.warning(f"Failed to trigger sync on {agent.hostname}: {response.status_code}")
                    return False
        except Exception as e:
            logger.warning(f"Failed to reach agent {agent.hostname}: {e}")
            return False

    tasks = [trigger_agent_sync(agent) for agent in agents]
    outcomes = await asyncio.gather(*tasks)

    success = sum(1 for o in outcomes if o)
    failed = sum(1 for o in outcomes if not o)

    if failed == 0:
        return HTMLResponse(f'<div class="text-green-500">Synced {success} agent(s)</div>')
    elif success == 0:
        return HTMLResponse(f'<div class="text-red-500">Failed to sync all {failed} agent(s)</div>')
    else:
        return HTMLResponse(f'<div class="text-yellow-500">Synced {success}, failed {failed} agent(s)</div>')


@router.get("/live", response_class=HTMLResponse)
async def live_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Live view: connections in the last 60 seconds, auto-refreshing."""
    stat_repo = ConnectionStatRepository(db)
    connections = stat_repo.get_recent_seconds(seconds=60, limit=200)
    return templates.TemplateResponse("live.html", {
        "request": request,
        "connections": connections,
        "active_page": "live"
    })


@router.get("/partials/live-connections", response_class=HTMLResponse)
async def live_connections_partial(request: Request, seconds: int = 60, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Partial for live connection list (HTMX poll every 2s)."""
    stat_repo = ConnectionStatRepository(db)
    connections = stat_repo.get_recent_seconds(seconds=seconds, limit=200)
    return templates.TemplateResponse("partials/live_connections.html", {
        "request": request,
        "connections": connections,
    })


@router.get("/live/stream")
async def live_stream(request: Request, _auth: None = Depends(_require_session)):
    """SSE endpoint: streams live connection events to the browser."""
    from controller.core.live_events import live_events

    q = live_events.subscribe()

    async def event_generator():
        # Replay history so the page loads with existing data
        for channel in ("incoming", "dns", "forward", "email"):
            for evt in reversed(live_events.get_history(channel)):
                yield f"data: {json.dumps(evt)}\n\n"
        # Stream new events as they arrive
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(evt)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            live_events.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, period: str = "24h", _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Statistics page with time range selection."""
    from controller.database.repositories import EmailStatRepository, FirewallStatRepository

    stat_repo = ConnectionStatRepository(db)
    blocklist_repo = BlocklistRepository(db)
    email_stat_repo = EmailStatRepository(db)
    firewall_stat_repo = FirewallStatRepository(db)

    # Parse period to hours (None for lifetime)
    period_map = {"24h": 24, "7d": 168, "30d": 720, "lifetime": None}
    hours = period_map.get(period, 24)

    summary = stat_repo.get_stats_summary(hours=hours)
    summary["auto_blocklist_count"] = blocklist_repo.get_auto_added_count(hours=hours)
    email_summary = email_stat_repo.get_stats_summary(hours=hours)
    firewall_summary = firewall_stat_repo.get_stats_summary(hours=hours)
    # Logs always show data based on selected period (up to 100 entries)
    log_hours = hours if hours else 720  # Default to 30 days max for logs in lifetime mode
    recent = stat_repo.get_recent(hours=log_hours, limit=100)
    recent_emails = email_stat_repo.get_recent(hours=log_hours, limit=100)
    recent_firewall = firewall_stat_repo.get_recent(hours=log_hours, limit=100)

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "summary": summary,
        "email_summary": email_summary,
        "firewall_summary": firewall_summary,
        "connections": recent,
        "email_connections": recent_emails,
        "firewall_entries": recent_firewall,
        "current_period": period,
        "active_page": "stats"
    })


# Ports never allowed on public interface (shown in firewall UI; agents enforce these)
DEFAULT_BLOCKED_PORTS = [
    {"port": 22, "name": "SSH"},
    {"port": 23, "name": "Telnet"},
    {"port": 3389, "name": "RDP"},
    {"port": 5900, "name": "VNC"},
    {"port": 5432, "name": "PostgreSQL"},
    {"port": 27017, "name": "MongoDB"},
    {"port": 6379, "name": "Redis"},
    {"port": 11211, "name": "Memcached"},
    {"port": 3306, "name": "MySQL"},
    {"port": 1433, "name": "MSSQL"},
]


@router.get("/firewall", response_class=HTMLResponse)
async def firewall_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Firewall rules management page (port rules and default blocked ports)."""
    firewall_repo = FirewallRuleRepository(db)
    agent_repo = AgentRepository(db)

    rules = firewall_repo.get_all()
    agents = agent_repo.get_all()

    return templates.TemplateResponse("firewall.html", {
        "request": request,
        "rules": rules,
        "agents": agents,
        "protocols": [p.value for p in Protocol],
        "actions": [a.value for a in FirewallAction],
        "default_blocked_ports": DEFAULT_BLOCKED_PORTS,
        "active_page": "firewall"
    })


@router.post("/firewall", response_class=HTMLResponse)
async def create_firewall_rule_htmx(
    request: Request,
    port: int = Form(...),
    protocol: str = Form("tcp"),
    interface: str = Form(...),
    action: str = Form("block"),
    description: str = Form(""),
    agent_id: str = Form(""),
    db: Session = Depends(get_db)
):
    """Create firewall rule via htmx form."""
    repo = FirewallRuleRepository(db)
    agent_repo = AgentRepository(db)

    # Parse agent_id (empty string = all agents)
    parsed_agent_id = int(agent_id) if agent_id else None

    # Validate agent if specified
    if parsed_agent_id:
        agent = agent_repo.get_by_id(parsed_agent_id)
        if not agent:
            return HTMLResponse(
                '<div class="text-red-500">Agent not found</div>',
                status_code=400
            )

    repo.create(
        port=port,
        protocol=Protocol(protocol),
        interface=interface,
        action=FirewallAction(action),
        description=description or None,
        enabled=True,
        agent_id=parsed_agent_id
    )

    # Return updated rules list
    rules = repo.get_all()
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/firewall_table.html", {
        "request": request,
        "rules": rules,
        "agents": agents
    })


@router.delete("/firewall/{rule_id}", response_class=HTMLResponse)
async def delete_firewall_rule_htmx(rule_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete firewall rule via htmx."""
    repo = FirewallRuleRepository(db)
    if not repo.delete(rule_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/firewall/{rule_id}/toggle", response_class=HTMLResponse)
async def toggle_firewall_rule_htmx(request: Request, rule_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Toggle firewall rule enabled status via htmx."""
    repo = FirewallRuleRepository(db)
    agent_repo = AgentRepository(db)

    rule = repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404)

    repo.update(rule_id, enabled=not rule.enabled)

    # Return updated rules list
    rules = repo.get_all()
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/firewall_table.html", {
        "request": request,
        "rules": rules,
        "agents": agents
    })


@router.post("/firewall/apply", response_class=HTMLResponse)
async def apply_firewall_rules_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Push config sync to all reachable agents (wireguard_ip or control_url)."""
    agent_repo = AgentRepository(db)
    agents = [a for a in agent_repo.get_all() if get_agent_base_url(a)]

    if not agents:
        return HTMLResponse(
            '<div class="text-yellow-500">No reachable agents to sync (set WireGuard IP or control_url)</div>',
            status_code=200
        )

    # Trigger sync on all agents in parallel
    results = {"success": 0, "failed": 0}

    async def trigger_agent_sync(agent):
        """Trigger sync on a single agent."""
        base = get_agent_base_url(agent)
        url = f"{base.rstrip('/')}/trigger-sync" if base else None
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=_agent_api_ssl_verify()) as client:
                response = await client.post(url)
                if response.status_code == 200:
                    logger.info(f"Triggered sync on agent {agent.hostname}")
                    return True
                else:
                    logger.warning(f"Failed to trigger sync on {agent.hostname}: {response.status_code}")
                    return False
        except Exception as e:
            logger.warning(f"Failed to reach agent {agent.hostname}: {e}")
            return False

    # Run all triggers in parallel
    tasks = [trigger_agent_sync(agent) for agent in agents]
    outcomes = await asyncio.gather(*tasks)

    results["success"] = sum(1 for o in outcomes if o)
    results["failed"] = sum(1 for o in outcomes if not o)

    if results["failed"] == 0:
        return HTMLResponse(
            f'<div class="text-green-500">Synced {results["success"]} agent(s)</div>'
        )
    elif results["success"] == 0:
        return HTMLResponse(
            f'<div class="text-red-500">Failed to sync all {results["failed"]} agent(s)</div>'
        )
    else:
        return HTMLResponse(
            f'<div class="text-yellow-500">Synced {results["success"]}, failed {results["failed"]} agent(s)</div>'
        )


@router.post("/firewall/test-port", response_class=HTMLResponse)
async def test_agent_port_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Test TCP connectivity to a port on an agent (verify firewall blocking). Returns HTML snippet."""
    form = await request.form()
    try:
        agent_id = int(form.get("agent_id", 0))
        port = int(form.get("port", 0))
    except (TypeError, ValueError):
        return HTMLResponse('<div class="text-red-500">Invalid agent or port.</div>')
    if port < 1 or port > 65535:
        return HTMLResponse('<div class="text-red-500">Port must be 1–65535.</div>')
    agent_repo = AgentRepository(db)
    agent = agent_repo.get_by_id(agent_id)
    if not agent:
        return HTMLResponse('<div class="text-red-500">Agent not found.</div>')
    host = get_agent_host(agent)
    if not host:
        return HTMLResponse(
            '<div class="text-amber-600">Agent has no WireGuard IP or control_url; cannot test.</div>'
        )
    try:
        await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
        return HTMLResponse(
            f'<div class="text-green-600">Port {port} on {agent.hostname} ({host}) is <strong>reachable</strong> (not blocked).</div>'
        )
    except asyncio.TimeoutError:
        return HTMLResponse(
            f'<div class="text-amber-600">Connection to {agent.hostname}:{port} timed out (port may be blocked or closed).</div>'
        )
    except ConnectionRefusedError:
        return HTMLResponse(
            f'<div class="text-green-600">Port {port} on {agent.hostname} is <strong>blocked/closed</strong> (connection refused).</div>'
        )
    except OSError as e:
        return HTMLResponse(f'<div class="text-red-500">Error: {e!s}</div>')


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Unified rules page combining services and assignments."""
    service_repo = ServiceRepository(db)
    assign_repo = ServiceAssignmentRepository(db)
    agent_repo = AgentRepository(db)

    assignments = assign_repo.get_all()
    agents = agent_repo.get_all()

    # Build combined rules view
    rules = []
    for assignment in assignments:
        rules.append({
            "assignment": assignment,
            "service": assignment.service
        })

    return templates.TemplateResponse("rules.html", {
        "request": request,
        "rules": rules,
        "agents": agents,
        "protocols": [p.value for p in Protocol],
        "active_page": "rules"
    })


@router.post("/rules", response_class=HTMLResponse)
async def create_rule_htmx(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    listen_port: int = Form(...),
    backend_host: str = Form(...),
    backend_port: int = Form(...),
    protocol: str = Form("tcp"),
    agent_id: str = Form(""),
    db: Session = Depends(get_db)
):
    """Create service and assignment in one step via htmx form."""
    service_repo = ServiceRepository(db)
    assign_repo = ServiceAssignmentRepository(db)
    agent_repo = AgentRepository(db)

    # Check for duplicate service name
    if service_repo.get_by_name(name):
        return HTMLResponse(
            '<div class="text-red-500">A rule with this name already exists</div>',
            status_code=400
        )

    # Check for listen port conflict
    existing_port = service_repo.get_by_listen_port(listen_port, Protocol(protocol))
    if existing_port:
        return HTMLResponse(
            f'<div class="text-red-500">Listen port {listen_port}/{protocol} already in use</div>',
            status_code=400
        )

    # Parse agent_id
    parsed_agent_id = int(agent_id) if agent_id else None

    # Validate agent if specified
    if parsed_agent_id:
        agent = agent_repo.get_by_id(parsed_agent_id)
        if not agent:
            return HTMLResponse(
                '<div class="text-red-500">Agent not found</div>',
                status_code=400
            )

    # Create service
    service = service_repo.create(
        name=name,
        description=description or None,
        listen_port=listen_port,
        backend_host=backend_host,
        backend_port=backend_port,
        protocol=Protocol(protocol)
    )

    # Create assignment
    assign_repo.create(
        service_id=service.id,
        agent_id=parsed_agent_id,
        enabled=True
    )

    # Return updated rules list
    assignments = assign_repo.get_all()
    rules = []
    for assignment in assignments:
        rules.append({
            "assignment": assignment,
            "service": assignment.service
        })

    return templates.TemplateResponse("partials/rules_table.html", {
        "request": request,
        "rules": rules
    })


@router.post("/rules/{assignment_id}/toggle", response_class=HTMLResponse)
async def toggle_rule_htmx(request: Request, assignment_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Toggle rule enabled status via htmx."""
    assign_repo = ServiceAssignmentRepository(db)

    assignment = assign_repo.get_by_id(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404)

    assign_repo.update(assignment_id, enabled=not assignment.enabled)

    # Return updated rules list
    assignments = assign_repo.get_all()
    rules = []
    for a in assignments:
        rules.append({
            "assignment": a,
            "service": a.service
        })

    return templates.TemplateResponse("partials/rules_table.html", {
        "request": request,
        "rules": rules
    })


@router.delete("/rules/{assignment_id}", response_class=HTMLResponse)
async def delete_rule_htmx(assignment_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete rule (assignment and service if no other assignments)."""
    assign_repo = ServiceAssignmentRepository(db)
    service_repo = ServiceRepository(db)

    assignment = assign_repo.get_by_id(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404)

    service_id = assignment.service_id

    # Delete the assignment
    assign_repo.delete(assignment_id)

    # Check if service has other assignments
    remaining = assign_repo.get_by_service(service_id)
    if not remaining:
        # No other assignments, delete the service too
        service_repo.delete(service_id)

    return HTMLResponse("")


@router.post("/rules/apply", response_class=HTMLResponse)
async def apply_rules_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Push config sync to all reachable agents (wireguard_ip or control_url)."""
    agent_repo = AgentRepository(db)
    agents = [a for a in agent_repo.get_all() if get_agent_base_url(a)]

    if not agents:
        return HTMLResponse(
            '<div class="text-yellow-500">No reachable agents to sync (set WireGuard IP or control_url)</div>',
            status_code=200
        )

    # Trigger sync on all agents in parallel
    async def trigger_agent_sync(agent):
        """Trigger sync on a single agent."""
        base = get_agent_base_url(agent)
        url = f"{base.rstrip('/')}/trigger-sync" if base else None
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=_agent_api_ssl_verify()) as client:
                response = await client.post(url)
                if response.status_code == 200:
                    logger.info(f"Triggered sync on agent {agent.hostname}")
                    return True
                else:
                    logger.warning(f"Failed to trigger sync on {agent.hostname}: {response.status_code}")
                    return False
        except Exception as e:
            logger.warning(f"Failed to reach agent {agent.hostname}: {e}")
            return False

    # Run all triggers in parallel
    tasks = [trigger_agent_sync(agent) for agent in agents]
    outcomes = await asyncio.gather(*tasks)

    success = sum(1 for o in outcomes if o)
    failed = sum(1 for o in outcomes if not o)

    if failed == 0:
        return HTMLResponse(
            f'<div class="text-green-500">Synced {success} agent(s)</div>'
        )
    elif success == 0:
        return HTMLResponse(
            f'<div class="text-red-500">Failed to sync all {failed} agent(s)</div>'
        )
    else:
        return HTMLResponse(
            f'<div class="text-yellow-500">Synced {success}, failed {failed} agent(s)</div>'
        )


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Alerts management page."""
    alert_repo = AlertRepository(db)
    agent_repo = AgentRepository(db)

    alerts = alert_repo.get_all(limit=100)
    counts = alert_repo.get_counts_by_severity()

    # Build alerts with agent hostnames
    alerts_with_agents = []
    for alert in alerts:
        agent_hostname = None
        if alert.agent_id:
            agent = agent_repo.get_by_id(alert.agent_id)
            if agent:
                agent_hostname = agent.hostname
        alerts_with_agents.append({
            "alert": alert,
            "agent_hostname": agent_hostname
        })

    return templates.TemplateResponse("alerts.html", {
        "request": request,
        "alerts": alerts_with_agents,
        "counts": counts,
        "total_unacked": sum(counts.values()),
        "severities": [s.value for s in AlertSeverity],
        "alert_types": [t.value for t in AlertType],
        "active_page": "alerts"
    })


@router.post("/alerts/{alert_id}/acknowledge", response_class=HTMLResponse)
async def acknowledge_alert_htmx(request: Request, alert_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Acknowledge an alert via htmx."""
    alert_repo = AlertRepository(db)
    agent_repo = AgentRepository(db)

    alert = alert_repo.acknowledge(alert_id)
    if not alert:
        raise HTTPException(status_code=404)

    # Return updated alerts list
    alerts = alert_repo.get_all(limit=100)
    alerts_with_agents = []
    for a in alerts:
        agent_hostname = None
        if a.agent_id:
            agent = agent_repo.get_by_id(a.agent_id)
            if agent:
                agent_hostname = agent.hostname
        alerts_with_agents.append({
            "alert": a,
            "agent_hostname": agent_hostname
        })

    return templates.TemplateResponse("partials/alerts_table.html", {
        "request": request,
        "alerts": alerts_with_agents
    })


@router.post("/alerts/acknowledge-all", response_class=HTMLResponse)
async def acknowledge_all_alerts_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Acknowledge all alerts via htmx."""
    alert_repo = AlertRepository(db)
    agent_repo = AgentRepository(db)

    alert_repo.acknowledge_all()

    # Return updated alerts list
    alerts = alert_repo.get_all(limit=100)
    alerts_with_agents = []
    for a in alerts:
        agent_hostname = None
        if a.agent_id:
            agent = agent_repo.get_by_id(a.agent_id)
            if agent:
                agent_hostname = agent.hostname
        alerts_with_agents.append({
            "alert": a,
            "agent_hostname": agent_hostname
        })

    return templates.TemplateResponse("partials/alerts_table.html", {
        "request": request,
        "alerts": alerts_with_agents
    })


@router.delete("/alerts/{alert_id}", response_class=HTMLResponse)
async def delete_alert_htmx(alert_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete alert via htmx."""
    repo = AlertRepository(db)
    if not repo.delete(alert_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/alerts/{alert_id}/block-ip", response_class=HTMLResponse)
async def block_ip_from_alert_htmx(request: Request, alert_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Block IP from alert and acknowledge the alert."""
    alert_repo = AlertRepository(db)
    blocklist_repo = BlocklistRepository(db)
    agent_repo = AgentRepository(db)

    alert = alert_repo.get_by_id(alert_id)
    if not alert:
        raise HTTPException(status_code=404)

    # Add IP to blocklist if not already blocked
    if not blocklist_repo.is_blocked(alert.source_ip):
        reason = f"Blocked from alert: {alert.alert_type.value}"
        blocklist_repo.add(alert.source_ip, reason)
        await trigger_sync_all_agents(db)

    # Acknowledge the alert
    alert_repo.acknowledge(alert_id)

    # Return updated alerts list
    alerts = alert_repo.get_all(limit=100)
    alerts_with_agents = []
    for a in alerts:
        agent_hostname = None
        if a.agent_id:
            agent = agent_repo.get_by_id(a.agent_id)
            if agent:
                agent_hostname = agent.hostname
        alerts_with_agents.append({
            "alert": a,
            "agent_hostname": agent_hostname
        })

    return templates.TemplateResponse("partials/alerts_table.html", {
        "request": request,
        "alerts": alerts_with_agents
    })


# HTMX partial endpoints for live updates
@router.get("/partials/agents-status", response_class=HTMLResponse)
async def agents_status_partial(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Partial for agent status updates."""
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/agents_status.html", {
        "request": request,
        "agents": agents
    })


@router.get("/partials/stats-summary", response_class=HTMLResponse)
async def stats_summary_partial(request: Request, period: str = "24h", _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Partial for stats summary updates."""
    stat_repo = ConnectionStatRepository(db)
    blocklist_repo = BlocklistRepository(db)

    # Parse period to hours (None for lifetime)
    period_map = {"24h": 24, "7d": 168, "30d": 720, "lifetime": None}
    hours = period_map.get(period, 24)

    summary = stat_repo.get_stats_summary(hours=hours)
    summary["auto_blocklist_count"] = blocklist_repo.get_auto_added_count(hours=hours)
    return templates.TemplateResponse("partials/stats_summary.html", {
        "request": request,
        "stats": summary
    })


# ============================================================================
# Email Proxy Routes
# ============================================================================

@router.get("/email", response_class=HTMLResponse)
async def email_page(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Email proxy management page."""
    config_repo = EmailConfigRepository(db)
    user_repo = EmailUserRepository(db)
    blocklist_repo = EmailBlocklistRepository(db)
    agent_repo = AgentRepository(db)

    config = config_repo.get_global()
    configs = config_repo.get_all()
    users = user_repo.get_all()
    blocklist = blocklist_repo.get_all()
    agents = agent_repo.get_all()

    # Build deployment status list
    deployments = []
    for c in configs:
        agent_hostname = None
        if c.agent_id:
            agent = agent_repo.get_by_id(c.agent_id)
            if agent:
                agent_hostname = agent.hostname
        deployments.append({
            "config_id": c.id,
            "agent_id": c.agent_id,
            "agent_hostname": agent_hostname,
            "mailcow_host": c.mailcow_host,
            "mailcow_port": c.mailcow_port,
            "deployment_status": c.deployment_status.value,
            "enabled": c.enabled
        })

    # Get SASL users and domains
    from controller.database.repositories import EmailSaslUserRepository, EmailDomainRepository
    sasl_repo = EmailSaslUserRepository(db)
    domain_repo = EmailDomainRepository(db)

    sasl_users = sasl_repo.get_all()
    domains = domain_repo.get_all()

    # Get cached Mailcow data
    manager = EmailManager(db)
    mailboxes = manager.get_cached_mailboxes()
    aliases = manager.get_cached_aliases()

    # If cache is empty and API is configured, trigger initial sync
    if not mailboxes and not aliases and config and config.mailcow_api_url:
        try:
            await manager.sync_all_mailcow_data()
            mailboxes = manager.get_cached_mailboxes()
            aliases = manager.get_cached_aliases()
        except Exception as e:
            logger.warning(f"Failed to sync Mailcow data on page load: {e}")

    return templates.TemplateResponse("email.html", {
        "request": request,
        "config": config,
        "deployments": deployments,
        "users": users,
        "blocklist": blocklist,
        "sasl_users": sasl_users,
        "domains": domains,
        "mailboxes": mailboxes,
        "aliases": aliases,
        "agents": agents,
        "active_page": "email"
    })


@router.post("/email/config", response_class=HTMLResponse)
async def save_email_config_htmx(
    request: Request,
    mailcow_host: str = Form(...),
    mailcow_port: int = Form(25),
    mailcow_api_url: str = Form(""),
    mailcow_api_key: str = Form(""),
    db: Session = Depends(get_db)
):
    """Save Mailcow configuration via htmx."""
    config_repo = EmailConfigRepository(db)

    # Check if global config exists
    existing = config_repo.get_global()

    if existing:
        # Update existing config
        config_repo.update(
            existing.id,
            mailcow_host=mailcow_host,
            mailcow_port=mailcow_port,
            mailcow_api_url=mailcow_api_url or None,
            mailcow_api_key=mailcow_api_key or None
        )
        return HTMLResponse('<div class="text-green-500">Configuration updated</div>')
    else:
        # Create new config
        config_repo.create(
            mailcow_host=mailcow_host,
            mailcow_port=mailcow_port,
            mailcow_api_url=mailcow_api_url or None,
            mailcow_api_key=mailcow_api_key or None,
            agent_id=None,  # Global config
            enabled=True
        )
        return HTMLResponse('<div class="text-green-500">Configuration saved</div>')


@router.post("/email/deploy", response_class=HTMLResponse)
async def deploy_email_htmx(
    request: Request,
    agent_ids: list = Form(default=[]),
    db: Session = Depends(get_db)
):
    """Deploy email proxy to selected agents via htmx."""
    if not agent_ids:
        return HTMLResponse(
            '<div class="text-red-500">Please select at least one agent</div>',
            status_code=400
        )

    config_repo = EmailConfigRepository(db)
    agent_repo = AgentRepository(db)

    # Ensure global config exists
    global_config = config_repo.get_global()
    if not global_config:
        return HTMLResponse(
            '<div class="text-red-500">Please save Mailcow configuration first</div>',
            status_code=400
        )

    # Create agent-specific configs and trigger deployment
    manager = EmailManager(db)
    results = {"success": [], "failed": [], "warnings": []}

    for agent_id in agent_ids:
        try:
            agent_id = int(agent_id)
            agent = agent_repo.get_by_id(agent_id)
            if not agent:
                continue

            # Check if agent-specific config exists
            existing = config_repo.get_for_agent(agent_id)
            if not existing or existing.agent_id is None:
                # Create agent-specific config from global
                config_repo.create(
                    mailcow_host=global_config.mailcow_host,
                    mailcow_port=global_config.mailcow_port,
                    mailcow_api_url=global_config.mailcow_api_url,
                    mailcow_api_key=global_config.mailcow_api_key,
                    agent_id=agent_id,
                    enabled=True
                )

            # Wait for deployment result to get actual error messages
            success, message = await manager.deploy_to_agent(agent_id)
            if success:
                results["success"].append(agent.hostname)
                # Check if there's a warning (e.g., SSL not configured)
                if message and "SSL" in message:
                    results["warnings"].append(message)
            else:
                results["failed"].append(f"{agent.hostname}: {message}")

        except (ValueError, TypeError) as e:
            continue

    # Build response based on results
    if results["success"] and not results["failed"]:
        ssl_warning = ""
        if results["warnings"]:
            ssl_warning = (
                '<br><span class="text-yellow-600">⚠️ SSL not configured. '
                'Run on each agent: apt install certbot && certbot certonly --standalone -d HOSTNAME && systemctl restart postfix</span>'
            )
        return HTMLResponse(
            f'<div class="text-green-500">Deployed to: {", ".join(results["success"])}{ssl_warning}</div>'
        )
    elif results["failed"] and not results["success"]:
        error_details = "; ".join(results["failed"])
        return HTMLResponse(
            f'<div class="text-red-500">Deployment failed: {error_details}</div>',
            status_code=200  # Return 200 so HTMX shows the error
        )
    elif results["success"] and results["failed"]:
        error_details = "; ".join(results["failed"])
        ssl_warning = ""
        if results["warnings"]:
            ssl_warning = ' ⚠️ SSL setup needed on deployed agents.'
        return HTMLResponse(
            f'<div class="text-yellow-500">Partial success - Deployed: {", ".join(results["success"])}. '
            f'Failed: {error_details}{ssl_warning}</div>'
        )
    else:
        return HTMLResponse(
            '<div class="text-red-500">No valid agents selected</div>',
            status_code=400
        )


@router.post("/email/users", response_class=HTMLResponse)
async def create_email_user_htmx(
    request: Request,
    email_address: str = Form(...),
    display_name: str = Form(""),
    agent_id: str = Form(""),
    create_mailcow_mailbox: str = Form(""),
    db: Session = Depends(get_db)
):
    """Create email user via htmx."""
    user_repo = EmailUserRepository(db)
    agent_repo = AgentRepository(db)
    manager = EmailManager(db)

    # Check if user already exists
    if user_repo.get_by_email(email_address):
        return HTMLResponse(
            '<div class="text-red-500">Email user already exists</div>',
            status_code=400
        )

    parsed_agent_id = int(agent_id) if agent_id else None
    should_create_mailbox = create_mailcow_mailbox == "true"

    mailcow_mailbox_id = None
    generated_password = None

    if should_create_mailbox:
        mailcow_mailbox_id, generated_password = await manager.create_mailcow_mailbox(
            email_address,
            display_name or None
        )

    user_repo.create(
        email_address=email_address,
        display_name=display_name or None,
        mailcow_mailbox_id=mailcow_mailbox_id,
        agent_id=parsed_agent_id,
        enabled=True
    )

    # Return updated users list
    users = user_repo.get_all()
    agents = agent_repo.get_all()

    response = templates.TemplateResponse("partials/email_users_table.html", {
        "request": request,
        "users": users,
        "agents": agents
    })

    if generated_password:
        # Use HX-Trigger to pass password to frontend via JSON event
        import json
        response.headers["HX-Trigger"] = json.dumps({
            "showPassword": {"password": generated_password, "email": email_address}
        })

    return response


@router.delete("/email/users/{user_id}", response_class=HTMLResponse)
async def delete_email_user_htmx(user_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete email user via htmx."""
    repo = EmailUserRepository(db)
    if not repo.delete(user_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/users/{user_id}/toggle", response_class=HTMLResponse)
async def toggle_email_user_htmx(request: Request, user_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Toggle email user enabled status via htmx."""
    user_repo = EmailUserRepository(db)
    agent_repo = AgentRepository(db)

    user = user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404)

    user_repo.update(user_id, enabled=not user.enabled)

    # Return updated users list
    users = user_repo.get_all()
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/email_users_table.html", {
        "request": request,
        "users": users,
        "agents": agents
    })


@router.post("/email/blocklist", response_class=HTMLResponse)
async def add_email_blocklist_htmx(
    request: Request,
    block_type: str = Form(...),
    value: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db)
):
    """Add entry to email blocklist via htmx."""
    repo = EmailBlocklistRepository(db)

    email_block_type = EmailBlocklistType(block_type)

    if repo.exists(email_block_type, value):
        return HTMLResponse(
            '<div class="text-red-500">Entry already exists in blocklist</div>',
            status_code=400
        )

    repo.add(email_block_type, value, reason or None)

    # Return updated blocklist
    blocklist = repo.get_all()
    return templates.TemplateResponse("partials/email_blocklist_table.html", {
        "request": request,
        "blocklist": blocklist
    })


@router.delete("/email/blocklist/{entry_id}", response_class=HTMLResponse)
async def remove_email_blocklist_htmx(entry_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Remove entry from email blocklist via htmx."""
    repo = EmailBlocklistRepository(db)
    if not repo.remove(entry_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/apply", response_class=HTMLResponse)
async def apply_email_config_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Push email config sync to all deployed agents."""
    manager = EmailManager(db)
    results = await manager.sync_all_agents()

    if results["failed"] == 0 and results["success"] > 0:
        return HTMLResponse(f'<div class="text-green-500">Synced {results["success"]} agent(s)</div>')
    elif results["success"] == 0 and results["failed"] > 0:
        return HTMLResponse(f'<div class="text-red-500">Failed to sync {results["failed"]} agent(s)</div>')
    elif results["success"] == 0 and results["failed"] == 0:
        return HTMLResponse('<div class="text-yellow-500">No deployed agents to sync</div>')
    else:
        return HTMLResponse(
            f'<div class="text-yellow-500">Synced {results["success"]}, failed {results["failed"]} agent(s)</div>'
        )


# =============================================================================
# SASL User Routes
# =============================================================================

@router.post("/email/sasl", response_class=HTMLResponse)
async def create_sasl_user_htmx(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    agent_id: str = Form(""),
    db: Session = Depends(get_db)
):
    """Create SASL user via htmx."""
    from controller.database.repositories import EmailSaslUserRepository
    sasl_repo = EmailSaslUserRepository(db)
    agent_repo = AgentRepository(db)
    manager = EmailManager(db)

    # Check if user already exists
    if sasl_repo.get_by_username(username):
        return HTMLResponse(
            '<div class="text-red-500">SASL user already exists</div>',
            status_code=400
        )

    parsed_agent_id = int(agent_id) if agent_id else None

    user, _ = manager.create_sasl_user(username, password, parsed_agent_id)

    # Return updated SASL users list
    sasl_users = sasl_repo.get_all()
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/email_sasl_table.html", {
        "request": request,
        "sasl_users": sasl_users,
        "agents": agents
    })


@router.delete("/email/sasl/{user_id}", response_class=HTMLResponse)
async def delete_sasl_user_htmx(user_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete SASL user via htmx."""
    manager = EmailManager(db)
    if not manager.delete_sasl_user(user_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/sasl/{user_id}/toggle", response_class=HTMLResponse)
async def toggle_sasl_user_htmx(request: Request, user_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Toggle SASL user enabled status via htmx."""
    from controller.database.repositories import EmailSaslUserRepository
    sasl_repo = EmailSaslUserRepository(db)
    agent_repo = AgentRepository(db)
    manager = EmailManager(db)

    user = manager.toggle_sasl_user(user_id)
    if not user:
        raise HTTPException(status_code=404)

    # Return updated SASL users list
    sasl_users = sasl_repo.get_all()
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/email_sasl_table.html", {
        "request": request,
        "sasl_users": sasl_users,
        "agents": agents
    })


@router.post("/email/sasl/{user_id}/reset", response_class=HTMLResponse)
async def reset_sasl_password_htmx(request: Request, user_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Reset SASL user password via htmx."""
    from controller.database.repositories import EmailSaslUserRepository
    manager = EmailManager(db)

    user, new_password = manager.reset_sasl_password(user_id)
    if not user:
        raise HTTPException(status_code=404)

    import json
    response = HTMLResponse(f'<div class="text-green-500">Password reset for {user.username}</div>')
    response.headers["HX-Trigger"] = json.dumps({
        "showPassword": {"password": new_password, "email": user.username}
    })
    return response


# =============================================================================
# Domain Routes
# =============================================================================

@router.post("/email/domains", response_class=HTMLResponse)
async def create_domain_htmx(
    request: Request,
    domain: str = Form(...),
    db: Session = Depends(get_db)
):
    """Create relay domain via htmx."""
    from controller.database.repositories import EmailDomainRepository
    domain_repo = EmailDomainRepository(db)
    manager = EmailManager(db)

    # Check if domain already exists
    if domain_repo.exists(domain):
        return HTMLResponse(
            '<div class="text-red-500">Domain already exists</div>',
            status_code=400
        )

    manager.create_domain(domain)

    # Return updated domains list
    domains = domain_repo.get_all()
    return templates.TemplateResponse("partials/email_domains_table.html", {
        "request": request,
        "domains": domains
    })


@router.delete("/email/domains/{domain_id}", response_class=HTMLResponse)
async def delete_domain_htmx(domain_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete relay domain via htmx."""
    manager = EmailManager(db)
    if not manager.delete_domain(domain_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/domains/{domain_id}/toggle", response_class=HTMLResponse)
async def toggle_domain_htmx(request: Request, domain_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Toggle domain enabled status via htmx."""
    from controller.database.repositories import EmailDomainRepository
    domain_repo = EmailDomainRepository(db)
    manager = EmailManager(db)

    domain = manager.toggle_domain(domain_id)
    if not domain:
        raise HTTPException(status_code=404)

    # Return updated domains list
    domains = domain_repo.get_all()
    return templates.TemplateResponse("partials/email_domains_table.html", {
        "request": request,
        "domains": domains
    })


@router.post("/email/domains/sync", response_class=HTMLResponse)
async def sync_mailcow_domains_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Sync domains from Mailcow via htmx."""
    from controller.database.repositories import EmailDomainRepository
    domain_repo = EmailDomainRepository(db)
    manager = EmailManager(db)

    count = await manager.sync_mailcow_domains()

    if count > 0:
        domains = domain_repo.get_all()
        response = templates.TemplateResponse("partials/email_domains_table.html", {
            "request": request,
            "domains": domains
        })
        return response
    else:
        return HTMLResponse('<div class="text-yellow-500">No domains found or Mailcow API not configured</div>')


# =============================================================================
# Mailcow Data Routes
# =============================================================================

@router.get("/email/mailcow/mailboxes", response_class=HTMLResponse)
async def get_mailcow_mailboxes_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Fetch, sync and display Mailcow mailboxes via htmx."""
    manager = EmailManager(db)
    # Sync from Mailcow (updates cache)
    await manager.sync_mailcow_mailboxes()
    # Return cached data
    mailboxes = manager.get_cached_mailboxes()

    return templates.TemplateResponse("partials/email_mailcow_mailboxes.html", {
        "request": request,
        "mailboxes": mailboxes
    })


@router.get("/email/mailcow/aliases", response_class=HTMLResponse)
async def get_mailcow_aliases_htmx(request: Request, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Fetch, sync and display Mailcow aliases via htmx."""
    manager = EmailManager(db)
    # Sync from Mailcow (updates cache)
    await manager.sync_mailcow_aliases()
    # Return cached data
    aliases = manager.get_cached_aliases()

    return templates.TemplateResponse("partials/email_mailcow_aliases.html", {
        "request": request,
        "aliases": aliases
    })


@router.post("/email/mailcow/aliases", response_class=HTMLResponse)
async def create_mailcow_alias_htmx(
    request: Request,
    address: str = Form(...),
    goto: str = Form(...),
    db: Session = Depends(get_db)
):
    """Create Mailcow alias via htmx."""
    manager = EmailManager(db)
    success, message = await manager.create_mailcow_alias(address, goto)

    if success:
        # Sync and return cached aliases
        await manager.sync_mailcow_aliases()
        aliases = manager.get_cached_aliases()
        return templates.TemplateResponse("partials/email_mailcow_aliases.html", {
            "request": request,
            "aliases": aliases
        })
    else:
        return HTMLResponse(f'<div class="text-red-500">{message}</div>', status_code=400)


@router.delete("/email/mailcow/aliases/{alias_id}", response_class=HTMLResponse)
async def delete_mailcow_alias_htmx(alias_id: int, _auth: None = Depends(_require_session), db: Session = Depends(get_db)):
    """Delete Mailcow alias via htmx."""
    manager = EmailManager(db)
    success, message = await manager.delete_mailcow_alias(alias_id)

    if not success:
        return HTMLResponse(f'<div class="text-red-500">{message}</div>', status_code=400)

    # Sync cache after delete
    await manager.sync_mailcow_aliases()
    return HTMLResponse("")
