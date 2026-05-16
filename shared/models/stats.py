from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class ConnectionStats(BaseModel):
    """Single connection statistics."""
    service_id: Optional[int] = None
    client_ip: str
    status: str
    duration: Optional[float] = None
    bytes_sent: int = 0
    bytes_received: int = 0
    timestamp: datetime
    proxy_type: str = "incoming"   # "incoming" | "dns" | "forward"
    target: Optional[str] = None   # forward proxy destination (host:port)


class StatsReport(BaseModel):
    """Batch of connection stats from agent."""
    agent_id: int
    connections: List[ConnectionStats]


class FirewallRuleStat(BaseModel):
    """Single firewall rule stat entry (delta counters from iptables)."""
    port: int
    protocol: str          # "tcp" or "udp"
    interface: str         # resolved interface name like "eth0", "wg0"
    action: str            # "allow" or "block"
    packets: int           # delta packet count since last report
    bytes: int             # delta byte count since last report
    timestamp: datetime


class FirewallStatsReport(BaseModel):
    """Batch of firewall stats from agent."""
    agent_id: int
    rules: List[FirewallRuleStat]
