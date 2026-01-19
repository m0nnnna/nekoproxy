from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from controller.database.database import get_db
from controller.database.repositories import FirewallRuleRepository
from shared.models import FirewallRuleCreate, FirewallRuleUpdate, FirewallRuleResponse

router = APIRouter()


@router.post("", response_model=FirewallRuleResponse, status_code=201)
def create_firewall_rule(rule: FirewallRuleCreate, db: Session = Depends(get_db)):
    """Create a new firewall rule."""
    repo = FirewallRuleRepository(db)

    # Check for duplicate rule (same port/protocol/interface)
    existing = repo.get_by_port_interface(rule.port, rule.protocol, rule.interface)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Firewall rule for port {rule.port}/{rule.protocol.value} on {rule.interface} already exists"
        )

    created = repo.create(
        port=rule.port,
        protocol=rule.protocol,
        interface=rule.interface,
        action=rule.action,
        description=rule.description,
        enabled=rule.enabled
    )

    return FirewallRuleResponse(
        id=created.id,
        port=created.port,
        protocol=created.protocol,
        interface=created.interface,
        action=created.action,
        description=created.description,
        enabled=created.enabled,
        created_at=created.created_at,
        updated_at=created.updated_at
    )


@router.get("", response_model=list[FirewallRuleResponse])
def list_firewall_rules(enabled_only: bool = False, interface: str = None, db: Session = Depends(get_db)):
    """List all firewall rules."""
    repo = FirewallRuleRepository(db)

    if interface:
        rules = repo.get_by_interface(interface)
    elif enabled_only:
        rules = repo.get_enabled()
    else:
        rules = repo.get_all()

    return [
        FirewallRuleResponse(
            id=r.id,
            port=r.port,
            protocol=r.protocol,
            interface=r.interface,
            action=r.action,
            description=r.description,
            enabled=r.enabled,
            created_at=r.created_at,
            updated_at=r.updated_at
        )
        for r in rules
    ]


@router.get("/{rule_id}", response_model=FirewallRuleResponse)
def get_firewall_rule(rule_id: int, db: Session = Depends(get_db)):
    """Get a specific firewall rule."""
    repo = FirewallRuleRepository(db)
    rule = repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Firewall rule not found")

    return FirewallRuleResponse(
        id=rule.id,
        port=rule.port,
        protocol=rule.protocol,
        interface=rule.interface,
        action=rule.action,
        description=rule.description,
        enabled=rule.enabled,
        created_at=rule.created_at,
        updated_at=rule.updated_at
    )


@router.put("/{rule_id}", response_model=FirewallRuleResponse)
def update_firewall_rule(rule_id: int, rule_update: FirewallRuleUpdate, db: Session = Depends(get_db)):
    """Update a firewall rule."""
    repo = FirewallRuleRepository(db)

    existing = repo.get_by_id(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Firewall rule not found")

    # Check for conflict if updating port, protocol, or interface
    if rule_update.port is not None or rule_update.protocol is not None or rule_update.interface is not None:
        new_port = rule_update.port if rule_update.port is not None else existing.port
        new_protocol = rule_update.protocol if rule_update.protocol is not None else existing.protocol
        new_interface = rule_update.interface if rule_update.interface is not None else existing.interface

        conflict = repo.get_by_port_interface(new_port, new_protocol, new_interface)
        if conflict and conflict.id != rule_id:
            raise HTTPException(
                status_code=400,
                detail=f"Firewall rule for port {new_port}/{new_protocol.value} on {new_interface} already exists"
            )

    rule = repo.update(rule_id, **rule_update.model_dump(exclude_unset=True))

    return FirewallRuleResponse(
        id=rule.id,
        port=rule.port,
        protocol=rule.protocol,
        interface=rule.interface,
        action=rule.action,
        description=rule.description,
        enabled=rule.enabled,
        created_at=rule.created_at,
        updated_at=rule.updated_at
    )


@router.delete("/{rule_id}")
def delete_firewall_rule(rule_id: int, db: Session = Depends(get_db)):
    """Delete a firewall rule."""
    repo = FirewallRuleRepository(db)
    if not repo.delete(rule_id):
        raise HTTPException(status_code=404, detail="Firewall rule not found")
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/{rule_id}/enable")
def enable_firewall_rule(rule_id: int, db: Session = Depends(get_db)):
    """Enable a firewall rule."""
    repo = FirewallRuleRepository(db)
    rule = repo.update(rule_id, enabled=True)
    if not rule:
        raise HTTPException(status_code=404, detail="Firewall rule not found")
    return {"status": "enabled", "rule_id": rule_id}


@router.post("/{rule_id}/disable")
def disable_firewall_rule(rule_id: int, db: Session = Depends(get_db)):
    """Disable a firewall rule."""
    repo = FirewallRuleRepository(db)
    rule = repo.update(rule_id, enabled=False)
    if not rule:
        raise HTTPException(status_code=404, detail="Firewall rule not found")
    return {"status": "disabled", "rule_id": rule_id}
