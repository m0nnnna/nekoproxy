from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from .common import Protocol


class ServiceBase(BaseModel):
    name: str
    description: Optional[str] = None
    listen_port: int  # Port to listen on for incoming connections
    backend_host: str  # Backend server to proxy to
    backend_port: int  # Backend port to proxy to
    protocol: Protocol = Protocol.TCP


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    listen_port: Optional[int] = None
    backend_host: Optional[str] = None
    backend_port: Optional[int] = None
    protocol: Optional[Protocol] = None


class ServiceResponse(ServiceBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
