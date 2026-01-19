import logging
from typing import Optional
from itertools import cycle

from sqlalchemy.orm import Session

from controller.database.repositories import AgentRepository, ForwardingRuleRepository, BlocklistRepository
from controller.database.models import Agent
from shared.models import AgentConfig, AgentRegistration, AgentHeartbeat
from shared.models.rule import ForwardingRuleResponse
from controller.config import settings

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages agent registration, configuration, and load balancing."""

    def __init__(self, db: Session):
        self.db = db
        self.agent_repo = AgentRepository(db)
        self.rule_repo = ForwardingRuleRepository(db)
        self.blocklist_repo = BlocklistRepository(db)
        self._agent_cycle: Optional[cycle] = None
        self._last_agent_count = 0

    def register_agent(self, registration: AgentRegistration) -> Agent:
        """Register a new agent or update existing one."""
        existing = self.agent_repo.get_by_wireguard_ip(registration.wireguard_ip)

        if existing:
            logger.info(f"Agent {registration.hostname} re-registered from {registration.wireguard_ip}")
            # Update existing agent
            existing.hostname = registration.hostname
            existing.public_ip = registration.public_ip
            existing.version = registration.version
            self.db.commit()
            self.db.refresh(existing)
            return existing

        # Create new agent
        agent = self.agent_repo.create(
            hostname=registration.hostname,
            wireguard_ip=registration.wireguard_ip,
            public_ip=registration.public_ip,
            version=registration.version
        )
        logger.info(f"New agent registered: {agent.hostname} ({agent.wireguard_ip})")
        self._invalidate_cycle()
        return agent

    def process_heartbeat(self, agent_id: int, heartbeat: AgentHeartbeat) -> Optional[Agent]:
        """Process agent heartbeat."""
        agent = self.agent_repo.update_heartbeat(
            agent_id=agent_id,
            active_connections=heartbeat.active_connections,
            cpu_percent=heartbeat.cpu_percent,
            memory_percent=heartbeat.memory_percent
        )
        if agent:
            logger.debug(f"Heartbeat from {agent.hostname}: {heartbeat.active_connections} connections")
        return agent

    def get_agent_config(self, agent_id: int) -> Optional[AgentConfig]:
        """Get configuration for an agent."""
        agent = self.agent_repo.get_by_id(agent_id)
        if not agent:
            return None

        # Get enabled forwarding rules
        rules = self.rule_repo.get_enabled()
        rule_responses = []
        for rule in rules:
            rule_responses.append(ForwardingRuleResponse(
                id=rule.id,
                service_id=rule.service_id,
                listen_port=rule.listen_port,
                backend_host=rule.backend_host,
                backend_port=rule.backend_port,
                protocol=rule.protocol,
                enabled=rule.enabled,
                resolved_backend_host=rule.resolved_backend_host,
                resolved_backend_port=rule.resolved_backend_port,
                service_name=rule.service.name,
                created_at=rule.created_at,
                updated_at=rule.updated_at
            ))

        # Get blocklist
        blocklist = self.blocklist_repo.get_all_ips()

        return AgentConfig(
            agent_id=agent_id,
            config_version=settings.config_version,
            forwarding_rules=rule_responses,
            blocklist=blocklist,
            heartbeat_interval=settings.heartbeat_interval
        )

    def get_healthy_agents(self) -> list[Agent]:
        """Get all healthy agents."""
        return self.agent_repo.get_healthy()

    def get_next_agent(self) -> Optional[Agent]:
        """Get next agent using round-robin load balancing."""
        healthy = self.get_healthy_agents()

        if not healthy:
            return None

        # Rebuild cycle if agent count changed
        if len(healthy) != self._last_agent_count:
            self._agent_cycle = cycle(healthy)
            self._last_agent_count = len(healthy)

        if self._agent_cycle:
            return next(self._agent_cycle)

        return healthy[0] if healthy else None

    def _invalidate_cycle(self):
        """Invalidate the round-robin cycle."""
        self._agent_cycle = None
        self._last_agent_count = 0
