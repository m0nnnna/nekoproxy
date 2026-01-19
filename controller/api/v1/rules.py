from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from controller.database.database import get_db
from controller.database.repositories import ForwardingRuleRepository, ServiceRepository
from shared.models import ForwardingRuleCreate, ForwardingRuleUpdate, ForwardingRuleResponse

router = APIRouter()


@router.post("", response_model=ForwardingRuleResponse, status_code=201)
def create_rule(rule: ForwardingRuleCreate, db: Session = Depends(get_db)):
    """Create a new forwarding rule."""
    rule_repo = ForwardingRuleRepository(db)
    service_repo = ServiceRepository(db)

    # Verify service exists
    service = service_repo.get_by_id(rule.service_id)
    if not service:
        raise HTTPException(status_code=400, detail="Service not found")

    # Check for port conflict
    existing = rule_repo.get_by_port(rule.listen_port, rule.protocol)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Port {rule.listen_port}/{rule.protocol.value} already in use"
        )

    created = rule_repo.create(
        service_id=rule.service_id,
        listen_port=rule.listen_port,
        backend_host=rule.backend_host,
        backend_port=rule.backend_port,
        protocol=rule.protocol,
        enabled=rule.enabled
    )

    return ForwardingRuleResponse(
        id=created.id,
        service_id=created.service_id,
        listen_port=created.listen_port,
        backend_host=created.backend_host,
        backend_port=created.backend_port,
        protocol=created.protocol,
        enabled=created.enabled,
        resolved_backend_host=created.resolved_backend_host,
        resolved_backend_port=created.resolved_backend_port,
        service_name=service.name,
        created_at=created.created_at,
        updated_at=created.updated_at
    )


@router.get("", response_model=list[ForwardingRuleResponse])
def list_rules(enabled_only: bool = False, db: Session = Depends(get_db)):
    """List all forwarding rules."""
    repo = ForwardingRuleRepository(db)
    rules = repo.get_enabled() if enabled_only else repo.get_all()

    return [
        ForwardingRuleResponse(
            id=r.id,
            service_id=r.service_id,
            listen_port=r.listen_port,
            backend_host=r.backend_host,
            backend_port=r.backend_port,
            protocol=r.protocol,
            enabled=r.enabled,
            resolved_backend_host=r.resolved_backend_host,
            resolved_backend_port=r.resolved_backend_port,
            service_name=r.service.name,
            created_at=r.created_at,
            updated_at=r.updated_at
        )
        for r in rules
    ]


@router.get("/{rule_id}", response_model=ForwardingRuleResponse)
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    """Get a specific forwarding rule."""
    repo = ForwardingRuleRepository(db)
    rule = repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    return ForwardingRuleResponse(
        id=rule.id,
        service_id=rule.service_id,
        listen_port=rule.listen_port,
        backend_host=rule.backend_host,
        backend_port=rule.backend_port,
        protocol=rule.protocol,
        enabled=rule.enabled,
        resolved_backend_host=rule.resolved_backend_host,
        resolved_backend_port=rule.resolved_backend_port,
        service_name=rule.service.name,
        created_at=rule.created_at,
        updated_at=rule.updated_at
    )


@router.put("/{rule_id}", response_model=ForwardingRuleResponse)
def update_rule(rule_id: int, rule_update: ForwardingRuleUpdate, db: Session = Depends(get_db)):
    """Update a forwarding rule."""
    rule_repo = ForwardingRuleRepository(db)
    service_repo = ServiceRepository(db)

    existing = rule_repo.get_by_id(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Verify service exists if updating service_id
    if rule_update.service_id:
        service = service_repo.get_by_id(rule_update.service_id)
        if not service:
            raise HTTPException(status_code=400, detail="Service not found")

    # Check for port conflict if updating port
    if rule_update.listen_port or rule_update.protocol:
        new_port = rule_update.listen_port or existing.listen_port
        new_protocol = rule_update.protocol or existing.protocol
        conflict = rule_repo.get_by_port(new_port, new_protocol)
        if conflict and conflict.id != rule_id:
            raise HTTPException(
                status_code=400,
                detail=f"Port {new_port}/{new_protocol.value} already in use"
            )

    rule = rule_repo.update(rule_id, **rule_update.model_dump(exclude_unset=True))

    return ForwardingRuleResponse(
        id=rule.id,
        service_id=rule.service_id,
        listen_port=rule.listen_port,
        backend_host=rule.backend_host,
        backend_port=rule.backend_port,
        protocol=rule.protocol,
        enabled=rule.enabled,
        resolved_backend_host=rule.resolved_backend_host,
        resolved_backend_port=rule.resolved_backend_port,
        service_name=rule.service.name,
        created_at=rule.created_at,
        updated_at=rule.updated_at
    )


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    """Delete a forwarding rule."""
    repo = ForwardingRuleRepository(db)
    if not repo.delete(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/{rule_id}/enable")
def enable_rule(rule_id: int, db: Session = Depends(get_db)):
    """Enable a forwarding rule."""
    repo = ForwardingRuleRepository(db)
    rule = repo.update(rule_id, enabled=True)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "enabled", "rule_id": rule_id}


@router.post("/{rule_id}/disable")
def disable_rule(rule_id: int, db: Session = Depends(get_db)):
    """Disable a forwarding rule."""
    repo = ForwardingRuleRepository(db)
    rule = repo.update(rule_id, enabled=False)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "disabled", "rule_id": rule_id}
