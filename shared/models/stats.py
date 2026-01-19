from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class ConnectionStats(BaseModel):
    """Single connection statistics."""
    service_id: int
    client_ip: str
    status: str  # "connected", "disconnected", "blocked"
    duration: Optional[float] = None  # seconds
    bytes_sent: int = 0
    bytes_received: int = 0
    timestamp: datetime


class StatsReport(BaseModel):
    """Batch of connection stats from agent."""
    agent_id: int
    connections: List[ConnectionStats]
