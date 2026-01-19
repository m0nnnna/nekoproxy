from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from controller.database.database import get_db
from controller.database.repositories import ServiceAssignmentRepository, ServiceRepository, AgentRepository
from shared.models import ServiceAssignmentCreate, ServiceAssignmentUpdate, ServiceAssignmentResponse

router = APIRouter()


@router.post("", response_model=ServiceAssignmentResponse, status_code=201)
def create_assignment(assignment: ServiceAssignmentCreate, db: Session = Depends(get_db)):
    """Assign a service to an agent (or all agents if agent_id is null)."""
    assign_repo = ServiceAssignmentRepository(db)
    service_repo = ServiceRepository(db)
    agent_repo = AgentRepository(db)

    # Verify service exists
    service = service_repo.get_by_id(assignment.service_id)
    if not service:
        raise HTTPException(status_code=400, detail="Service not found")

    # Verify agent exists if specified
    agent = None
    if assignment.agent_id is not None:
        agent = agent_repo.get_by_id(assignment.agent_id)
        if not agent:
            raise HTTPException(status_code=400, detail="Agent not found")

    # Check for duplicate assignment
    if assign_repo.exists(assignment.service_id, assignment.agent_id):
        target = agent.hostname if agent else "all agents"
        raise HTTPException(
            status_code=400,
            detail=f"Service '{service.name}' is already assigned to {target}"
        )

    created = assign_repo.create(
        service_id=assignment.service_id,
        agent_id=assignment.agent_id,
        enabled=assignment.enabled
    )

    return ServiceAssignmentResponse(
        id=created.id,
        service_id=created.service_id,
        agent_id=created.agent_id,
        enabled=created.enabled,
        service_name=service.name,
        agent_name=agent.hostname if agent else None,
        created_at=created.created_at,
        updated_at=created.updated_at
    )


@router.get("", response_model=list[ServiceAssignmentResponse])
def list_assignments(enabled_only: bool = False, agent_id: int = None, db: Session = Depends(get_db)):
    """List all service assignments."""
    repo = ServiceAssignmentRepository(db)

    if agent_id is not None:
        assignments = repo.get_enabled_for_agent(agent_id) if enabled_only else repo.get_by_agent(agent_id)
    elif enabled_only:
        assignments = repo.get_enabled()
    else:
        assignments = repo.get_all()

    return [
        ServiceAssignmentResponse(
            id=a.id,
            service_id=a.service_id,
            agent_id=a.agent_id,
            enabled=a.enabled,
            service_name=a.service.name,
            agent_name=a.agent.hostname if a.agent else None,
            created_at=a.created_at,
            updated_at=a.updated_at
        )
        for a in assignments
    ]


@router.get("/{assignment_id}", response_model=ServiceAssignmentResponse)
def get_assignment(assignment_id: int, db: Session = Depends(get_db)):
    """Get a specific service assignment."""
    repo = ServiceAssignmentRepository(db)
    assignment = repo.get_by_id(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    return ServiceAssignmentResponse(
        id=assignment.id,
        service_id=assignment.service_id,
        agent_id=assignment.agent_id,
        enabled=assignment.enabled,
        service_name=assignment.service.name,
        agent_name=assignment.agent.hostname if assignment.agent else None,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at
    )


@router.put("/{assignment_id}", response_model=ServiceAssignmentResponse)
def update_assignment(assignment_id: int, assignment_update: ServiceAssignmentUpdate, db: Session = Depends(get_db)):
    """Update a service assignment."""
    assign_repo = ServiceAssignmentRepository(db)
    service_repo = ServiceRepository(db)
    agent_repo = AgentRepository(db)

    existing = assign_repo.get_by_id(assignment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Assignment not found")

    # Verify service if updating
    if assignment_update.service_id is not None:
        service = service_repo.get_by_id(assignment_update.service_id)
        if not service:
            raise HTTPException(status_code=400, detail="Service not found")

    # Verify agent if updating
    if assignment_update.agent_id is not None:
        agent = agent_repo.get_by_id(assignment_update.agent_id)
        if not agent:
            raise HTTPException(status_code=400, detail="Agent not found")

    assignment = assign_repo.update(assignment_id, **assignment_update.model_dump(exclude_unset=True))

    return ServiceAssignmentResponse(
        id=assignment.id,
        service_id=assignment.service_id,
        agent_id=assignment.agent_id,
        enabled=assignment.enabled,
        service_name=assignment.service.name,
        agent_name=assignment.agent.hostname if assignment.agent else None,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at
    )


@router.delete("/{assignment_id}")
def delete_assignment(assignment_id: int, db: Session = Depends(get_db)):
    """Delete a service assignment."""
    repo = ServiceAssignmentRepository(db)
    if not repo.delete(assignment_id):
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"status": "deleted", "assignment_id": assignment_id}


@router.post("/{assignment_id}/enable")
def enable_assignment(assignment_id: int, db: Session = Depends(get_db)):
    """Enable a service assignment."""
    repo = ServiceAssignmentRepository(db)
    assignment = repo.update(assignment_id, enabled=True)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"status": "enabled", "assignment_id": assignment_id}


@router.post("/{assignment_id}/disable")
def disable_assignment(assignment_id: int, db: Session = Depends(get_db)):
    """Disable a service assignment."""
    repo = ServiceAssignmentRepository(db)
    assignment = repo.update(assignment_id, enabled=False)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"status": "disabled", "assignment_id": assignment_id}
