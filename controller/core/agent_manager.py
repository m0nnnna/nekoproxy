import logging
from datetime import datetime
from typing import Optional
from itertools import cycle

from sqlalchemy.orm import Session
from sqlalchemy import func

from controller.database.repositories import AgentRepository, ServiceAssignmentRepository, BlocklistRepository, FirewallRuleRepository
from controller.database.models import Agent, FirewallRule, ServiceAssignment, Service, BlocklistEntry
from shared.models import AgentConfig, AgentRegistration, AgentHeartbeat, ServiceResponse, FirewallRuleResponse
from controller.config import settings

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages agent registration, configuration, and load balancing."""

    def __init__(self, db: Session):
        self.db = db
        self.agent_repo = AgentRepository(db)
        self.assignment_repo = ServiceAssignmentRepository(db)
        self.blocklist_repo = BlocklistRepository(db)
        self.firewall_repo = FirewallRuleRepository(db)
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

    def _compute_config_version(self, agent_id: int) -> int:
        """Compute config version based on timestamps and record counts.

        Returns a version number that changes when:
        - Any config record is created (timestamp increases)
        - Any config record is updated (timestamp increases)
        - Any config record is deleted (count changes)

        Version format: timestamp_seconds * 10000 + record_count_hash
        This ensures deletions are detected even when timestamps don't change.
        """
        # Get max updated_at from firewall rules (agent-specific and global)
        firewall_max = self.db.query(func.max(FirewallRule.updated_at)).filter(
            (FirewallRule.agent_id == agent_id) | (FirewallRule.agent_id == None)
        ).scalar()

        # Get max updated_at from service assignments (agent-specific and global)
        assignment_max = self.db.query(func.max(ServiceAssignment.updated_at)).filter(
            (ServiceAssignment.agent_id == agent_id) | (ServiceAssignment.agent_id == None)
        ).scalar()

        # Get max updated_at from services
        service_max = self.db.query(func.max(Service.updated_at)).scalar()

        # Get max added_at from blocklist
        blocklist_max = self.db.query(func.max(BlocklistEntry.added_at)).scalar()

        # Get record counts to detect deletions
        firewall_count = self.db.query(func.count(FirewallRule.id)).filter(
            (FirewallRule.agent_id == agent_id) | (FirewallRule.agent_id == None)
        ).scalar() or 0

        assignment_count = self.db.query(func.count(ServiceAssignment.id)).filter(
            (ServiceAssignment.agent_id == agent_id) | (ServiceAssignment.agent_id == None)
        ).scalar() or 0

        blocklist_count = self.db.query(func.count(BlocklistEntry.id)).scalar() or 0

        # Find the maximum timestamp across all sources
        timestamps = [t for t in [firewall_max, assignment_max, service_max, blocklist_max] if t]

        if not timestamps:
            # No data, but include counts in case records exist with null timestamps
            return firewall_count + assignment_count + blocklist_count + 1

        max_timestamp = max(timestamps)
        # Combine timestamp (seconds) with count hash for unique version
        # timestamp * 10000 gives room for count variations without overflow
        count_hash = (firewall_count * 100 + assignment_count * 10 + blocklist_count) % 10000
        return int(max_timestamp.timestamp()) * 10000 + count_hash

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

        # Get firewall rules for this agent
        firewall_rules_db = self.firewall_repo.get_enabled_for_agent(agent_id)
        firewall_rules = [
            FirewallRuleResponse(
                id=rule.id,
                port=rule.port,
                protocol=rule.protocol,
                interface=rule.interface,
                action=rule.action,
                description=rule.description,
                enabled=rule.enabled,
                agent_id=rule.agent_id,
                created_at=rule.created_at,
                updated_at=rule.updated_at
            )
            for rule in firewall_rules_db
        ]

        return AgentConfig(
            agent_id=agent_id,
            config_version=self._compute_config_version(agent_id),
            services=services,
            blocklist=blocklist,
            firewall_rules=firewall_rules,
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
