from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from .models import Agent, Service, ServiceAssignment, BlocklistEntry, ConnectionStat, FirewallRule, Alert, EmailConfig, EmailUser, EmailBlocklistEntry, EmailStat
from shared.models.common import HealthStatus, Protocol, FirewallAction, AlertSeverity, AlertType, EmailBlocklistType, EmailDeploymentStatus


class AgentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, hostname: str, wireguard_ip: str, public_ip: Optional[str] = None, version: str = "2.0.0") -> Agent:
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

    def create(self, name: str, listen_port: int, backend_host: str, backend_port: int,
               description: Optional[str] = None, protocol: Protocol = Protocol.TCP) -> Service:
        service = Service(
            name=name,
            description=description,
            listen_port=listen_port,
            backend_host=backend_host,
            backend_port=backend_port,
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

    def get_by_listen_port(self, listen_port: int, protocol: Protocol) -> Optional[Service]:
        return self.db.query(Service).filter(
            and_(Service.listen_port == listen_port, Service.protocol == protocol)
        ).first()

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


class ServiceAssignmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, service_id: int, agent_id: Optional[int] = None, enabled: bool = True) -> ServiceAssignment:
        assignment = ServiceAssignment(
            service_id=service_id,
            agent_id=agent_id,
            enabled=enabled
        )
        self.db.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def get_by_id(self, assignment_id: int) -> Optional[ServiceAssignment]:
        return self.db.query(ServiceAssignment).filter(ServiceAssignment.id == assignment_id).first()

    def get_all(self) -> List[ServiceAssignment]:
        return self.db.query(ServiceAssignment).all()

    def get_enabled(self) -> List[ServiceAssignment]:
        return self.db.query(ServiceAssignment).filter(ServiceAssignment.enabled == True).all()

    def get_by_agent(self, agent_id: int) -> List[ServiceAssignment]:
        """Get all assignments for a specific agent (including global assignments where agent_id is NULL)."""
        return self.db.query(ServiceAssignment).filter(
            or_(ServiceAssignment.agent_id == agent_id, ServiceAssignment.agent_id == None)
        ).all()

    def get_enabled_for_agent(self, agent_id: int) -> List[ServiceAssignment]:
        """Get enabled assignments for a specific agent (including global assignments)."""
        return self.db.query(ServiceAssignment).filter(
            and_(
                ServiceAssignment.enabled == True,
                or_(ServiceAssignment.agent_id == agent_id, ServiceAssignment.agent_id == None)
            )
        ).all()

    def get_by_service(self, service_id: int) -> List[ServiceAssignment]:
        return self.db.query(ServiceAssignment).filter(ServiceAssignment.service_id == service_id).all()

    def exists(self, service_id: int, agent_id: Optional[int]) -> bool:
        """Check if an assignment already exists."""
        query = self.db.query(ServiceAssignment).filter(ServiceAssignment.service_id == service_id)
        if agent_id is None:
            query = query.filter(ServiceAssignment.agent_id == None)
        else:
            query = query.filter(ServiceAssignment.agent_id == agent_id)
        return query.first() is not None

    def update(self, assignment_id: int, **kwargs) -> Optional[ServiceAssignment]:
        assignment = self.get_by_id(assignment_id)
        if assignment:
            for key, value in kwargs.items():
                if hasattr(assignment, key):
                    setattr(assignment, key, value)
            self.db.commit()
            self.db.refresh(assignment)
        return assignment

    def delete(self, assignment_id: int) -> bool:
        assignment = self.get_by_id(assignment_id)
        if assignment:
            self.db.delete(assignment)
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


class FirewallRuleRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, port: int, interface: str, protocol: Protocol = Protocol.TCP,
               action: FirewallAction = FirewallAction.BLOCK, description: Optional[str] = None,
               enabled: bool = True, agent_id: Optional[int] = None) -> FirewallRule:
        rule = FirewallRule(
            port=port,
            protocol=protocol,
            interface=interface,
            action=action,
            description=description,
            enabled=enabled,
            agent_id=agent_id
        )
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def get_by_id(self, rule_id: int) -> Optional[FirewallRule]:
        return self.db.query(FirewallRule).filter(FirewallRule.id == rule_id).first()

    def get_all(self) -> List[FirewallRule]:
        return self.db.query(FirewallRule).all()

    def get_enabled(self) -> List[FirewallRule]:
        return self.db.query(FirewallRule).filter(FirewallRule.enabled == True).all()

    def get_enabled_for_agent(self, agent_id: int) -> List[FirewallRule]:
        """Get enabled firewall rules for a specific agent (including global rules where agent_id is NULL)."""
        return self.db.query(FirewallRule).filter(
            and_(
                FirewallRule.enabled == True,
                or_(FirewallRule.agent_id == agent_id, FirewallRule.agent_id == None)
            )
        ).all()

    def get_by_interface(self, interface: str) -> List[FirewallRule]:
        return self.db.query(FirewallRule).filter(FirewallRule.interface == interface).all()

    def get_by_port_interface(self, port: int, protocol: Protocol, interface: str) -> Optional[FirewallRule]:
        return self.db.query(FirewallRule).filter(
            and_(
                FirewallRule.port == port,
                FirewallRule.protocol == protocol,
                FirewallRule.interface == interface
            )
        ).first()

    def update(self, rule_id: int, **kwargs) -> Optional[FirewallRule]:
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


class AlertRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, alert_type: AlertType, severity: AlertSeverity, source_ip: str,
               description: str, port: Optional[int] = None, interface: Optional[str] = None,
               agent_id: Optional[int] = None) -> Alert:
        alert = Alert(
            alert_type=alert_type,
            severity=severity,
            source_ip=source_ip,
            port=port,
            interface=interface,
            description=description,
            agent_id=agent_id,
            acknowledged=False
        )
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        return alert

    def get_by_id(self, alert_id: int) -> Optional[Alert]:
        return self.db.query(Alert).filter(Alert.id == alert_id).first()

    def get_all(self, limit: int = 100) -> List[Alert]:
        return self.db.query(Alert).order_by(Alert.created_at.desc()).limit(limit).all()

    def get_unacknowledged(self, limit: int = 100) -> List[Alert]:
        return self.db.query(Alert).filter(
            Alert.acknowledged == False
        ).order_by(Alert.created_at.desc()).limit(limit).all()

    def get_by_severity(self, severity: AlertSeverity, limit: int = 100) -> List[Alert]:
        return self.db.query(Alert).filter(
            Alert.severity == severity
        ).order_by(Alert.created_at.desc()).limit(limit).all()

    def get_by_source_ip(self, source_ip: str, limit: int = 100) -> List[Alert]:
        return self.db.query(Alert).filter(
            Alert.source_ip == source_ip
        ).order_by(Alert.created_at.desc()).limit(limit).all()

    def get_recent(self, hours: int = 24, limit: int = 100) -> List[Alert]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return self.db.query(Alert).filter(
            Alert.created_at >= cutoff
        ).order_by(Alert.created_at.desc()).limit(limit).all()

    def acknowledge(self, alert_id: int) -> Optional[Alert]:
        alert = self.get_by_id(alert_id)
        if alert:
            alert.acknowledged = True
            self.db.commit()
            self.db.refresh(alert)
        return alert

    def acknowledge_all(self) -> int:
        """Acknowledge all unacknowledged alerts."""
        count = self.db.query(Alert).filter(
            Alert.acknowledged == False
        ).update({"acknowledged": True})
        self.db.commit()
        return count

    def delete(self, alert_id: int) -> bool:
        alert = self.get_by_id(alert_id)
        if alert:
            self.db.delete(alert)
            self.db.commit()
            return True
        return False

    def cleanup_old(self, days: int = 30) -> int:
        """Delete alerts older than specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = self.db.query(Alert).filter(
            Alert.created_at < cutoff
        ).delete()
        self.db.commit()
        return deleted

    def get_counts_by_severity(self) -> dict:
        """Get count of unacknowledged alerts by severity."""
        counts = {}
        for severity in AlertSeverity:
            counts[severity.value] = self.db.query(Alert).filter(
                and_(Alert.severity == severity, Alert.acknowledged == False)
            ).count()
        return counts


class EmailConfigRepository:
    """Repository for email proxy configuration."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, mailcow_host: str, mailcow_port: int = 25,
               mailcow_api_url: Optional[str] = None, mailcow_api_key: Optional[str] = None,
               agent_id: Optional[int] = None, enabled: bool = True) -> EmailConfig:
        config = EmailConfig(
            mailcow_host=mailcow_host,
            mailcow_port=mailcow_port,
            mailcow_api_url=mailcow_api_url,
            mailcow_api_key=mailcow_api_key,
            agent_id=agent_id,
            enabled=enabled
        )
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return config

    def get_by_id(self, config_id: int) -> Optional[EmailConfig]:
        return self.db.query(EmailConfig).filter(EmailConfig.id == config_id).first()

    def get_for_agent(self, agent_id: Optional[int]) -> Optional[EmailConfig]:
        """Get config for agent. First checks agent-specific, then falls back to global."""
        if agent_id:
            config = self.db.query(EmailConfig).filter(EmailConfig.agent_id == agent_id).first()
            if config:
                return config
        # Fall back to global config (agent_id is NULL)
        return self.db.query(EmailConfig).filter(EmailConfig.agent_id == None).first()

    def get_global(self) -> Optional[EmailConfig]:
        """Get the global email config (agent_id is NULL)."""
        return self.db.query(EmailConfig).filter(EmailConfig.agent_id == None).first()

    def get_all(self) -> List[EmailConfig]:
        return self.db.query(EmailConfig).all()

    def get_deployed(self) -> List[EmailConfig]:
        """Get all configs with deployed status."""
        return self.db.query(EmailConfig).filter(
            EmailConfig.deployment_status == EmailDeploymentStatus.DEPLOYED
        ).all()

    def update(self, config_id: int, **kwargs) -> Optional[EmailConfig]:
        config = self.get_by_id(config_id)
        if config:
            for key, value in kwargs.items():
                if hasattr(config, key) and value is not None:
                    setattr(config, key, value)
            self.db.commit()
            self.db.refresh(config)
        return config

    def update_deployment_status(self, config_id: int, status: EmailDeploymentStatus) -> Optional[EmailConfig]:
        return self.update(config_id, deployment_status=status)

    def delete(self, config_id: int) -> bool:
        config = self.get_by_id(config_id)
        if config:
            self.db.delete(config)
            self.db.commit()
            return True
        return False


