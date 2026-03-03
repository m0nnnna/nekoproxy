"""Trigger config sync on agents (e.g. after blocklist/firewall/settings changes)."""

import asyncio
import logging
from typing import Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from controller.database.repositories import AgentRepository

logger = logging.getLogger(__name__)

# Control API port on agents (must match agent config api_port)
AGENT_API_PORT = 8002


def _get_agents_to_sync(db: Session, agent_ids: Optional[List[int]] = None):
    """Return list of agents to sync: if agent_ids given, those agents; else all healthy."""
    agent_repo = AgentRepository(db)
    if agent_ids:
        agents = [agent_repo.get_by_id(aid) for aid in agent_ids]
        agents = [a for a in agents if a]
    else:
        agents = agent_repo.get_healthy()
    return agents


async def trigger_sync_all_agents(db: Session, agent_ids: Optional[List[int]] = None) -> Dict[str, int]:
    """POST /trigger-sync to healthy agents. If agent_ids is set, only those agents; else all healthy. Returns {"success": n, "failed": n}."""
    agents = _get_agents_to_sync(db, agent_ids)

    async def trigger_one(agent):
        url = f"http://{agent.wireguard_ip}:{AGENT_API_PORT}/trigger-sync"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(url)
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
