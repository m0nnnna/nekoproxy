from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from controller.database.database import get_db
from controller.database.repositories import AgentRepository
from controller.core.agent_manager import AgentManager
from shared.models import AgentRegistration, AgentHeartbeat, AgentConfig, AgentStatus

router = APIRouter()


@router.post("/register", response_model=AgentStatus)
def register_agent(registration: AgentRegistration, db: Session = Depends(get_db)):
    """Register a new agent or update existing registration."""
    manager = AgentManager(db)
    agent = manager.register_agent(registration)
    return AgentStatus(
        id=agent.id,
        hostname=agent.hostname,
        wireguard_ip=agent.wireguard_ip,
        public_ip=agent.public_ip,
        status=agent.status,
        last_heartbeat=agent.last_heartbeat,
        active_connections=agent.active_connections,
        cpu_percent=agent.cpu_percent,
        memory_percent=agent.memory_percent,
        version=agent.version,
        created_at=agent.created_at
    )


@router.post("/{agent_id}/heartbeat", response_model=AgentStatus)
def heartbeat(agent_id: int, heartbeat_data: AgentHeartbeat, db: Session = Depends(get_db)):
    """Process agent heartbeat."""
    manager = AgentManager(db)
    agent = manager.process_heartbeat(agent_id, heartbeat_data)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentStatus(
        id=agent.id,
        hostname=agent.hostname,
        wireguard_ip=agent.wireguard_ip,
        public_ip=agent.public_ip,
        status=agent.status,
        last_heartbeat=agent.last_heartbeat,
        active_connections=agent.active_connections,
        cpu_percent=agent.cpu_percent,
        memory_percent=agent.memory_percent,
        version=agent.version,
        created_at=agent.created_at
    )


@router.get("/{agent_id}/config", response_model=AgentConfig)
def get_agent_config(agent_id: int, db: Session = Depends(get_db)):
    """Get configuration for an agent."""
    manager = AgentManager(db)
    config = manager.get_agent_config(agent_id)
    if not config:
        raise HTTPException(status_code=404, detail="Agent not found")
    return config


@router.get("", response_model=list[AgentStatus])
def list_agents(db: Session = Depends(get_db)):
    """List all agents."""
    repo = AgentRepository(db)
    agents = repo.get_all()
    return [
        AgentStatus(
            id=a.id,
            hostname=a.hostname,
            wireguard_ip=a.wireguard_ip,
            public_ip=a.public_ip,
            status=a.status,
            last_heartbeat=a.last_heartbeat,
            active_connections=a.active_connections,
            cpu_percent=a.cpu_percent,
            memory_percent=a.memory_percent,
            version=a.version,
            created_at=a.created_at
        )
        for a in agents
    ]


@router.get("/{agent_id}", response_model=AgentStatus)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """Get specific agent details."""
    repo = AgentRepository(db)
    agent = repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentStatus(
        id=agent.id,
        hostname=agent.hostname,
        wireguard_ip=agent.wireguard_ip,
        public_ip=agent.public_ip,
        status=agent.status,
        last_heartbeat=agent.last_heartbeat,
        active_connections=agent.active_connections,
        cpu_percent=agent.cpu_percent,
        memory_percent=agent.memory_percent,
        version=agent.version,
        created_at=agent.created_at
    )


@router.delete("/{agent_id}")
def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    """Remove an agent."""
    repo = AgentRepository(db)
    if not repo.delete(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted", "agent_id": agent_id}
