from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from controller.database.database import get_db
from controller.database.repositories import ConnectionStatRepository
from shared.models import StatsReport

router = APIRouter()


class StatsSummary(BaseModel):
    total_connections: int
    blocked_connections: int
    total_bytes_sent: int
    total_bytes_received: int
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


@router.post("/connections")
def report_connections(report: StatsReport, db: Session = Depends(get_db)):
    """Receive connection statistics from an agent."""
    repo = ConnectionStatRepository(db)

    stats_data = [
        {
            "agent_id": report.agent_id,
            "service_id": conn.service_id,
            "client_ip": conn.client_ip,
            "status": conn.status,
            "duration": conn.duration,
            "bytes_sent": conn.bytes_sent,
            "bytes_received": conn.bytes_received,
            "timestamp": conn.timestamp
        }
        for conn in report.connections
    ]

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
