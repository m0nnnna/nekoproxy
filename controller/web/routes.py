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
    ServiceAssignmentRepository,
    BlocklistRepository,
    ConnectionStatRepository,
    FirewallRuleRepository
)
from shared.models.common import Protocol, FirewallAction

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
async def delete_service_htmx(service_id: int, db: Session = Depends(get_db)):
    """Delete service via htmx."""
    repo = ServiceRepository(db)
    if not repo.delete(service_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.get("/assignments", response_class=HTMLResponse)
async def assignments_page(request: Request, db: Session = Depends(get_db)):
    """Service assignments management page."""
    assign_repo = ServiceAssignmentRepository(db)
    service_repo = ServiceRepository(db)
    agent_repo = AgentRepository(db)

    assignments = assign_repo.get_all()
    services = service_repo.get_all()
    agents = agent_repo.get_all()

    return templates.TemplateResponse("assignments.html", {
        "request": request,
        "assignments": assignments,
        "services": services,
        "agents": agents,
        "active_page": "assignments"
    })


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
async def delete_assignment_htmx(assignment_id: int, db: Session = Depends(get_db)):
    """Delete assignment via htmx."""
    repo = ServiceAssignmentRepository(db)
    if not repo.delete(assignment_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/assignments/{assignment_id}/toggle", response_class=HTMLResponse)
async def toggle_assignment_htmx(request: Request, assignment_id: int, db: Session = Depends(get_db)):
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


@router.get("/firewall", response_class=HTMLResponse)
async def firewall_page(request: Request, db: Session = Depends(get_db)):
    """Firewall rules management page."""
    repo = FirewallRuleRepository(db)
    rules = repo.get_all()

    return templates.TemplateResponse("firewall.html", {
        "request": request,
        "rules": rules,
        "protocols": [p.value for p in Protocol],
        "actions": [a.value for a in FirewallAction],
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
    db: Session = Depends(get_db)
):
    """Create firewall rule via htmx form."""
    repo = FirewallRuleRepository(db)

    # Check for duplicate
    existing = repo.get_by_port_interface(port, Protocol(protocol), interface)
    if existing:
        return HTMLResponse(
            f'<div class="text-red-500">Rule for port {port}/{protocol} on {interface} already exists</div>',
            status_code=400
        )

    repo.create(
        port=port,
        protocol=Protocol(protocol),
        interface=interface,
        action=FirewallAction(action),
        description=description or None,
        enabled=True
    )

    # Return updated rules list
    rules = repo.get_all()
    return templates.TemplateResponse("partials/firewall_table.html", {
        "request": request,
        "rules": rules
    })


@router.delete("/firewall/{rule_id}", response_class=HTMLResponse)
async def delete_firewall_rule_htmx(rule_id: int, db: Session = Depends(get_db)):
    """Delete firewall rule via htmx."""
    repo = FirewallRuleRepository(db)
    if not repo.delete(rule_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/firewall/{rule_id}/toggle", response_class=HTMLResponse)
async def toggle_firewall_rule_htmx(request: Request, rule_id: int, db: Session = Depends(get_db)):
    """Toggle firewall rule enabled status via htmx."""
    repo = FirewallRuleRepository(db)

    rule = repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404)

    repo.update(rule_id, enabled=not rule.enabled)

    # Return updated rules list
    rules = repo.get_all()
    return templates.TemplateResponse("partials/firewall_table.html", {
        "request": request,
        "rules": rules
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
