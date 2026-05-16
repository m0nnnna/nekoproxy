from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from controller.database.database import get_db
from controller.database.repositories import ConnectionStatRepository, EmailStatRepository, FirewallStatRepository
from controller.core.auth import require_api_token, require_agent_token
from controller.core.live_events import live_events
from shared.models import StatsReport

router = APIRouter()


def _any_agent_auth(
    agent_id: int = 0,
    x_agent_token: Optional[str] = Header(default=None, alias="X-Agent-Token"),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Accept either an agent token (matching any registered agent) or admin token.
    Used for batch report endpoints where agent_id is in the body, not the path.
    """
    from controller.database.repositories import AgentRepository
    import hmac
    from controller.core.auth import _get_active_api_token

    # Try admin token first
    admin_token = x_api_token
    if not admin_token and authorization and authorization.lower().startswith("bearer "):
        admin_token = authorization[7:].strip()
    active_admin = _get_active_api_token(db)
    if active_admin and admin_token and hmac.compare_digest(admin_token, active_admin):
        return

    # Try per-agent token against all agents (since agent_id is in body, not path)
    if x_agent_token:
        from controller.database.models import Agent
        from fastapi import HTTPException
        agents = db.query(Agent).filter(Agent.agent_token == x_agent_token).first()
        if agents:
            return

    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail="Invalid or missing token")


class StatsSummary(BaseModel):
    total_connections: int
    blocked_connections: int
    total_bytes_sent: int
    total_bytes_received: int
    period_hours: Optional[int]  # None for lifetime


class EmailStatsSummary(BaseModel):
    total_emails: int
    blocked_emails: int
    delivered_emails: int
    deferred_emails: int
    bounced_emails: int
    email_bytes_sent: int
    email_bytes_received: int
    period_hours: Optional[int]  # None for lifetime


class ConnectionStatResponse(BaseModel):
    id: int
    agent_id: int
    service_id: int | None
    client_ip: str
    status: str
    duration: float | None
    bytes_sent: int
    bytes_received: int
    timestamp: str


class EmailStatResponse(BaseModel):
    id: int
    agent_id: int
    client_ip: str
    sender: Optional[str]
    recipient: Optional[str]
    status: str
    bytes_sent: int
    bytes_received: int
    message_id: Optional[str]
    timestamp: str


class EmailStatsReport(BaseModel):
    agent_id: int
    emails: list[dict]


class FirewallStatsSummary(BaseModel):
    total_firewall_packets: int
    total_firewall_bytes: int
    blocked_packets: int
    blocked_bytes: int
    allowed_packets: int
    allowed_bytes: int
    period_hours: Optional[int]


class FirewallStatResponse(BaseModel):
    id: int
    agent_id: int
    port: int
    protocol: str
    interface: str
    action: str
    packets: int
    bytes_count: int
    timestamp: str


class FirewallStatsReportRequest(BaseModel):
    agent_id: int
    rules: list[dict]


@router.post("/connections")
def report_connections(report: StatsReport, db: Session = Depends(get_db), _auth: None = Depends(_any_agent_auth)):
    """Receive connection statistics from an agent."""
    repo = ConnectionStatRepository(db)

    # Pre-load agent name and service names for live event labelling
    agent_name = str(report.agent_id)
    try:
        from controller.database.models import Agent as _Agent
        _agent = db.query(_Agent).filter(_Agent.id == report.agent_id).first()
        if _agent:
            agent_name = _agent.hostname
    except Exception:
        pass

    service_ids = {c.service_id for c in report.connections if c.service_id}
    service_names: dict = {}
    if service_ids:
        try:
            from controller.database.models import Service as _Service
            for svc in db.query(_Service).filter(_Service.id.in_(service_ids)).all():
                service_names[svc.id] = svc.name
        except Exception:
            pass

    stats_data = []
    for conn in report.connections:
        timestamp = conn.timestamp
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except (ValueError, TypeError):
                timestamp = datetime.utcnow()

        proxy_type = getattr(conn, "proxy_type", "incoming") or "incoming"
        target = getattr(conn, "target", None)

        # Push every connection type to the live event bus
        live_events.push(proxy_type, {
            "time": timestamp.strftime("%H:%M:%S"),
            "agent": agent_name,
            "agent_id": report.agent_id,
            "client_ip": conn.client_ip,
            "service": service_names.get(conn.service_id) if conn.service_id else None,
            "status": conn.status,
            "duration": round(conn.duration, 2) if conn.duration is not None else None,
            "bytes_sent": conn.bytes_sent,
            "bytes_received": conn.bytes_received,
            "target": target,
        })

        # Only persist incoming (TCP/UDP service) connections to DB
        if proxy_type == "incoming":
            stats_data.append({
                "agent_id": report.agent_id,
                "service_id": conn.service_id,
                "client_ip": conn.client_ip,
                "status": conn.status,
                "duration": conn.duration,
                "bytes_sent": conn.bytes_sent,
                "bytes_received": conn.bytes_received,
                "timestamp": timestamp
            })

    count = repo.add_batch(stats_data) if stats_data else 0
    return {"status": "accepted", "count": count}


@router.get("/summary", response_model=StatsSummary)
def get_stats_summary(hours: Optional[int] = 24, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get aggregated statistics for the specified period. Use hours=0 for lifetime."""
    repo = ConnectionStatRepository(db)
    # Treat 0 as lifetime (None)
    if hours == 0:
        hours = None
    return repo.get_stats_summary(hours=hours)


@router.get("/recent", response_model=list[ConnectionStatResponse])
def get_recent_stats(hours: int = 24, limit: int = 100, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get recent connection statistics."""
    repo = ConnectionStatRepository(db)
    stats = repo.get_recent(hours=hours, limit=limit)

    return [
        ConnectionStatResponse(
            id=s.id,
            agent_id=s.agent_id,
            service_id=s.service_id,
            client_ip=s.client_ip,
            status=s.status,
            duration=s.duration,
            bytes_sent=s.bytes_sent,
            bytes_received=s.bytes_received,
            timestamp=s.timestamp.isoformat()
        )
        for s in stats
    ]


@router.get("/agent/{agent_id}", response_model=list[ConnectionStatResponse])
def get_agent_stats(agent_id: int, limit: int = 100, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get connection statistics for a specific agent."""
    repo = ConnectionStatRepository(db)
    stats = repo.get_by_agent(agent_id, limit=limit)

    return [
        ConnectionStatResponse(
            id=s.id,
            agent_id=s.agent_id,
            service_id=s.service_id,
            client_ip=s.client_ip,
            status=s.status,
            duration=s.duration,
            bytes_sent=s.bytes_sent,
            bytes_received=s.bytes_received,
            timestamp=s.timestamp.isoformat()
        )
        for s in stats
    ]


# Email Stats Endpoints

@router.post("/email")
def report_email_stats(report: EmailStatsReport, db: Session = Depends(get_db), _auth: None = Depends(_any_agent_auth)):
    """Receive email statistics from an agent."""
    repo = EmailStatRepository(db)

    agent_name = str(report.agent_id)
    try:
        from controller.database.models import Agent as _Agent
        _agent = db.query(_Agent).filter(_Agent.id == report.agent_id).first()
        if _agent:
            agent_name = _agent.hostname
    except Exception:
        pass

    stats_data = []
    for email in report.emails:
        timestamp = email.get("timestamp")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except (ValueError, TypeError):
                timestamp = datetime.utcnow()
        elif timestamp is None:
            timestamp = datetime.utcnow()

        live_events.push("email", {
            "time": timestamp.strftime("%H:%M:%S"),
            "agent": agent_name,
            "agent_id": report.agent_id,
            "client_ip": email.get("client_ip", "unknown"),
            "sender": email.get("sender") or "",
            "recipient": email.get("recipient") or "",
            "status": email.get("status", "unknown"),
            "bytes": email.get("bytes_sent", 0),
        })

        stats_data.append({
            "agent_id": report.agent_id,
            "client_ip": email.get("client_ip", "unknown"),
            "sender": email.get("sender"),
            "recipient": email.get("recipient"),
            "status": email.get("status", "unknown"),
            "bytes_sent": email.get("bytes_sent", 0),
            "bytes_received": email.get("bytes_received", 0),
            "message_id": email.get("message_id"),
            "timestamp": timestamp
        })

    count = repo.add_batch(stats_data)
    return {"status": "accepted", "count": count}


@router.get("/email/summary", response_model=EmailStatsSummary)
def get_email_stats_summary(hours: Optional[int] = 24, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get aggregated email statistics for the specified period. Use hours=0 for lifetime."""
    repo = EmailStatRepository(db)
    # Treat 0 as lifetime (None)
    if hours == 0:
        hours = None
    return repo.get_stats_summary(hours=hours)


@router.get("/email/recent", response_model=list[EmailStatResponse])
def get_recent_email_stats(hours: int = 24, limit: int = 100, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get recent email statistics."""
    repo = EmailStatRepository(db)
    stats = repo.get_recent(hours=hours, limit=limit)

    return [
        EmailStatResponse(
            id=s.id,
            agent_id=s.agent_id,
            client_ip=s.client_ip,
            sender=s.sender,
            recipient=s.recipient,
            status=s.status,
            bytes_sent=s.bytes_sent,
            bytes_received=s.bytes_received,
            message_id=s.message_id,
            timestamp=s.timestamp.isoformat()
        )
        for s in stats
    ]


@router.get("/email/agent/{agent_id}", response_model=list[EmailStatResponse])
def get_agent_email_stats(agent_id: int, limit: int = 100, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get email statistics for a specific agent."""
    repo = EmailStatRepository(db)
    stats = repo.get_by_agent(agent_id, limit=limit)

    return [
        EmailStatResponse(
            id=s.id,
            agent_id=s.agent_id,
            client_ip=s.client_ip,
            sender=s.sender,
            recipient=s.recipient,
            status=s.status,
            bytes_sent=s.bytes_sent,
            bytes_received=s.bytes_received,
            message_id=s.message_id,
            timestamp=s.timestamp.isoformat()
        )
        for s in stats
    ]


# Firewall Stats Endpoints

@router.post("/firewall")
def report_firewall_stats(report: FirewallStatsReportRequest, db: Session = Depends(get_db), _auth: None = Depends(_any_agent_auth)):
    """Receive firewall (iptables) statistics from an agent."""
    repo = FirewallStatRepository(db)

    stats_data = []
    for rule in report.rules:
        timestamp = rule.get("timestamp")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except (ValueError, TypeError):
                timestamp = datetime.utcnow()
        elif timestamp is None:
            timestamp = datetime.utcnow()

        stats_data.append({
            "agent_id": report.agent_id,
            "port": rule.get("port", 0),
            "protocol": rule.get("protocol", "tcp"),
            "interface": rule.get("interface", "unknown"),
            "action": rule.get("action", "block"),
            "packets": rule.get("packets", 0),
            "bytes_count": rule.get("bytes", 0),
            "timestamp": timestamp
        })

    count = repo.add_batch(stats_data)
    return {"status": "accepted", "count": count}


@router.get("/firewall/summary", response_model=FirewallStatsSummary)
def get_firewall_stats_summary(hours: Optional[int] = 24, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get aggregated firewall statistics. Use hours=0 for lifetime."""
    repo = FirewallStatRepository(db)
    if hours == 0:
        hours = None
    return repo.get_stats_summary(hours=hours)


@router.get("/firewall/recent", response_model=list[FirewallStatResponse])
def get_recent_firewall_stats(hours: int = 24, limit: int = 100, db: Session = Depends(get_db), _auth: None = Depends(require_api_token)):
    """Get recent firewall statistics."""
    repo = FirewallStatRepository(db)
    stats = repo.get_recent(hours=hours, limit=limit)

    return [
        FirewallStatResponse(
            id=s.id,
            agent_id=s.agent_id,
            port=s.port,
            protocol=s.protocol,
            interface=s.interface,
            action=s.action,
            packets=s.packets,
            bytes_count=s.bytes_count,
            timestamp=s.timestamp.isoformat()
        )
        for s in stats
    ]
