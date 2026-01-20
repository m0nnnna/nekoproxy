"""Web dashboard routes using Jinja2 templates."""

import httpx
import asyncio
import logging

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
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
    AlertRepository
)
from shared.models.common import Protocol, FirewallAction, AlertSeverity, AlertType

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
async def delete_service_htmx(service_id: int, db: Session = Depends(get_db)):
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


@router.post("/blocklist/apply", response_class=HTMLResponse)
async def apply_blocklist_htmx(request: Request, db: Session = Depends(get_db)):
    """Push config sync to all healthy agents."""
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_healthy()

    if not agents:
        return HTMLResponse(
            '<div class="text-yellow-500">No healthy agents to sync</div>',
            status_code=200
        )

    async def trigger_agent_sync(agent):
        url = f"http://{agent.wireguard_ip}:8002/trigger-sync"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
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
    """Firewall rules management page (includes port rules and blocklist)."""
    firewall_repo = FirewallRuleRepository(db)
    blocklist_repo = BlocklistRepository(db)
    agent_repo = AgentRepository(db)

    rules = firewall_repo.get_all()
    entries = blocklist_repo.get_all()
    agents = agent_repo.get_all()

    return templates.TemplateResponse("firewall.html", {
        "request": request,
        "rules": rules,
        "entries": entries,
        "agents": agents,
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
async def apply_firewall_rules_htmx(request: Request, db: Session = Depends(get_db)):
    """Push config sync to all healthy agents."""
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_healthy()

    if not agents:
        return HTMLResponse(
            '<div class="text-yellow-500">No healthy agents to sync</div>',
            status_code=200
        )

    # Trigger sync on all agents in parallel
    results = {"success": 0, "failed": 0}

    async def trigger_agent_sync(agent):
        """Trigger sync on a single agent."""
        url = f"http://{agent.wireguard_ip}:8002/trigger-sync"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
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


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, db: Session = Depends(get_db)):
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
async def toggle_rule_htmx(request: Request, assignment_id: int, db: Session = Depends(get_db)):
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
async def delete_rule_htmx(assignment_id: int, db: Session = Depends(get_db)):
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
async def apply_rules_htmx(request: Request, db: Session = Depends(get_db)):
    """Push config sync to all healthy agents (same as firewall apply)."""
    agent_repo = AgentRepository(db)
    agents = agent_repo.get_healthy()

    if not agents:
        return HTMLResponse(
            '<div class="text-yellow-500">No healthy agents to sync</div>',
            status_code=200
        )

    # Trigger sync on all agents in parallel
    async def trigger_agent_sync(agent):
        """Trigger sync on a single agent."""
        url = f"http://{agent.wireguard_ip}:8002/trigger-sync"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
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
async def alerts_page(request: Request, db: Session = Depends(get_db)):
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
async def acknowledge_alert_htmx(request: Request, alert_id: int, db: Session = Depends(get_db)):
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
async def acknowledge_all_alerts_htmx(request: Request, db: Session = Depends(get_db)):
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
async def delete_alert_htmx(alert_id: int, db: Session = Depends(get_db)):
    """Delete alert via htmx."""
    repo = AlertRepository(db)
    if not repo.delete(alert_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


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
