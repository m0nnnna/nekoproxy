from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from .common import Protocol


class ServiceBase(BaseModel):
    name: str
    description: Optional[str] = None
    default_backend_host: str
    default_backend_port: int
    protocol: Protocol = Protocol.TCP


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_backend_host: Optional[str] = None
    default_backend_port: Optional[int] = None
    protocol: Optional[Protocol] = None


class ServiceResponse(ServiceBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
