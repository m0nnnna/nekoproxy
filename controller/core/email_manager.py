"""Email proxy management for NekoProxy controller."""

import logging
import asyncio
import secrets
import string
import hashlib
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import func

from controller.database.repositories import (
    EmailConfigRepository, EmailUserRepository, EmailBlocklistRepository,
    AgentRepository, EmailSaslUserRepository, EmailDomainRepository,
    MailcowMailboxRepository, MailcowAliasRepository
)
from controller.database.models import (
    Agent, EmailConfig, EmailUser, EmailBlocklistEntry,
    EmailSaslUser, EmailDomain
)
from shared.models.email import AgentEmailConfig, SaslCredential
from shared.models.common import EmailDeploymentStatus

logger = logging.getLogger(__name__)


class EmailManager:
    """Manages email proxy deployment and configuration."""

    def __init__(self, db: Session):
        self.db = db
        self.config_repo = EmailConfigRepository(db)
        self.user_repo = EmailUserRepository(db)
        self.blocklist_repo = EmailBlocklistRepository(db)
        self.agent_repo = AgentRepository(db)
        self.sasl_repo = EmailSaslUserRepository(db)
        self.domain_repo = EmailDomainRepository(db)
        self.mailbox_repo = MailcowMailboxRepository(db)
        self.alias_repo = MailcowAliasRepository(db)

    async def deploy_to_agent(self, agent_id: int) -> Tuple[bool, str]:
        """Deploy Postfix + rspamd + SASL to an agent.

        Returns:
            Tuple of (success: bool, message: str)
        """
        agent = self.agent_repo.get_by_id(agent_id)
        if not agent:
            logger.error(f"Agent {agent_id} not found")
            return False, "Agent not found"

        config = self.config_repo.get_for_agent(agent_id)
        if not config:
            logger.error(f"No email config found for agent {agent_id}")
            return False, "No email configuration found"

        # Update status to deploying
        self.config_repo.update_deployment_status(config.id, EmailDeploymentStatus.DEPLOYING)

        try:
            # Trigger deployment on agent via control API
            url = f"http://{agent.wireguard_ip}:8002/deploy-email"

            deploy_config = {
                "mailcow_host": config.mailcow_host,
                "mailcow_port": config.mailcow_port,
                "proxy_ip": agent.public_ip or agent.wireguard_ip,
            }

            async with httpx.AsyncClient(timeout=300.0) as client:  # 5 min timeout for deployment
                response = await client.post(url, json=deploy_config)
                response.raise_for_status()

            self.config_repo.update_deployment_status(config.id, EmailDeploymentStatus.DEPLOYED)
            logger.info(f"Email proxy deployed to agent {agent.hostname}")
            return True, f"Deployed to {agent.hostname}"

        except httpx.TimeoutException:
            logger.error(f"Timeout deploying email proxy to agent {agent.hostname}")
            self.config_repo.update_deployment_status(config.id, EmailDeploymentStatus.FAILED)
            return False, "Deployment timed out"
        except httpx.HTTPStatusError as e:
            # Try to extract error message from agent response
            error_message = f"HTTP error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                if "message" in error_data:
                    error_message = error_data["message"]
            except Exception:
                pass
            logger.error(f"HTTP error deploying email proxy to agent {agent.hostname}: {error_message}")
            self.config_repo.update_deployment_status(config.id, EmailDeploymentStatus.FAILED)
            return False, error_message
        except Exception as e:
            logger.error(f"Failed to deploy email proxy to agent {agent.hostname}: {e}")
            self.config_repo.update_deployment_status(config.id, EmailDeploymentStatus.FAILED)
            return False, str(e)

    def get_agent_email_config(self, agent_id: int) -> Optional[AgentEmailConfig]:
        """Build email configuration for an agent."""
        config = self.config_repo.get_for_agent(agent_id)
        if not config or not config.enabled:
            return AgentEmailConfig(enabled=False)

        # Check if deployed
        if config.deployment_status != EmailDeploymentStatus.DEPLOYED:
            return AgentEmailConfig(enabled=False)

        # Get authorized senders
        users = self.user_repo.get_enabled_for_agent(agent_id)
        authorized_senders = [u.email_address for u in users]

        # Get SASL users with credentials
        sasl_users_db = self.sasl_repo.get_enabled_for_agent(agent_id)
        sasl_users = [
            SaslCredential(username=u.username, password=u.password_hash)
            for u in sasl_users_db
        ]

        # Get relay domains
        relay_domains = self.domain_repo.get_enabled_domains()

        # Get blocklists
        blocklist_addresses = self.blocklist_repo.get_addresses()
        blocklist_domains = self.blocklist_repo.get_domains()
        blocklist_ips = self.blocklist_repo.get_ips()

        return AgentEmailConfig(
            enabled=True,
            mailcow_host=config.mailcow_host,
            mailcow_port=config.mailcow_port,
            authorized_senders=authorized_senders,
            sasl_users=sasl_users,
            relay_domains=relay_domains,
            blocklist_addresses=blocklist_addresses,
            blocklist_domains=blocklist_domains,
            blocklist_ips=blocklist_ips,
            config_version=self._compute_version()
        )

    def _compute_version(self) -> int:
        """Compute config version for change detection."""
        config_max = self.db.query(func.max(EmailConfig.updated_at)).scalar()
        user_max = self.db.query(func.max(EmailUser.updated_at)).scalar()
        blocklist_max = self.db.query(func.max(EmailBlocklistEntry.added_at)).scalar()
        sasl_max = self.db.query(func.max(EmailSaslUser.updated_at)).scalar()
        domain_max = self.db.query(func.max(EmailDomain.updated_at)).scalar()

        timestamps = [t for t in [config_max, user_max, blocklist_max, sasl_max, domain_max] if t]
        if not timestamps:
            return 1

        max_timestamp = max(timestamps)
        return int(max_timestamp.timestamp())

    # =========================================================================
    # SASL User Management
    # =========================================================================

    def create_sasl_user(self, username: str, password: str,
                         agent_id: Optional[int] = None) -> Tuple[EmailSaslUser, str]:
        """Create a new SASL user.

        Returns:
            Tuple of (user, password) - password is the plain text for display
        """
        # Store password as plain text (will be sent to agent for sasldb)
        # In production, you might want to encrypt this at rest
        user = self.sasl_repo.create(
            username=username.lower(),
            password_hash=password,  # Store plain for sasldb sync
            agent_id=agent_id,
            enabled=True
        )
        return user, password

    def reset_sasl_password(self, user_id: int) -> Tuple[Optional[EmailSaslUser], Optional[str]]:
        """Reset SASL user password.

        Returns:
            Tuple of (user, new_password) or (None, None) if not found
        """
        user = self.sasl_repo.get_by_id(user_id)
        if not user:
            return None, None

        new_password = self._generate_password()
        self.sasl_repo.update_password(user_id, new_password)
        return user, new_password

    def get_all_sasl_users(self) -> List[EmailSaslUser]:
        """Get all SASL users."""
        return self.sasl_repo.get_all()

    def delete_sasl_user(self, user_id: int) -> bool:
        """Delete a SASL user."""
        return self.sasl_repo.delete(user_id)

    def toggle_sasl_user(self, user_id: int) -> Optional[EmailSaslUser]:
        """Toggle SASL user enabled status."""
        user = self.sasl_repo.get_by_id(user_id)
        if user:
            return self.sasl_repo.update(user_id, enabled=not user.enabled)
        return None

    # =========================================================================
    # Domain Management
    # =========================================================================

    def create_domain(self, domain: str) -> EmailDomain:
        """Create a new relay domain."""
        return self.domain_repo.create(domain.lower(), mailcow_managed=False, enabled=True)

    def get_all_domains(self) -> List[EmailDomain]:
        """Get all relay domains."""
        return self.domain_repo.get_all()

    def delete_domain(self, domain_id: int) -> bool:
        """Delete a relay domain."""
        return self.domain_repo.delete(domain_id)

    def toggle_domain(self, domain_id: int) -> Optional[EmailDomain]:
        """Toggle domain enabled status."""
        domain = self.domain_repo.get_by_id(domain_id)
        if domain:
            return self.domain_repo.update(domain_id, enabled=not domain.enabled)
        return None

    # =========================================================================
    # Mailcow API Integration
    # =========================================================================

    async def fetch_mailcow_domains(self) -> List[Dict[str, Any]]:
        """Fetch all domains from Mailcow API."""
        config = self.config_repo.get_global()
        if not config or not config.mailcow_api_url or not config.mailcow_api_key:
            logger.warning("Mailcow API not configured")
            return []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{config.mailcow_api_url.rstrip('/')}/api/v1/get/domain/all",
                    headers={"X-API-Key": config.mailcow_api_key}
                )
                response.raise_for_status()
                domains = response.json()
                logger.info(f"Fetched {len(domains)} domains from Mailcow")
                return domains
        except Exception as e:
            logger.error(f"Failed to fetch Mailcow domains: {e}")
            return []

    async def sync_mailcow_domains(self) -> int:
        """Sync domains from Mailcow to local database.

        Returns:
            Number of domains synced
        """
        domains_data = await self.fetch_mailcow_domains()
        if not domains_data:
            return 0

        domain_names = [d.get("domain_name") for d in domains_data if d.get("domain_name")]
        self.domain_repo.sync_from_mailcow(domain_names)
        return len(domain_names)

    async def fetch_mailcow_mailboxes(self) -> List[Dict[str, Any]]:
        """Fetch all mailboxes from Mailcow API."""
        config = self.config_repo.get_global()
        if not config or not config.mailcow_api_url or not config.mailcow_api_key:
            logger.warning("Mailcow API not configured")
            return []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{config.mailcow_api_url.rstrip('/')}/api/v1/get/mailbox/all",
                    headers={"X-API-Key": config.mailcow_api_key}
                )
                response.raise_for_status()
                mailboxes = response.json()
                logger.info(f"Fetched {len(mailboxes)} mailboxes from Mailcow")
                return mailboxes
        except Exception as e:
            logger.error(f"Failed to fetch Mailcow mailboxes: {e}")
            return []

    async def sync_mailcow_mailboxes(self) -> int:
        """Sync mailboxes from Mailcow to local cache.

        Returns:
            Number of mailboxes synced
        """
        mailboxes_data = await self.fetch_mailcow_mailboxes()
        if mailboxes_data:
            self.mailbox_repo.sync(mailboxes_data)
        return len(mailboxes_data)

    def get_cached_mailboxes(self) -> List[Dict[str, Any]]:
        """Get cached mailboxes from database."""
        mailboxes = self.mailbox_repo.get_all()
        return [
            {
                "username": m.username,
                "name": m.name,
                "domain": m.domain,
                "quota": m.quota,
                "quota_used": m.quota_used,
                "active": 1 if m.active else 0,
            }
            for m in mailboxes
        ]

    async def fetch_mailcow_aliases(self) -> List[Dict[str, Any]]:
        """Fetch all aliases from Mailcow API."""
        config = self.config_repo.get_global()
        if not config or not config.mailcow_api_url or not config.mailcow_api_key:
            logger.warning("Mailcow API not configured")
            return []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{config.mailcow_api_url.rstrip('/')}/api/v1/get/alias/all",
                    headers={"X-API-Key": config.mailcow_api_key}
                )
                response.raise_for_status()
                aliases = response.json()
                logger.info(f"Fetched {len(aliases)} aliases from Mailcow")
                return aliases
        except Exception as e:
            logger.error(f"Failed to fetch Mailcow aliases: {e}")
            return []

    async def sync_mailcow_aliases(self) -> int:
        """Sync aliases from Mailcow to local cache.

        Returns:
            Number of aliases synced
        """
        aliases_data = await self.fetch_mailcow_aliases()
        if aliases_data:
            self.alias_repo.sync(aliases_data)
        return len(aliases_data)

    def get_cached_aliases(self) -> List[Dict[str, Any]]:
        """Get cached aliases from database."""
        aliases = self.alias_repo.get_all()
        return [
            {
                "id": a.mailcow_id,
                "address": a.address,
                "goto": a.goto,
                "active": 1 if a.active else 0,
            }
            for a in aliases
        ]

    async def sync_all_mailcow_data(self) -> Dict[str, int]:
        """Sync all data from Mailcow (domains, mailboxes, aliases).

        Returns:
            Dict with counts of synced items
        """
        results = {
            "domains": await self.sync_mailcow_domains(),
            "mailboxes": await self.sync_mailcow_mailboxes(),
            "aliases": await self.sync_mailcow_aliases(),
        }
        logger.info(f"Mailcow sync complete: {results}")
        return results

    async def create_mailcow_alias(self, address: str, goto: str) -> Tuple[bool, str]:
        """Create an alias in Mailcow.

        Args:
            address: The alias address (e.g., alias@domain.com)
            goto: The destination address(es), comma-separated

        Returns:
            Tuple of (success, message)
        """
        config = self.config_repo.get_global()
        if not config or not config.mailcow_api_url or not config.mailcow_api_key:
            return False, "Mailcow API not configured"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{config.mailcow_api_url.rstrip('/')}/api/v1/add/alias",
                    headers={"X-API-Key": config.mailcow_api_key},
                    json={
                        "address": address,
                        "goto": goto,
                        "active": "1"
                    }
                )
                response.raise_for_status()
                logger.info(f"Created Mailcow alias: {address} -> {goto}")
                return True, f"Created alias {address}"
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                if isinstance(error_data, list) and error_data:
                    error_msg = str(error_data[0].get("msg", error_msg))
            except:
                pass
            logger.error(f"Failed to create Mailcow alias: {error_msg}")
            return False, error_msg
        except Exception as e:
            logger.error(f"Failed to create Mailcow alias: {e}")
            return False, str(e)

    async def delete_mailcow_alias(self, alias_id: int) -> Tuple[bool, str]:
        """Delete an alias from Mailcow.

        Args:
            alias_id: The Mailcow alias ID

        Returns:
            Tuple of (success, message)
        """
        config = self.config_repo.get_global()
        if not config or not config.mailcow_api_url or not config.mailcow_api_key:
            return False, "Mailcow API not configured"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{config.mailcow_api_url.rstrip('/')}/api/v1/delete/alias",
                    headers={"X-API-Key": config.mailcow_api_key},
                    json=[str(alias_id)]
                )
                response.raise_for_status()
                logger.info(f"Deleted Mailcow alias ID: {alias_id}")
                return True, "Alias deleted"
        except Exception as e:
            logger.error(f"Failed to delete Mailcow alias: {e}")
            return False, str(e)

    # =========================================================================
    # Mailbox Management
    # =========================================================================

    async def create_mailcow_mailbox(self, email_address: str,
                                      display_name: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """Create a mailbox in Mailcow via API.

        Returns:
            Tuple of (mailbox_id, generated_password) or (None, None) on failure
        """
        config = self.config_repo.get_global()
        if not config or not config.mailcow_api_url or not config.mailcow_api_key:
            logger.warning("Mailcow API not configured, skipping mailbox creation")
            return None, None

        local_part, domain = email_address.split('@')
        password = self._generate_password()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{config.mailcow_api_url.rstrip('/')}/api/v1/add/mailbox",
                    headers={"X-API-Key": config.mailcow_api_key},
                    json={
                        "local_part": local_part,
                        "domain": domain,
                        "name": display_name or local_part,
                        "password": password,
                        "password2": password,
                        "quota": "1024",  # 1GB default
                        "active": "1"
                    }
                )
                response.raise_for_status()

                mailbox_id = email_address  # Mailcow uses email as the ID
                logger.info(f"Created Mailcow mailbox for {email_address}")
                return mailbox_id, password

        except Exception as e:
            logger.error(f"Failed to create Mailcow mailbox: {e}")
            return None, None

    async def delete_mailcow_mailbox(self, mailbox_id: str) -> bool:
        """Delete a mailbox from Mailcow via API."""
        config = self.config_repo.get_global()
        if not config or not config.mailcow_api_url or not config.mailcow_api_key:
            return False

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{config.mailcow_api_url.rstrip('/')}/api/v1/delete/mailbox",
                    headers={"X-API-Key": config.mailcow_api_key},
                    json=[mailbox_id]
                )
                response.raise_for_status()
                logger.info(f"Deleted Mailcow mailbox {mailbox_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to delete Mailcow mailbox: {e}")
            return False

    # =========================================================================
    # Sync Operations
    # =========================================================================

    async def sync_all_agents(self) -> dict:
        """Trigger email config sync on all deployed agents."""
        configs = self.config_repo.get_deployed()

        results = {"success": 0, "failed": 0, "agents": []}

        async def trigger_sync(config: EmailConfig):
            if not config.agent_id:
                return None
            agent = self.agent_repo.get_by_id(config.agent_id)
            if not agent:
                return None
            url = f"http://{agent.wireguard_ip}:8002/trigger-email-sync"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(url)
                    return response.status_code == 200
            except Exception as e:
                logger.warning(f"Failed to sync email config to agent {agent.hostname}: {e}")
                return False

        tasks = [trigger_sync(c) for c in configs]
        outcomes = await asyncio.gather(*tasks)

        for outcome in outcomes:
            if outcome is True:
                results["success"] += 1
            elif outcome is False:
                results["failed"] += 1

        return results

    async def trigger_agent_sync(self, agent_id: int) -> bool:
        """Trigger email config sync on a specific agent."""
        agent = self.agent_repo.get_by_id(agent_id)
        if not agent:
            return False

        url = f"http://{agent.wireguard_ip}:8002/trigger-email-sync"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url)
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"Failed to sync email config to agent {agent.hostname}: {e}")
            return False

    def _generate_password(self, length: int = 16) -> str:
        """Generate a secure random password."""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(length))
