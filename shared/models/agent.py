from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from .common import HealthStatus
from .service import ServiceResponse
from .firewall import FirewallRuleResponse
from .email import AgentEmailConfig


class AgentRegistration(BaseModel):
    """Sent by agent when registering with controller."""
    hostname: str
    wireguard_ip: str
    public_ip: Optional[str] = None
    version: str = "2.0.0"


class AgentHeartbeat(BaseModel):
    """Sent by agent every 30 seconds."""
    active_connections: int = 0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    bytes_sent: int = 0
    bytes_received: int = 0


class AgentConfig(BaseModel):
    """Configuration sent from controller to agent."""
    agent_id: int
    config_version: int = 1
    services: List[ServiceResponse] = Field(default_factory=list)  # Services assigned to this agent
    blocklist: List[str] = Field(default_factory=list)
    firewall_rules: List[FirewallRuleResponse] = Field(default_factory=list)  # Firewall rules for this agent
    email_config: Optional[AgentEmailConfig] = None  # Email proxy configuration
    heartbeat_interval: int = 30


class AgentStatus(BaseModel):
    """Agent status as tracked by controller."""
    id: int
    hostname: str
    wireguard_ip: str
    public_ip: Optional[str] = None
    status: HealthStatus = HealthStatus.UNKNOWN
    last_heartbeat: Optional[datetime] = None
    active_connections: int = 0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    version: str = "2.0.0"
    created_at: datetime

    class Config:
        from_attributes = True
