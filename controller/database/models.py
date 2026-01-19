from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.orm import relationship

from .database import Base
from shared.models.common import Protocol, HealthStatus, FirewallAction


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
    service_assignments = relationship("ServiceAssignment", back_populates="agent", cascade="all, delete-orphan")


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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
