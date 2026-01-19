from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from .common import Protocol, FirewallAction


class FirewallRuleBase(BaseModel):
    port: int
    protocol: Protocol = Protocol.TCP
    interface: str  # "public", "wireguard", or specific interface name like "eth0", "wg0"
    action: FirewallAction = FirewallAction.BLOCK
    description: Optional[str] = None
    enabled: bool = True


class FirewallRuleCreate(FirewallRuleBase):
    pass


class FirewallRuleUpdate(BaseModel):
    port: Optional[int] = None
    protocol: Optional[Protocol] = None
    interface: Optional[str] = None
    action: Optional[FirewallAction] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


class FirewallRuleResponse(FirewallRuleBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
