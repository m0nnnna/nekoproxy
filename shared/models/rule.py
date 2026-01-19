from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from .common import Protocol


class ForwardingRuleBase(BaseModel):
    service_id: int
    listen_port: int
    backend_host: Optional[str] = None  # Override service default
    backend_port: Optional[int] = None  # Override service default
    protocol: Protocol = Protocol.TCP
    enabled: bool = True


class ForwardingRuleCreate(ForwardingRuleBase):
    pass


class ForwardingRuleUpdate(BaseModel):
    service_id: Optional[int] = None
    listen_port: Optional[int] = None
    backend_host: Optional[str] = None
    backend_port: Optional[int] = None
    protocol: Optional[Protocol] = None
    enabled: Optional[bool] = None


class ForwardingRuleResponse(ForwardingRuleBase):
    id: int
    # Resolved backend (from rule override or service default)
    resolved_backend_host: str
    resolved_backend_port: int
    service_name: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
