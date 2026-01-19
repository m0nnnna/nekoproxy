from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship

from .database import Base
from shared.models.common import Protocol, HealthStatus


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
    version = Column(String(20), default="1.0.0")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    connection_stats = relationship("ConnectionStat", back_populates="agent", cascade="all, delete-orphan")


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    default_backend_host = Column(String(255), nullable=False)
    default_backend_port = Column(Integer, nullable=False)
    protocol = Column(SQLEnum(Protocol), default=Protocol.TCP)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    forwarding_rules = relationship("ForwardingRule", back_populates="service", cascade="all, delete-orphan")


class ForwardingRule(Base):
    __tablename__ = "forwarding_rules"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    listen_port = Column(Integer, nullable=False)
    backend_host = Column(String(255), nullable=True)  # Override service default
    backend_port = Column(Integer, nullable=True)  # Override service default
    protocol = Column(SQLEnum(Protocol), default=Protocol.TCP)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    service = relationship("Service", back_populates="forwarding_rules")
    connection_stats = relationship("ConnectionStat", back_populates="forwarding_rule", cascade="all, delete-orphan")

    @property
    def resolved_backend_host(self) -> str:
        """Get effective backend host (rule override or service default)."""
        return self.backend_host or self.service.default_backend_host

    @property
    def resolved_backend_port(self) -> int:
        """Get effective backend port (rule override or service default)."""
        return self.backend_port or self.service.default_backend_port


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
    service_id = Column(Integer, ForeignKey("forwarding_rules.id"), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    client_ip = Column(String(45), nullable=False)
    status = Column(String(20), nullable=False)  # connected, disconnected, blocked
    duration = Column(Float, nullable=True)  # seconds
    bytes_sent = Column(Integer, default=0)
    bytes_received = Column(Integer, default=0)

    # Relationships
    agent = relationship("Agent", back_populates="connection_stats")
    forwarding_rule = relationship("ForwardingRule", back_populates="connection_stats")