class EmailUserRepository:
    """Repository for authorized email senders."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, email_address: str, display_name: Optional[str] = None,
               mailcow_mailbox_id: Optional[str] = None, agent_id: Optional[int] = None,
               enabled: bool = True) -> EmailUser:
        user = EmailUser(
            email_address=email_address.lower(),
            display_name=display_name,
            mailcow_mailbox_id=mailcow_mailbox_id,
            agent_id=agent_id,
            enabled=enabled
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def get_by_id(self, user_id: int) -> Optional[EmailUser]:
        return self.db.query(EmailUser).filter(EmailUser.id == user_id).first()

    def get_by_email(self, email_address: str) -> Optional[EmailUser]:
        return self.db.query(EmailUser).filter(
            EmailUser.email_address == email_address.lower()
        ).first()

    def get_all(self) -> List[EmailUser]:
        return self.db.query(EmailUser).all()

    def get_enabled(self) -> List[EmailUser]:
        return self.db.query(EmailUser).filter(EmailUser.enabled == True).all()

    def get_enabled_for_agent(self, agent_id: int) -> List[EmailUser]:
        """Get enabled users for agent (including global users where agent_id is NULL)."""
        return self.db.query(EmailUser).filter(
            and_(
                EmailUser.enabled == True,
                or_(EmailUser.agent_id == agent_id, EmailUser.agent_id == None)
            )
        ).all()

    def get_enabled_emails(self) -> List[str]:
        """Get list of enabled email addresses."""
        users = self.db.query(EmailUser.email_address).filter(EmailUser.enabled == True).all()
        return [u.email_address for u in users]

    def update(self, user_id: int, **kwargs) -> Optional[EmailUser]:
        user = self.get_by_id(user_id)
        if user:
            for key, value in kwargs.items():
                if hasattr(user, key):
                    setattr(user, key, value)
            self.db.commit()
            self.db.refresh(user)
        return user

    def delete(self, user_id: int) -> bool:
        user = self.get_by_id(user_id)
        if user:
            self.db.delete(user)
            self.db.commit()
            return True
        return False


class EmailBlocklistRepository:
    """Repository for email blocklist entries."""

    def __init__(self, db: Session):
        self.db = db

    def add(self, block_type: EmailBlocklistType, value: str,
            reason: Optional[str] = None) -> EmailBlocklistEntry:
        entry = EmailBlocklistEntry(
            block_type=block_type,
            value=value.lower(),
            reason=reason
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get_by_id(self, entry_id: int) -> Optional[EmailBlocklistEntry]:
        return self.db.query(EmailBlocklistEntry).filter(
            EmailBlocklistEntry.id == entry_id
        ).first()

    def exists(self, block_type: EmailBlocklistType, value: str) -> bool:
        return self.db.query(EmailBlocklistEntry).filter(
            and_(
                EmailBlocklistEntry.block_type == block_type,
                EmailBlocklistEntry.value == value.lower()
            )
        ).first() is not None

    def get_all(self) -> List[EmailBlocklistEntry]:
        return self.db.query(EmailBlocklistEntry).all()

    def get_by_type(self, block_type: EmailBlocklistType) -> List[EmailBlocklistEntry]:
        return self.db.query(EmailBlocklistEntry).filter(
            EmailBlocklistEntry.block_type == block_type
        ).all()

    def get_addresses(self) -> List[str]:
        """Get all blocked email addresses."""
        entries = self.db.query(EmailBlocklistEntry.value).filter(
            EmailBlocklistEntry.block_type == EmailBlocklistType.ADDRESS
        ).all()
        return [e.value for e in entries]

    def get_domains(self) -> List[str]:
        """Get all blocked domains."""
        entries = self.db.query(EmailBlocklistEntry.value).filter(
            EmailBlocklistEntry.block_type == EmailBlocklistType.DOMAIN
        ).all()
        return [e.value for e in entries]

    def get_ips(self) -> List[str]:
        """Get all blocked IPs and IP ranges."""
        entries = self.db.query(EmailBlocklistEntry.value).filter(
            or_(
                EmailBlocklistEntry.block_type == EmailBlocklistType.IP,
                EmailBlocklistEntry.block_type == EmailBlocklistType.IP_RANGE
            )
        ).all()
        return [e.value for e in entries]

    def remove(self, entry_id: int) -> bool:
        entry = self.get_by_id(entry_id)
        if entry:
            self.db.delete(entry)
            self.db.commit()
            return True
        return False


class EmailSaslUserRepository:
    """Repository for SASL authentication users."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, username: str, password_hash: str,
               agent_id: Optional[int] = None, enabled: bool = True) -> "EmailSaslUser":
        from .models import EmailSaslUser
        user = EmailSaslUser(
            username=username.lower(),
            password_hash=password_hash,
            agent_id=agent_id,
            enabled=enabled
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def get_by_id(self, user_id: int) -> Optional["EmailSaslUser"]:
        from .models import EmailSaslUser
        return self.db.query(EmailSaslUser).filter(EmailSaslUser.id == user_id).first()

    def get_by_username(self, username: str) -> Optional["EmailSaslUser"]:
        from .models import EmailSaslUser
        return self.db.query(EmailSaslUser).filter(
            EmailSaslUser.username == username.lower()
        ).first()

    def get_all(self) -> List["EmailSaslUser"]:
        from .models import EmailSaslUser
        return self.db.query(EmailSaslUser).all()

    def get_enabled(self) -> List["EmailSaslUser"]:
        from .models import EmailSaslUser
        return self.db.query(EmailSaslUser).filter(EmailSaslUser.enabled == True).all()

    def get_enabled_for_agent(self, agent_id: int) -> List["EmailSaslUser"]:
        """Get enabled SASL users for agent (including global users)."""
        from .models import EmailSaslUser
        return self.db.query(EmailSaslUser).filter(
            and_(
                EmailSaslUser.enabled == True,
                or_(EmailSaslUser.agent_id == agent_id, EmailSaslUser.agent_id == None)
            )
        ).all()

    def update(self, user_id: int, **kwargs) -> Optional["EmailSaslUser"]:
        user = self.get_by_id(user_id)
        if user:
            for key, value in kwargs.items():
                if hasattr(user, key):
                    setattr(user, key, value)
            self.db.commit()
            self.db.refresh(user)
        return user

    def update_password(self, user_id: int, password_hash: str) -> Optional["EmailSaslUser"]:
        return self.update(user_id, password_hash=password_hash)

    def delete(self, user_id: int) -> bool:
        user = self.get_by_id(user_id)
        if user:
            self.db.delete(user)
            self.db.commit()
            return True
        return False


class EmailDomainRepository:
    """Repository for email relay domains."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, domain: str, mailcow_managed: bool = False,
               enabled: bool = True) -> "EmailDomain":
        from .models import EmailDomain
        entry = EmailDomain(
            domain=domain.lower(),
            mailcow_managed=mailcow_managed,
            enabled=enabled
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get_by_id(self, domain_id: int) -> Optional["EmailDomain"]:
        from .models import EmailDomain
        return self.db.query(EmailDomain).filter(EmailDomain.id == domain_id).first()

    def get_by_domain(self, domain: str) -> Optional["EmailDomain"]:
        from .models import EmailDomain
        return self.db.query(EmailDomain).filter(
            EmailDomain.domain == domain.lower()
        ).first()

    def exists(self, domain: str) -> bool:
        return self.get_by_domain(domain) is not None

    def get_all(self) -> List["EmailDomain"]:
        from .models import EmailDomain
        return self.db.query(EmailDomain).all()

    def get_enabled(self) -> List["EmailDomain"]:
        from .models import EmailDomain
        return self.db.query(EmailDomain).filter(EmailDomain.enabled == True).all()

    def get_enabled_domains(self) -> List[str]:
        """Get list of enabled domain names."""
        from .models import EmailDomain
        domains = self.db.query(EmailDomain.domain).filter(EmailDomain.enabled == True).all()
        return [d.domain for d in domains]

    def update(self, domain_id: int, **kwargs) -> Optional["EmailDomain"]:
        domain = self.get_by_id(domain_id)
        if domain:
            for key, value in kwargs.items():
                if hasattr(domain, key):
                    setattr(domain, key, value)
            self.db.commit()
            self.db.refresh(domain)
        return domain

    def sync_from_mailcow(self, domains: List[str]):
        """Sync domains from Mailcow API - add new ones, mark existing as mailcow_managed."""
        from .models import EmailDomain
        for domain_name in domains:
            existing = self.get_by_domain(domain_name)
            if existing:
                if not existing.mailcow_managed:
                    existing.mailcow_managed = True
                    self.db.commit()
            else:
                self.create(domain_name, mailcow_managed=True, enabled=True)

    def delete(self, domain_id: int) -> bool:
        domain = self.get_by_id(domain_id)
        if domain:
            self.db.delete(domain)
            self.db.commit()
            return True
        return False


class MailcowMailboxRepository:
    """Repository for cached Mailcow mailbox data."""

    def __init__(self, db: Session):
        self.db = db

    def sync(self, mailboxes_data: list):
        """Sync mailboxes from Mailcow API response."""
        from .models import MailcowMailbox
        now = datetime.utcnow()

        # Get existing usernames for comparison
        existing = {m.username: m for m in self.db.query(MailcowMailbox).all()}
        seen = set()

        for mb in mailboxes_data:
            username = mb.get("username", "")
            if not username:
                continue
            seen.add(username)

            if username in existing:
                # Update existing
                mailbox = existing[username]
                mailbox.name = mb.get("name", "")
                mailbox.domain = mb.get("domain", "")
                mailbox.quota = mb.get("quota", 0)
                mailbox.quota_used = mb.get("quota_used", 0)
                mailbox.active = mb.get("active") in (1, "1", True)
                mailbox.last_synced = now
            else:
                # Create new
                mailbox = MailcowMailbox(
                    username=username,
                    name=mb.get("name", ""),
                    domain=mb.get("domain", ""),
                    quota=mb.get("quota", 0),
                    quota_used=mb.get("quota_used", 0),
                    active=mb.get("active") in (1, "1", True),
                    last_synced=now
                )
                self.db.add(mailbox)

        # Remove mailboxes that no longer exist in Mailcow
        for username, mailbox in existing.items():
            if username not in seen:
                self.db.delete(mailbox)

        self.db.commit()

    def get_all(self) -> list:
        from .models import MailcowMailbox
        return self.db.query(MailcowMailbox).all()

    def clear(self):
        from .models import MailcowMailbox
        self.db.query(MailcowMailbox).delete()
        self.db.commit()


class MailcowAliasRepository:
    """Repository for cached Mailcow alias data."""

    def __init__(self, db: Session):
        self.db = db

    def sync(self, aliases_data: list):
        """Sync aliases from Mailcow API response."""
        from .models import MailcowAlias
        now = datetime.utcnow()

        # Get existing by mailcow_id
        existing = {a.mailcow_id: a for a in self.db.query(MailcowAlias).all()}
        seen = set()

        for alias in aliases_data:
            mailcow_id = alias.get("id")
            if not mailcow_id:
                continue
            seen.add(mailcow_id)

            if mailcow_id in existing:
                # Update existing
                entry = existing[mailcow_id]
                entry.address = alias.get("address", "")
                entry.goto = alias.get("goto", "")
                entry.active = alias.get("active") in (1, "1", True)
                entry.last_synced = now
            else:
                # Create new
                entry = MailcowAlias(
                    mailcow_id=mailcow_id,
                    address=alias.get("address", ""),
                    goto=alias.get("goto", ""),
                    active=alias.get("active") in (1, "1", True),
                    last_synced=now
                )
                self.db.add(entry)

        # Remove aliases that no longer exist in Mailcow
        for mailcow_id, entry in existing.items():
            if mailcow_id not in seen:
                self.db.delete(entry)

        self.db.commit()

    def get_all(self) -> list:
        from .models import MailcowAlias
        return self.db.query(MailcowAlias).all()

    def clear(self):
        from .models import MailcowAlias
        self.db.query(MailcowAlias).delete()
        self.db.commit()


class EmailStatRepository:
    """Repository for email proxy statistics."""

    def __init__(self, db: Session):
        self.db = db

    def add(self, agent_id: int, client_ip: str, status: str, sender: Optional[str] = None,
            recipient: Optional[str] = None, bytes_sent: int = 0, bytes_received: int = 0,
            message_id: Optional[str] = None) -> EmailStat:
        stat = EmailStat(
            agent_id=agent_id,
            client_ip=client_ip,
            sender=sender,
            recipient=recipient,
            status=status,
            bytes_sent=bytes_sent,
            bytes_received=bytes_received,
            message_id=message_id
        )
        self.db.add(stat)
        self.db.commit()
        self.db.refresh(stat)
        return stat

    def add_batch(self, stats: List[dict]) -> int:
        """Add multiple email stats at once."""
        count = 0
        for stat_data in stats:
            stat = EmailStat(**stat_data)
            self.db.add(stat)
            count += 1
        self.db.commit()
        return count

    def get_recent(self, hours: int = 24, limit: int = 100) -> List[EmailStat]:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return self.db.query(EmailStat).filter(
            EmailStat.timestamp >= cutoff
        ).order_by(EmailStat.timestamp.desc()).limit(limit).all()

    def get_by_agent(self, agent_id: int, limit: int = 100) -> List[EmailStat]:
        return self.db.query(EmailStat).filter(
            EmailStat.agent_id == agent_id
        ).order_by(EmailStat.timestamp.desc()).limit(limit).all()

    def cleanup_old(self, days: int = 30) -> int:
        """Delete stats older than specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = self.db.query(EmailStat).filter(
            EmailStat.timestamp < cutoff
        ).delete()
        self.db.commit()
        return deleted

    def get_stats_summary(self, hours: int = 24) -> dict:
        """Get aggregated email statistics."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stats = self.db.query(EmailStat).filter(
            EmailStat.timestamp >= cutoff
        ).all()

        total_emails = len(stats)
        total_bytes_sent = sum(s.bytes_sent for s in stats)
        total_bytes_received = sum(s.bytes_received for s in stats)
        blocked_count = sum(1 for s in stats if s.status == "blocked")
        delivered_count = sum(1 for s in stats if s.status == "delivered")
        deferred_count = sum(1 for s in stats if s.status == "deferred")
        bounced_count = sum(1 for s in stats if s.status == "bounced")

        return {
            "total_emails": total_emails,
            "blocked_emails": blocked_count,
            "delivered_emails": delivered_count,
            "deferred_emails": deferred_count,
            "bounced_emails": bounced_count,
            "email_bytes_sent": total_bytes_sent,
            "email_bytes_received": total_bytes_received,
            "period_hours": hours
        }
