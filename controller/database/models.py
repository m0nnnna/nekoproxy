from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship

from .database import Base
from shared.models.common import Protocol, HealthStatus, FirewallAction, AlertSeverity, AlertType, EmailBlocklistType, EmailDeploymentStatus


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String(255), nullable=False)
    wireguard_ip = Column(String(45), unique=True, nullable=False)
    public_ip = Column(String(45), nullable=True)
    status = Column(SQLEnum(HealthStatus), default=HealthStatus.UNKNOWN)
    last_heartbeat = Column(DateTime, nullable=True)
    active_connections = Column(Integer, default=0)
    cpu_percent = Column(Float, default=0.0)
    memory_percent = Column(Float, default=0.0)
    version = Column(String(20), default="2.0.0")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    connection_stats = relationship("ConnectionStat", back_populates="agent", cascade="all, delete-orphan")
    service_assignments = relationship("ServiceAssignment", back_populates="agent", cascade="all, delete-orphan")
    email_stats = relationship("EmailStat", back_populates="agent", cascade="all, delete-orphan")


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    listen_port = Column(Integer, nullable=False)  # Port to listen on
    backend_host = Column(String(255), nullable=False)  # Backend server
    backend_port = Column(Integer, nullable=False)  # Backend port
    protocol = Column(SQLEnum(Protocol), default=Protocol.TCP)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    assignments = relationship("ServiceAssignment", back_populates="service", cascade="all, delete-orphan")
    connection_stats = relationship("ConnectionStat", back_populates="service", cascade="all, delete-orphan")


class ServiceAssignment(Base):
    """Assigns a service to an agent. If agent_id is NULL, the service is assigned to all agents."""
    __tablename__ = "service_assignments"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)  # NULL = all agents
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    service = relationship("Service", back_populates="assignments")
    agent = relationship("Agent", back_populates="service_assignments")


class BlocklistEntry(Base):
    __tablename__ = "blocklist"

    id = Column(Integer, primary_key=True, index=True)
    ip = Column(String(45), unique=True, nullable=False, index=True)
    reason = Column(Text, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)


class ConnectionStat(Base):
    __tablename__ = "connection_stats"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    client_ip = Column(String(45), nullable=False)
    status = Column(String(20), nullable=False)  # connected, disconnected, blocked
    duration = Column(Float, nullable=True)  # seconds
    bytes_sent = Column(Integer, default=0)
    bytes_received = Column(Integer, default=0)

    # Relationships
    agent = relationship("Agent", back_populates="connection_stats")
    service = relationship("Service", back_populates="connection_stats")


class FirewallRule(Base):
    __tablename__ = "firewall_rules"

    id = Column(Integer, primary_key=True, index=True)
    port = Column(Integer, nullable=False)
    protocol = Column(SQLEnum(Protocol), default=Protocol.TCP)
    interface = Column(String(50), nullable=False)  # "public", "wireguard", or specific interface
    action = Column(SQLEnum(FirewallAction), default=FirewallAction.BLOCK)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)  # NULL = all agents
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    agent = relationship("Agent")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    alert_type = Column(SQLEnum(AlertType), nullable=False)
    severity = Column(SQLEnum(AlertSeverity), default=AlertSeverity.MEDIUM)
    source_ip = Column(String(45), nullable=False, index=True)
    port = Column(Integer, nullable=True)
    interface = Column(String(50), nullable=True)
    description = Column(Text, nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    acknowledged = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    agent = relationship("Agent")


class EmailConfig(Base):
    """Email proxy configuration (Mailcow connection settings)."""
    __tablename__ = "email_configs"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)  # NULL = global default
    mailcow_host = Column(String(255), nullable=False)
    mailcow_port = Column(Integer, default=25)
    mailcow_api_url = Column(String(512), nullable=True)
    mailcow_api_key = Column(String(255), nullable=True)
    deployment_status = Column(SQLEnum(EmailDeploymentStatus), default=EmailDeploymentStatus.NOT_DEPLOYED)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    agent = relationship("Agent")


class EmailUser(Base):
    """Authorized email senders (Postfix address map)."""
    __tablename__ = "email_users"

    id = Column(Integer, primary_key=True, index=True)
    email_address = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=True)
    mailcow_mailbox_id = Column(String(100), nullable=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)  # NULL = all agents
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    agent = relationship("Agent")


class EmailBlocklistEntry(Base):
    """Email blocklist entries (addresses, domains, IPs)."""
    __tablename__ = "email_blocklist"

    id = Column(Integer, primary_key=True, index=True)
    block_type = Column(SQLEnum(EmailBlocklistType), nullable=False)
    value = Column(String(255), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)


class EmailSaslUser(Base):
    """SASL authentication users for email relay."""
    __tablename__ = "email_sasl_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, nullable=False, index=True)  # Usually email address
    password_hash = Column(String(255), nullable=False)  # Stored hashed, sent to agent for sasldb
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)  # NULL = all agents
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    agent = relationship("Agent")


class EmailDomain(Base):
    """Email domains for relay (fetched from Mailcow or manually added)."""
    __tablename__ = "email_domains"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(255), unique=True, nullable=False, index=True)
    mailcow_managed = Column(Boolean, default=False)  # True if from Mailcow API
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MailcowMailbox(Base):
    """Cached mailbox data from Mailcow API."""
    __tablename__ = "mailcow_mailboxes"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, nullable=False, index=True)  # Full email address
    name = Column(String(255), nullable=True)
    domain = Column(String(255), nullable=False)
    quota = Column(Integer, default=0)  # Bytes
    quota_used = Column(Integer, default=0)  # Bytes
    active = Column(Boolean, default=True)
    last_synced = Column(DateTime, default=datetime.utcnow)


class MailcowAlias(Base):
    """Cached alias data from Mailcow API."""
    __tablename__ = "mailcow_aliases"

    id = Column(Integer, primary_key=True, index=True)
    mailcow_id = Column(Integer, unique=True, nullable=False)  # ID from Mailcow
    address = Column(String(255), nullable=False, index=True)
    goto = Column(Text, nullable=False)  # Comma-separated destinations
    active = Column(Boolean, default=True)
    last_synced = Column(DateTime, default=datetime.utcnow)


class EmailStat(Base):
    """Email proxy statistics."""
    __tablename__ = "email_stats"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    client_ip = Column(String(45), nullable=False)
    sender = Column(String(255), nullable=True)  # From address
    recipient = Column(String(255), nullable=True)  # To address
    status = Column(String(20), nullable=False)  # delivered, blocked, deferred, bounced
    bytes_sent = Column(Integer, default=0)
    bytes_received = Column(Integer, default=0)
    message_id = Column(String(255), nullable=True)

    # Relationships
    agent = relationship("Agent")
