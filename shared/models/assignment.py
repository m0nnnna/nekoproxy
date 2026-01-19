from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ServiceAssignmentBase(BaseModel):
    service_id: int
    agent_id: Optional[int] = None  # None means assign to all agents
    enabled: bool = True


class ServiceAssignmentCreate(ServiceAssignmentBase):
    pass


class ServiceAssignmentUpdate(BaseModel):
    service_id: Optional[int] = None
    agent_id: Optional[int] = None
    enabled: Optional[bool] = None


class ServiceAssignmentResponse(ServiceAssignmentBase):
    id: int
    service_name: str
    agent_name: Optional[str] = None  # None if assigned to all agents
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
