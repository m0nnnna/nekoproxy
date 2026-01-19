import logging
from typing import Optional
from itertools import cycle

from sqlalchemy.orm import Session

from controller.database.repositories import AgentRepository, ServiceAssignmentRepository, BlocklistRepository
from controller.database.models import Agent
from shared.models import AgentConfig, AgentRegistration, AgentHeartbeat, ServiceResponse
from controller.config import settings

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages agent registration, configuration, and load balancing."""

    def __init__(self, db: Session):
        self.db = db
        self.agent_repo = AgentRepository(db)
        self.assignment_repo = ServiceAssignmentRepository(db)
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

        # Get enabled service assignments for this agent
        assignments = self.assignment_repo.get_enabled_for_agent(agent_id)

        # Build list of services from assignments
        services = []
        seen_service_ids = set()
        for assignment in assignments:
            if assignment.service_id not in seen_service_ids:
                seen_service_ids.add(assignment.service_id)
                service = assignment.service
                services.append(ServiceResponse(
                    id=service.id,
                    name=service.name,
                    description=service.description,
                    listen_port=service.listen_port,
                    backend_host=service.backend_host,
                    backend_port=service.backend_port,
                    protocol=service.protocol,
                    created_at=service.created_at,
                    updated_at=service.updated_at
                ))

        # Get blocklist
        blocklist = self.blocklist_repo.get_all_ips()

        return AgentConfig(
            agent_id=agent_id,
            config_version=settings.config_version,
            services=services,
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
