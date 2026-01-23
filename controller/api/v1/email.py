"""Email proxy API endpoints."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from controller.database.database import get_db
from controller.database.repositories import (
    EmailConfigRepository, EmailUserRepository, EmailBlocklistRepository, AgentRepository
)
from controller.core.email_manager import EmailManager
from shared.models.email import (
    EmailConfigCreate, EmailConfigUpdate, EmailConfigResponse,
    EmailUserCreate, EmailUserUpdate, EmailUserResponse,
    EmailBlocklistCreate, EmailBlocklistResponse,
    AgentEmailConfig
)
from shared.models.common import EmailDeploymentStatus

router = APIRouter()


# ============================================================================
# Email Configuration Endpoints
# ============================================================================

@router.post("/config", response_model=EmailConfigResponse, status_code=201)
def create_email_config(config: EmailConfigCreate, db: Session = Depends(get_db)):
    """Create email (Mailcow) configuration."""
    repo = EmailConfigRepository(db)

    # Check if config already exists for this agent
    existing = repo.get_for_agent(config.agent_id)
    if existing and existing.agent_id == config.agent_id:
        raise HTTPException(status_code=400, detail="Configuration already exists for this agent")

    created = repo.create(
        mailcow_host=config.mailcow_host,
        mailcow_port=config.mailcow_port,
        mailcow_api_url=config.mailcow_api_url,
        mailcow_api_key=config.mailcow_api_key,
        agent_id=config.agent_id,
        enabled=config.enabled
    )
    return created


@router.get("/config", response_model=List[EmailConfigResponse])
def list_email_configs(db: Session = Depends(get_db)):
    """List all email configurations."""
    repo = EmailConfigRepository(db)
    return repo.get_all()


@router.get("/config/{config_id}", response_model=EmailConfigResponse)
def get_email_config(config_id: int, db: Session = Depends(get_db)):
    """Get a specific email configuration."""
    repo = EmailConfigRepository(db)
    config = repo.get_by_id(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found")
    return config


@router.put("/config/{config_id}", response_model=EmailConfigResponse)
def update_email_config(config_id: int, config: EmailConfigUpdate, db: Session = Depends(get_db)):
    """Update email configuration."""
    repo = EmailConfigRepository(db)
    updated = repo.update(config_id, **config.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Configuration not found")
    return updated


@router.delete("/config/{config_id}")
def delete_email_config(config_id: int, db: Session = Depends(get_db)):
    """Delete email configuration."""
    repo = EmailConfigRepository(db)
    if not repo.delete(config_id):
        raise HTTPException(status_code=404, detail="Configuration not found")
    return {"status": "deleted", "id": config_id}


# ============================================================================
# Deployment Endpoints
# ============================================================================

@router.post("/deploy/{agent_id}")
async def deploy_email_proxy(
    agent_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Deploy Postfix + rspamd to specified agent."""
    agent_repo = AgentRepository(db)
    agent = agent_repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    config_repo = EmailConfigRepository(db)
    config = config_repo.get_for_agent(agent_id)
    if not config:
        raise HTTPException(status_code=400, detail="No email configuration found. Create a configuration first.")

    # Deploy in background
    manager = EmailManager(db)
    background_tasks.add_task(manager.deploy_to_agent, agent_id)

    return {"status": "deployment_started", "agent_id": agent_id, "agent_hostname": agent.hostname}


