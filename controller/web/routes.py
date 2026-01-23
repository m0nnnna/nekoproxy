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
    AlertRepository,
    EmailConfigRepository,
    EmailUserRepository,
    EmailBlocklistRepository
)
from controller.core.email_manager import EmailManager
from shared.models.common import Protocol, FirewallAction, AlertSeverity, AlertType, EmailBlocklistType

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


# ============================================================================
# Email Proxy Routes
# ============================================================================

@router.get("/email", response_class=HTMLResponse)
async def email_page(request: Request, db: Session = Depends(get_db)):
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
    results = {"success": [], "failed": []}

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
            else:
                results["failed"].append(f"{agent.hostname}: {message}")

        except (ValueError, TypeError) as e:
            continue

    # Build response based on results
    if results["success"] and not results["failed"]:
        return HTMLResponse(
            f'<div class="text-green-500">Deployed to: {", ".join(results["success"])}</div>'
        )
    elif results["failed"] and not results["success"]:
        error_details = "; ".join(results["failed"])
        return HTMLResponse(
            f'<div class="text-red-500">Deployment failed: {error_details}</div>',
            status_code=200  # Return 200 so HTMX shows the error
        )
    elif results["success"] and results["failed"]:
        error_details = "; ".join(results["failed"])
        return HTMLResponse(
            f'<div class="text-yellow-500">Partial success - Deployed: {", ".join(results["success"])}. '
            f'Failed: {error_details}</div>'
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
async def delete_email_user_htmx(user_id: int, db: Session = Depends(get_db)):
    """Delete email user via htmx."""
    repo = EmailUserRepository(db)
    if not repo.delete(user_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/users/{user_id}/toggle", response_class=HTMLResponse)
async def toggle_email_user_htmx(request: Request, user_id: int, db: Session = Depends(get_db)):
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
async def remove_email_blocklist_htmx(entry_id: int, db: Session = Depends(get_db)):
    """Remove entry from email blocklist via htmx."""
    repo = EmailBlocklistRepository(db)
    if not repo.remove(entry_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/apply", response_class=HTMLResponse)
async def apply_email_config_htmx(request: Request, db: Session = Depends(get_db)):
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
async def delete_sasl_user_htmx(user_id: int, db: Session = Depends(get_db)):
    """Delete SASL user via htmx."""
    manager = EmailManager(db)
    if not manager.delete_sasl_user(user_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/sasl/{user_id}/toggle", response_class=HTMLResponse)
async def toggle_sasl_user_htmx(request: Request, user_id: int, db: Session = Depends(get_db)):
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
async def reset_sasl_password_htmx(request: Request, user_id: int, db: Session = Depends(get_db)):
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
async def delete_domain_htmx(domain_id: int, db: Session = Depends(get_db)):
    """Delete relay domain via htmx."""
    manager = EmailManager(db)
    if not manager.delete_domain(domain_id):
        raise HTTPException(status_code=404)
    return HTMLResponse("")


@router.post("/email/domains/{domain_id}/toggle", response_class=HTMLResponse)
async def toggle_domain_htmx(request: Request, domain_id: int, db: Session = Depends(get_db)):
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
async def sync_mailcow_domains_htmx(request: Request, db: Session = Depends(get_db)):
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
async def get_mailcow_mailboxes_htmx(request: Request, db: Session = Depends(get_db)):
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
async def get_mailcow_aliases_htmx(request: Request, db: Session = Depends(get_db)):
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
async def delete_mailcow_alias_htmx(alias_id: int, db: Session = Depends(get_db)):
    """Delete Mailcow alias via htmx."""
    manager = EmailManager(db)
    success, message = await manager.delete_mailcow_alias(alias_id)

    if not success:
        return HTMLResponse(f'<div class="text-red-500">{message}</div>', status_code=400)

    # Sync cache after delete
    await manager.sync_mailcow_aliases()
    return HTMLResponse("")
