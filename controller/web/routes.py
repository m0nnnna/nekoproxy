"""Web dashboard routes using Jinja2 templates."""

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from controller.config import settings
from controller.database.database import get_db
from controller.database.repositories import (
    AgentRepository,
    ServiceRepository,
    ForwardingRuleRepository,
    BlocklistRepository,
    ConnectionStatRepository
)
from shared.models.common import Protocol

# Ensure templates directory exists
settings.templates_dir.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(settings.templates_dir))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Main dashboard page."""
    agent_repo = AgentRepository(db)
    stat_repo = ConnectionStatRepository(db)

    agents = agent_repo.get_all()
    stats_summary = stat_repo.get_stats_summary(hours=24)
    recent_connections = stat_repo.get_recent(hours=1, limit=10)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "agents": agents,
        "stats": stats_summary,
        "recent_connections": recent_connections,
        "active_page": "dashboard"
    })


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request, db: Session = Depends(get_db)):
    """Agents management page."""
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_all()

    return templates.TemplateResponse("agents.html", {
        "request": request,
        "agents": agents,
        "active_page": "agents"
    })


@router.delete("/agents/{agent_id}", response_class=HTMLResponse)
async def delete_agent_htmx(agent_id: int, db: Session = Depends(get_db)):
    """Delete agent via htmx."""
    repo = AgentRepository(db)
    if not repo.delete(agent_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.get("/services", response_class=HTMLResponse)
async def services_page(request: Request, db: Session = Depends(get_db)):
    """Services management page."""
    service_repo = ServiceRepository(db)
    services = service_repo.get_all()

    return templates.TemplateResponse("services.html", {
        "request": request,
        "services": services,
        "protocols": [p.value for p in Protocol],
        "active_page": "services"
    })


@router.post("/services", response_class=HTMLResponse)
async def create_service_htmx(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    default_backend_host: str = Form(...),
    default_backend_port: int = Form(...),
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

    repo.create(
        name=name,
        description=description or None,
        default_backend_host=default_backend_host,
        default_backend_port=default_backend_port,
        protocol=Protocol(protocol)
    )

    # Return updated services list
    services = repo.get_all()
    return templates.TemplateResponse("partials/services_table.html", {
        "request": request,
        "services": services
    })


@router.delete("/services/{service_id}", response_class=HTMLResponse)
async def delete_service_htmx(service_id: int, db: Session = Depends(get_db)):
    """Delete service via htmx."""
    repo = ServiceRepository(db)
    if not repo.delete(service_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, db: Session = Depends(get_db)):
    """Forwarding rules management page."""
    rule_repo = ForwardingRuleRepository(db)
    service_repo = ServiceRepository(db)

    rules = rule_repo.get_all()
    services = service_repo.get_all()

    return templates.TemplateResponse("rules.html", {
        "request": request,
        "rules": rules,
        "services": services,
        "protocols": [p.value for p in Protocol],
        "active_page": "rules"
    })


@router.post("/rules", response_class=HTMLResponse)
async def create_rule_htmx(
    request: Request,
    service_id: int = Form(...),
    listen_port: int = Form(...),
    backend_host: str = Form(""),
    backend_port: int = Form(None),
    protocol: str = Form("tcp"),
    db: Session = Depends(get_db)
):
    """Create forwarding rule via htmx form."""
    rule_repo = ForwardingRuleRepository(db)
    service_repo = ServiceRepository(db)

    # Validate service exists
    service = service_repo.get_by_id(service_id)
    if not service:
        return HTMLResponse(
            '<div class="text-red-500">Service not found</div>',
            status_code=400
        )

    # Check port conflict
    existing = rule_repo.get_by_port(listen_port, Protocol(protocol))
    if existing:
        return HTMLResponse(
            f'<div class="text-red-500">Port {listen_port}/{protocol} already in use</div>',
            status_code=400
        )

    rule_repo.create(
        service_id=service_id,
        listen_port=listen_port,
        backend_host=backend_host or None,
        backend_port=backend_port,
        protocol=Protocol(protocol),
        enabled=True
    )

    # Return updated rules list
    rules = rule_repo.get_all()
    services = service_repo.get_all()
    return templates.TemplateResponse("partials/rules_table.html", {
        "request": request,
        "rules": rules,
        "services": services
    })


@router.delete("/rules/{rule_id}", response_class=HTMLResponse)
async def delete_rule_htmx(rule_id: int, db: Session = Depends(get_db)):
    """Delete rule via htmx."""
    repo = ForwardingRuleRepository(db)
    if not repo.delete(rule_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/rules/{rule_id}/toggle", response_class=HTMLResponse)
async def toggle_rule_htmx(request: Request, rule_id: int, db: Session = Depends(get_db)):
    """Toggle rule enabled status via htmx."""
    rule_repo = ForwardingRuleRepository(db)
    service_repo = ServiceRepository(db)

    rule = rule_repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404)

    rule_repo.update(rule_id, enabled=not rule.enabled)

    # Return updated rules list
    rules = rule_repo.get_all()
    services = service_repo.get_all()
    return templates.TemplateResponse("partials/rules_table.html", {
        "request": request,
        "rules": rules,
        "services": services
    })


@router.get("/blocklist", response_class=HTMLResponse)
async def blocklist_page(request: Request, db: Session = Depends(get_db)):
    """Blocklist management page."""
    repo = BlocklistRepository(db)
    entries = repo.get_all()

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

    # Return updated blocklist
    entries = repo.get_all()
    return templates.TemplateResponse("partials/blocklist_table.html", {
        "request": request,
        "entries": entries
    })


@router.delete("/blocklist/{ip}", response_class=HTMLResponse)
async def remove_blocklist_htmx(ip: str, db: Session = Depends(get_db)):
    """Remove IP from blocklist via htmx."""
    repo = BlocklistRepository(db)
    if not repo.remove(ip):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, db: Session = Depends(get_db)):
    """Statistics page."""
    stat_repo = ConnectionStatRepository(db)

    summary = stat_repo.get_stats_summary(hours=24)
    recent = stat_repo.get_recent(hours=24, limit=100)

    return templates.TemplateResponse("stats.html", {
        "request": request,
        "summary": summary,
        "connections": recent,
        "active_page": "stats"
    })


# HTMX partial endpoints for live updates
@router.get("/partials/agents-status", response_class=HTMLResponse)
async def agents_status_partial(request: Request, db: Session = Depends(get_db)):
    """Partial for agent status updates."""
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_all()
    return templates.TemplateResponse("partials/agents_status.html", {
        "request": request,
        "agents": agents
    })


@router.get("/partials/stats-summary", response_class=HTMLResponse)
async def stats_summary_partial(request: Request, db: Session = Depends(get_db)):
    """Partial for stats summary updates."""
    stat_repo = ConnectionStatRepository(db)
    summary = stat_repo.get_stats_summary(hours=24)
    return templates.TemplateResponse("partials/stats_summary.html", {
        "request": request,
        "stats": summary
    })