@router.post("/deploy")
async def deploy_email_proxy_multi(
    agent_ids: List[int],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Deploy Postfix + rspamd to multiple agents."""
    agent_repo = AgentRepository(db)
    manager = EmailManager(db)

    started = []
    for agent_id in agent_ids:
        agent = agent_repo.get_by_id(agent_id)
        if agent:
            background_tasks.add_task(manager.deploy_to_agent, agent_id)
            started.append({"agent_id": agent_id, "hostname": agent.hostname})

    return {"status": "deployment_started", "agents": started}


@router.get("/deploy/status")
def get_deployment_status(db: Session = Depends(get_db)):
    """Get deployment status for all configurations."""
    config_repo = EmailConfigRepository(db)
    agent_repo = AgentRepository(db)

    configs = config_repo.get_all()
    status_list = []

    for config in configs:
        agent_hostname = None
        if config.agent_id:
            agent = agent_repo.get_by_id(config.agent_id)
            if agent:
                agent_hostname = agent.hostname

        status_list.append({
            "config_id": config.id,
            "agent_id": config.agent_id,
            "agent_hostname": agent_hostname or "Global",
            "mailcow_host": config.mailcow_host,
            "deployment_status": config.deployment_status.value,
            "enabled": config.enabled
        })

    return status_list


# ============================================================================
# Email User Management
# ============================================================================

@router.post("/users", response_model=EmailUserResponse, status_code=201)
async def create_email_user(user: EmailUserCreate, db: Session = Depends(get_db)):
    """Create email user and optionally create Mailcow mailbox."""
    repo = EmailUserRepository(db)
    manager = EmailManager(db)

    # Check if user already exists
    if repo.get_by_email(user.email_address):
        raise HTTPException(status_code=400, detail="Email user already exists")

    mailcow_mailbox_id = None
    generated_password = None

    if user.create_mailcow_mailbox:
        # Create mailbox in Mailcow via API
        mailcow_mailbox_id, generated_password = await manager.create_mailcow_mailbox(
            user.email_address,
            user.display_name
        )

    created = repo.create(
        email_address=user.email_address,
        display_name=user.display_name,
        mailcow_mailbox_id=mailcow_mailbox_id,
        agent_id=user.agent_id,
        enabled=user.enabled
    )

    # Return with generated password (only shown once)
    response = EmailUserResponse(
        id=created.id,
        email_address=created.email_address,
        display_name=created.display_name,
        mailcow_mailbox_id=created.mailcow_mailbox_id,
        agent_id=created.agent_id,
        enabled=created.enabled,
        generated_password=generated_password,
        created_at=created.created_at,
        updated_at=created.updated_at
    )
    return response


@router.get("/users", response_model=List[EmailUserResponse])
def list_email_users(db: Session = Depends(get_db)):
    """List all email users."""
    repo = EmailUserRepository(db)
    return repo.get_all()


@router.get("/users/{user_id}", response_model=EmailUserResponse)
def get_email_user(user_id: int, db: Session = Depends(get_db)):
    """Get a specific email user."""
    repo = EmailUserRepository(db)
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.put("/users/{user_id}", response_model=EmailUserResponse)
def update_email_user(user_id: int, user: EmailUserUpdate, db: Session = Depends(get_db)):
    """Update email user."""
    repo = EmailUserRepository(db)
    updated = repo.update(user_id, **user.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


@router.delete("/users/{user_id}")
async def delete_email_user(
    user_id: int,
    delete_mailbox: bool = False,
    db: Session = Depends(get_db)
):
    """Delete email user and optionally delete Mailcow mailbox."""
    repo = EmailUserRepository(db)
    manager = EmailManager(db)

    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if delete_mailbox and user.mailcow_mailbox_id:
        await manager.delete_mailcow_mailbox(user.mailcow_mailbox_id)

    repo.delete(user_id)
    return {"status": "deleted", "id": user_id}


@router.post("/users/{user_id}/toggle", response_model=EmailUserResponse)
def toggle_email_user(user_id: int, db: Session = Depends(get_db)):
    """Toggle email user enabled status."""
    repo = EmailUserRepository(db)
    user = repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    updated = repo.update(user_id, enabled=not user.enabled)
    return updated


# ============================================================================
# Email Blocklist
# ============================================================================

@router.post("/blocklist", response_model=EmailBlocklistResponse, status_code=201)
def add_to_email_blocklist(entry: EmailBlocklistCreate, db: Session = Depends(get_db)):
    """Add entry to email blocklist."""
    repo = EmailBlocklistRepository(db)

    if repo.exists(entry.block_type, entry.value):
        raise HTTPException(status_code=400, detail="Entry already exists in blocklist")

    return repo.add(entry.block_type, entry.value, entry.reason)


@router.get("/blocklist", response_model=List[EmailBlocklistResponse])
def list_email_blocklist(db: Session = Depends(get_db)):
    """List all email blocklist entries."""
    repo = EmailBlocklistRepository(db)
    return repo.get_all()


@router.delete("/blocklist/{entry_id}")
def remove_from_email_blocklist(entry_id: int, db: Session = Depends(get_db)):
    """Remove entry from email blocklist."""
    repo = EmailBlocklistRepository(db)
    if not repo.remove(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "removed", "id": entry_id}


# ============================================================================
# Apply/Sync Configuration
# ============================================================================

@router.post("/apply")
async def apply_email_config(db: Session = Depends(get_db)):
    """Push email configuration to all deployed agents."""
    manager = EmailManager(db)
    results = await manager.sync_all_agents()
    return results


@router.post("/apply/{agent_id}")
async def apply_email_config_to_agent(agent_id: int, db: Session = Depends(get_db)):
    """Push email configuration to a specific agent."""
    manager = EmailManager(db)
    success = await manager.trigger_agent_sync(agent_id)
    if success:
        return {"status": "synced", "agent_id": agent_id}
    else:
        raise HTTPException(status_code=500, detail="Failed to sync configuration to agent")


# ============================================================================
# Agent Config Endpoint (used by agents during config sync)
# ============================================================================

@router.get("/agent/{agent_id}/config", response_model=AgentEmailConfig)
def get_agent_email_config(agent_id: int, db: Session = Depends(get_db)):
    """Get email configuration for a specific agent (used by agent config sync)."""
    manager = EmailManager(db)
    config = manager.get_agent_email_config(agent_id)
    if config is None:
        return AgentEmailConfig(enabled=False)
    return config
