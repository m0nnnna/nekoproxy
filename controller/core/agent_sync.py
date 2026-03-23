"""Trigger config sync on agents (e.g. after blocklist/firewall/settings changes)."""

import asyncio
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from controller.database.repositories import AgentRepository

logger = logging.getLogger(__name__)

# Control API port on agents (must match agent config api_port)
AGENT_API_PORT = 8002


def get_agent_host(agent) -> Optional[str]:
    """Return host (IP or hostname) to reach the agent, or None. From control_url or wireguard_ip."""
    if getattr(agent, "control_url", None) and (agent.control_url or "").strip():
        parsed = urlparse((agent.control_url or "").strip())
        if parsed.hostname:
            return parsed.hostname
    if getattr(agent, "wireguard_ip", None) and agent.wireguard_ip:
        return agent.wireguard_ip
    return None


def get_agent_base_url(agent) -> Optional[str]:
    """Return base URL to reach agent control API, or None if unreachable.
    Prefers control_url (e.g. http://127.0.0.1:8002 for same-machine/Windows agents), else wireguard_ip:8002.
    """
    if getattr(agent, "control_url", None) and (agent.control_url or "").strip():
        base = (agent.control_url or "").strip().rstrip("/")
        if base:
            return base
    if getattr(agent, "wireguard_ip", None) and agent.wireguard_ip:
        return f"https://{agent.wireguard_ip}:{AGENT_API_PORT}"
    return None


def _get_agents_to_sync(db: Session, agent_ids: Optional[List[int]] = None):
    """Return list of agents to sync: if agent_ids given, those agents; else all agents.
    Only includes agents that are reachable (have wireguard_ip or control_url).
    Uses get_all() so the sync count reflects every reachable agent, not just healthy ones.
    """
    agent_repo = AgentRepository(db)
    if agent_ids:
        agents = [agent_repo.get_by_id(aid) for aid in agent_ids]
        agents = [a for a in agents if a]
    else:
        agents = agent_repo.get_all()
    return [a for a in agents if get_agent_base_url(a)]


def _controller_token() -> Optional[str]:
    """Return the controller token from settings (set during startup token initialisation)."""
    try:
        from controller.config import settings
        return settings.controller_token or None
    except Exception:
        return None


def _agent_api_ssl_verify() -> bool:
    """Return whether to verify agent ControlAPI SSL certificates."""
    try:
        from controller.config import settings
        return settings.agent_api_ssl_verify
    except Exception:
        return False


async def trigger_sync_all_agents(db: Session, agent_ids: Optional[List[int]] = None) -> Dict[str, int]:
    """POST /trigger-sync to reachable agents (wireguard_ip or control_url)."""
    agents = _get_agents_to_sync(db, agent_ids)
    token = _controller_token()
    ssl_verify = _agent_api_ssl_verify()

    async def trigger_one(agent):
        base = get_agent_base_url(agent)
        if not base:
            return False
        url = f"{base.rstrip('/')}/trigger-sync"
        headers = {}
        if token:
            headers["X-Controller-Token"] = token
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=ssl_verify) as client:
                r = await client.post(url, headers=headers)
                if r.status_code == 200:
                    logger.info(f"Triggered sync on agent {agent.hostname}")
                    return True
                logger.warning(f"Sync failed on {agent.hostname}: {r.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Failed to reach agent {agent.hostname}: {e}")
            return False

    outcomes = await asyncio.gather(*[trigger_one(a) for a in agents])
    success = sum(1 for o in outcomes if o)
    failed = len(outcomes) - success
    return {"success": success, "failed": failed}
