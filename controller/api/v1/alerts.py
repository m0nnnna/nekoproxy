from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Optional

from controller.database.database import get_db
from controller.database.repositories import AlertRepository, AgentRepository
from controller.core.auth import require_api_token
from shared.models import AlertCreate, AlertResponse
from shared.models.common import AlertSeverity

router = APIRouter()

def _alert_create_auth(
    x_agent_token: Optional[str] = Header(default=None, alias="X-Agent-Token"),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Accept agent token or admin token for alert creation (agents report alerts)."""
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



@router.post("", response_model=AlertResponse, status_code=201)
def create_alert(alert: AlertCreate, db: Session = Depends(get_db), _auth: None = Depends(_alert_create_auth)):
    """Create a new alert (typically called by agents)."""
    repo = AlertRepository(db)

    created = repo.create(
        alert_type=alert.alert_type,
        severity=alert.severity,
        source_ip=alert.source_ip,
        port=alert.port,
        interface=alert.interface,
        description=alert.description,
        agent_id=alert.agent_id
    )

    agent_hostname = None
    if created.agent_id:
        agent_repo = AgentRepository(db)
        agent = agent_repo.get_by_id(created.agent_id)
        if agent:
            agent_hostname = agent.hostname

    return AlertResponse(
        id=created.id,
        alert_type=created.alert_type,
        severity=created.severity,
        source_ip=created.source_ip,
        port=created.port,
        interface=created.interface,
        description=created.description,
        agent_id=created.agent_id,
        agent_hostname=agent_hostname,
        acknowledged=created.acknowledged,
        created_at=created.created_at
    )


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    unacknowledged_only: bool = False,
    severity: AlertSeverity = None,
    source_ip: str = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_token),
):
    """List alerts with optional filters."""
    repo = AlertRepository(db)
    agent_repo = AgentRepository(db)

    if source_ip:
        alerts = repo.get_by_source_ip(source_ip, limit=limit)
    elif severity:
        alerts = repo.get_by_severity(severity, limit=limit)
    elif unacknowledged_only:
        alerts = repo.get_unacknowledged(limit=limit)
    else:
        alerts = repo.get_all(limit=limit)

    result = []
    for a in alerts:
        agent_hostname = None
        if a.agent_id:
            agent = agent_repo.get_by_id(a.agent_id)
            if agent:
                agent_hostname = agent.hostname

        result.append(AlertResponse(
            id=a.id,
            alert_type=a.alert_type,
            severity=a.severity,
            source_ip=a.source_ip,
            port=a.port,
            interface=a.interface,
            description=a.description,
            agent_id=a.agent_id,
            agent_hostname=agent_hostname,
            acknowledged=a.acknowledged,
            created_at=a.created_at
        ))

    return result


@router.get("/counts")
def get_alert_counts(db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get count of unacknowledged alerts by severity."""
    repo = AlertRepository(db)
    counts = repo.get_counts_by_severity()
    total = sum(counts.values())
    return {"counts": counts, "total": total}


@router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(alert_id: int, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get a specific alert."""
    repo = AlertRepository(db)
    agent_repo = AgentRepository(db)

    alert = repo.get_by_id(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    agent_hostname = None
    if alert.agent_id:
        agent = agent_repo.get_by_id(alert.agent_id)
        if agent:
            agent_hostname = agent.hostname

    return AlertResponse(
        id=alert.id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        source_ip=alert.source_ip,
        port=alert.port,
        interface=alert.interface,
        description=alert.description,
        agent_id=alert.agent_id,
        agent_hostname=agent_hostname,
        acknowledged=alert.acknowledged,
        created_at=alert.created_at
    )


@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Acknowledge an alert."""
    repo = AlertRepository(db)
    alert = repo.acknowledge(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "acknowledged", "alert_id": alert_id}


@router.post("/acknowledge-all")
def acknowledge_all_alerts(db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Acknowledge all unacknowledged alerts."""
    repo = AlertRepository(db)
    count = repo.acknowledge_all()
    return {"status": "acknowledged", "count": count}


@router.delete("/{alert_id}")
def delete_alert(alert_id: int, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Delete an alert."""
    repo = AlertRepository(db)
    if not repo.delete(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "deleted", "alert_id": alert_id}
