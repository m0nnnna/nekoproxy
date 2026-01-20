from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from .common import AlertSeverity, AlertType


class AlertBase(BaseModel):
    alert_type: AlertType
    severity: AlertSeverity
    source_ip: str
    port: Optional[int] = None
    interface: Optional[str] = None
    description: str
    agent_id: Optional[int] = None


class AlertCreate(AlertBase):
    pass


class AlertResponse(AlertBase):
    id: int
    acknowledged: bool
    created_at: datetime
    agent_hostname: Optional[str] = None

    class Config:
        from_attributes = True
