"""Email proxy Pydantic models."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field

from .common import EmailBlocklistType, EmailDeploymentStatus


# Email Config Models
class EmailConfigBase(BaseModel):
    mailcow_host: str
    mailcow_port: int = 25
    mailcow_api_url: Optional[str] = None
    mailcow_api_key: Optional[str] = None
    agent_id: Optional[int] = None
    enabled: bool = True


class EmailConfigCreate(EmailConfigBase):
    pass


class EmailConfigUpdate(BaseModel):
    mailcow_host: Optional[str] = None
    mailcow_port: Optional[int] = None
    mailcow_api_url: Optional[str] = None
    mailcow_api_key: Optional[str] = None
    enabled: Optional[bool] = None


class EmailConfigResponse(EmailConfigBase):
    id: int
    deployment_status: EmailDeploymentStatus
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Email User Models
class EmailUserBase(BaseModel):
    email_address: str
    display_name: Optional[str] = None
    agent_id: Optional[int] = None
    enabled: bool = True


class EmailUserCreate(EmailUserBase):
    create_mailcow_mailbox: bool = True


class EmailUserUpdate(BaseModel):
    display_name: Optional[str] = None
    enabled: Optional[bool] = None


class EmailUserResponse(EmailUserBase):
    id: int
    mailcow_mailbox_id: Optional[str] = None
    generated_password: Optional[str] = None  # Only returned on creation
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Email Blocklist Models
class EmailBlocklistBase(BaseModel):
    block_type: EmailBlocklistType
    value: str
    reason: Optional[str] = None


class EmailBlocklistCreate(EmailBlocklistBase):
    pass


class EmailBlocklistResponse(EmailBlocklistBase):
    id: int
    added_at: datetime

    class Config:
        from_attributes = True


# SASL User Models
class SaslUserBase(BaseModel):
    username: str  # Usually email address
    agent_id: Optional[int] = None
    enabled: bool = True


class SaslUserCreate(SaslUserBase):
    password: str  # Plain text, will be hashed


class SaslUserUpdate(BaseModel):
    enabled: Optional[bool] = None


class SaslUserResponse(SaslUserBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SaslUserPasswordReset(BaseModel):
    new_password: str


# Email Domain Models
class EmailDomainBase(BaseModel):
    domain: str
    enabled: bool = True


class EmailDomainCreate(EmailDomainBase):
    pass


class EmailDomainResponse(EmailDomainBase):
    id: int
    mailcow_managed: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# SASL credential for syncing to agent (includes password for sasldb)
class SaslCredential(BaseModel):
    username: str
    password: str  # Plain password for agent to add to sasldb


# Agent Email Config (sent to agents during config sync)
class AgentEmailConfig(BaseModel):
    """Email configuration sent from controller to agent."""
    enabled: bool = False
    mailcow_host: Optional[str] = None
    mailcow_port: int = 25
    authorized_senders: List[str] = Field(default_factory=list)
    sasl_users: List[SaslCredential] = Field(default_factory=list)  # SASL credentials
    relay_domains: List[str] = Field(default_factory=list)  # Domains to relay for
    blocklist_addresses: List[str] = Field(default_factory=list)
    blocklist_domains: List[str] = Field(default_factory=list)
    blocklist_ips: List[str] = Field(default_factory=list)
    config_version: int = 1
