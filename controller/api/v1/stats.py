from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from controller.database.database import get_db
from controller.database.repositories import ConnectionStatRepository, EmailStatRepository
from shared.models import StatsReport

router = APIRouter()


class StatsSummary(BaseModel):
    total_connections: int
    blocked_connections: int
    total_bytes_sent: int
    total_bytes_received: int
    period_hours: int


class EmailStatsSummary(BaseModel):
    total_emails: int
    blocked_emails: int
    delivered_emails: int
    deferred_emails: int
    bounced_emails: int
    email_bytes_sent: int
    email_bytes_received: int
    period_hours: int


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


@router.post("/connections")
def report_connections(report: StatsReport, db: Session = Depends(get_db)):
    """Receive connection statistics from an agent."""
    repo = ConnectionStatRepository(db)

    stats_data = []
    for conn in report.connections:
        # Ensure timestamp is a datetime object
        timestamp = conn.timestamp
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except (ValueError, TypeError):
                timestamp = datetime.utcnow()

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

    count = repo.add_batch(stats_data)
    return {"status": "accepted", "count": count}


@router.get("/summary", response_model=StatsSummary)
def get_stats_summary(hours: int = 24, db: Session = Depends(get_db)):
    """Get aggregated statistics for the specified period."""
    repo = ConnectionStatRepository(db)
    return repo.get_stats_summary(hours=hours)


@router.get("/recent", response_model=list[ConnectionStatResponse])
def get_recent_stats(hours: int = 24, limit: int = 100, db: Session = Depends(get_db)):
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
def get_agent_stats(agent_id: int, limit: int = 100, db: Session = Depends(get_db)):
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
def report_email_stats(report: EmailStatsReport, db: Session = Depends(get_db)):
    """Receive email statistics from an agent."""
    repo = EmailStatRepository(db)

    stats_data = []
    for email in report.emails:
        # Parse timestamp from ISO string to datetime
        timestamp = email.get("timestamp")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except (ValueError, TypeError):
                timestamp = datetime.utcnow()
        elif timestamp is None:
            timestamp = datetime.utcnow()

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
def get_email_stats_summary(hours: int = 24, db: Session = Depends(get_db)):
    """Get aggregated email statistics for the specified period."""
    repo = EmailStatRepository(db)
    return repo.get_stats_summary(hours=hours)


@router.get("/email/recent", response_model=list[EmailStatResponse])
def get_recent_email_stats(hours: int = 24, limit: int = 100, db: Session = Depends(get_db)):
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
def get_agent_email_stats(agent_id: int, limit: int = 100, db: Session = Depends(get_db)):
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
