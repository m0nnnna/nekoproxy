from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from .models import Agent, Service, ForwardingRule, BlocklistEntry, ConnectionStat
from shared.models.common import HealthStatus, Protocol


class AgentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, hostname: str, wireguard_ip: str, public_ip: Optional[str] = None, version: str = "1.0.0") -> Agent:
        agent = Agent(
            hostname=hostname,
            wireguard_ip=wireguard_ip,
            public_ip=public_ip,
            version=version,
            status=HealthStatus.HEALTHY,
            last_heartbeat=datetime.utcnow()
        )
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def get_by_id(self, agent_id: int) -> Optional[Agent]:
        return self.db.query(Agent).filter(Agent.id == agent_id).first()

    def get_by_wireguard_ip(self, wireguard_ip: str) -> Optional[Agent]:
        return self.db.query(Agent).filter(Agent.wireguard_ip == wireguard_ip).first()

    def get_all(self) -> List[Agent]:
        return self.db.query(Agent).all()

    def get_healthy(self) -> List[Agent]:
        return self.db.query(Agent).filter(Agent.status == HealthStatus.HEALTHY).all()

    def update_heartbeat(self, agent_id: int, active_connections: int, cpu_percent: float, memory_percent: float) -> Optional[Agent]:
        agent = self.get_by_id(agent_id)
        if agent:
            agent.last_heartbeat = datetime.utcnow()
            agent.status = HealthStatus.HEALTHY
            agent.active_connections = active_connections
            agent.cpu_percent = cpu_percent
            agent.memory_percent = memory_percent
            self.db.commit()
            self.db.refresh(agent)
        return agent

    def mark_unhealthy(self, agent_id: int) -> Optional[Agent]:
        agent = self.get_by_id(agent_id)
        if agent:
            agent.status = HealthStatus.UNHEALTHY
            self.db.commit()
        return agent

    def delete(self, agent_id: int) -> bool:
        agent = self.get_by_id(agent_id)
        if agent:
            self.db.delete(agent)
            self.db.commit()
            return True
        return False


class ServiceRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, name: str, default_backend_host: str, default_backend_port: int,
               description: Optional[str] = None, protocol: Protocol = Protocol.TCP) -> Service:
        service = Service(
            name=name,
            description=description,
            default_backend_host=default_backend_host,
            default_backend_port=default_backend_port,
            protocol=protocol
        )
        self.db.add(service)
        self.db.commit()
        self.db.refresh(service)
        return service

    def get_by_id(self, service_id: int) -> Optional[Service]:
        return self.db.query(Service).filter(Service.id == service_id).first()

    def get_by_name(self, name: str) -> Optional[Service]:
        return self.db.query(Service).filter(Service.name == name).first()

    def get_all(self) -> List[Service]:
        return self.db.query(Service).all()

    def update(self, service_id: int, **kwargs) -> Optional[Service]:
        service = self.get_by_id(service_id)
        if service:
            for key, value in kwargs.items():
                if value is not None and hasattr(service, key):
                    setattr(service, key, value)
            self.db.commit()
            self.db.refresh(service)
        return service

    def delete(self, service_id: int) -> bool:
        service = self.get_by_id(service_id)
        if service:
            self.db.delete(service)
            self.db.commit()
            return True
        return False


class ForwardingRuleRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, service_id: int, listen_port: int, protocol: Protocol = Protocol.TCP,
               backend_host: Optional[str] = None, backend_port: Optional[int] = None,
               enabled: bool = True) -> ForwardingRule:
        rule = ForwardingRule(
            service_id=service_id,
            listen_port=listen_port,
            backend_host=backend_host,
            backend_port=backend_port,
            protocol=protocol,
            enabled=enabled
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def get_by_id(self, rule_id: int) -> Optional[ForwardingRule]:
        return self.db.query(ForwardingRule).filter(ForwardingRule.id == rule_id).first()

    def get_all(self) -> List[ForwardingRule]:
        return self.db.query(ForwardingRule).all()

    def get_enabled(self) -> List[ForwardingRule]:
        return self.db.query(ForwardingRule).filter(ForwardingRule.enabled == True).all()

    def get_by_port(self, listen_port: int, protocol: Protocol) -> Optional[ForwardingRule]:
        return self.db.query(ForwardingRule).filter(
            and_(ForwardingRule.listen_port == listen_port, ForwardingRule.protocol == protocol)
        ).first()

    def update(self, rule_id: int, **kwargs) -> Optional[ForwardingRule]:
        rule = self.get_by_id(rule_id)
        if rule:
            for key, value in kwargs.items():
                if hasattr(rule, key):
                    setattr(rule, key, value)
            self.db.commit()
            self.db.refresh(rule)
        return rule

    def delete(self, rule_id: int) -> bool:
        rule = self.get_by_id(rule_id)
        if rule:
            self.db.delete(rule)
            self.db.commit()
            return True
        return False


class BlocklistRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, ip: str, reason: Optional[str] = None) -> BlocklistEntry:
        entry = BlocklistEntry(ip=ip, reason=reason)
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def remove(self, ip: str) -> bool:
        entry = self.db.query(BlocklistEntry).filter(BlocklistEntry.ip == ip).first()
        if entry:
            self.db.delete(entry)
            self.db.commit()
            return True
        return False

    def is_blocked(self, ip: str) -> bool:
        return self.db.query(BlocklistEntry).filter(BlocklistEntry.ip == ip).first() is not None

    def get_all(self) -> List[BlocklistEntry]:
        return self.db.query(BlocklistEntry).all()

    def get_all_ips(self) -> List[str]:
        entries = self.db.query(BlocklistEntry.ip).all()
        return [e.ip for e in entries]


class ConnectionStatRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, agent_id: int, client_ip: str, status: str, service_id: Optional[int] = None,
            duration: Optional[float] = None, bytes_sent: int = 0, bytes_received: int = 0) -> ConnectionStat:
        stat = ConnectionStat(
            agent_id=agent_id,
            service_id=service_id,
            client_ip=client_ip,
            status=status,
            duration=duration,
            bytes_sent=bytes_sent,
            bytes_received=bytes_received
        )
        self.db.add(stat)
        self.db.commit()
        self.db.refresh(stat)
        return stat

    def add_batch(self, stats: List[dict]) -> int:
        """Add multiple stats at once."""
        count = 0
        for stat_data in stats:
            stat = ConnectionStat(**stat_data)
            self.db.add(stat)
            count += 1
        self.db.commit()
        return count

    def get_recent(self, hours: int = 24, limit: int = 100) -> List[ConnectionStat]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return self.db.query(ConnectionStat).filter(
            ConnectionStat.timestamp >= cutoff
        ).order_by(ConnectionStat.timestamp.desc()).limit(limit).all()

    def get_by_agent(self, agent_id: int, limit: int = 100) -> List[ConnectionStat]:
        return self.db.query(ConnectionStat).filter(
            ConnectionStat.agent_id == agent_id
        ).order_by(ConnectionStat.timestamp.desc()).limit(limit).all()

    def cleanup_old(self, days: int = 30) -> int:
        """Delete stats older than specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = self.db.query(ConnectionStat).filter(
            ConnectionStat.timestamp < cutoff
        ).delete()
        self.db.commit()
        return deleted

    def get_stats_summary(self, hours: int = 24) -> dict:
        """Get aggregated statistics."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stats = self.db.query(ConnectionStat).filter(
            ConnectionStat.timestamp >= cutoff
        ).all()

        total_connections = len(stats)
        total_bytes_sent = sum(s.bytes_sent for s in stats)
        total_bytes_received = sum(s.bytes_received for s in stats)
        blocked_count = sum(1 for s in stats if s.status == "blocked")

        return {
            "total_connections": total_connections,
            "blocked_connections": blocked_count,
            "total_bytes_sent": total_bytes_sent,
            "total_bytes_received": total_bytes_received,
            "period_hours": hours
        }
