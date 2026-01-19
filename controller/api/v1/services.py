from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from controller.database.database import get_db
from controller.database.repositories import ServiceRepository
from shared.models import ServiceCreate, ServiceUpdate, ServiceResponse

router = APIRouter()


@router.post("", response_model=ServiceResponse, status_code=201)
def create_service(service: ServiceCreate, db: Session = Depends(get_db)):
    """Create a new service definition."""
    repo = ServiceRepository(db)

    # Check for duplicate name
    existing = repo.get_by_name(service.name)
    if existing:
        raise HTTPException(status_code=400, detail="Service with this name already exists")

    # Check for listen port conflict
    existing_port = repo.get_by_listen_port(service.listen_port, service.protocol)
    if existing_port:
        raise HTTPException(
            status_code=400,
            detail=f"Listen port {service.listen_port}/{service.protocol.value} already in use by service '{existing_port.name}'"
        )

    created = repo.create(
        name=service.name,
        description=service.description,
        listen_port=service.listen_port,
        backend_host=service.backend_host,
        backend_port=service.backend_port,
        protocol=service.protocol
    )
    return ServiceResponse(
        id=created.id,
        name=created.name,
        description=created.description,
        listen_port=created.listen_port,
        backend_host=created.backend_host,
        backend_port=created.backend_port,
        protocol=created.protocol,
        created_at=created.created_at,
        updated_at=created.updated_at
    )


@router.get("", response_model=list[ServiceResponse])
def list_services(db: Session = Depends(get_db)):
    """List all service definitions."""
    repo = ServiceRepository(db)
    services = repo.get_all()
    return [
        ServiceResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            listen_port=s.listen_port,
            backend_host=s.backend_host,
            backend_port=s.backend_port,
            protocol=s.protocol,
            created_at=s.created_at,
            updated_at=s.updated_at
        )
        for s in services
    ]


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(service_id: int, db: Session = Depends(get_db)):
    """Get a specific service."""
    repo = ServiceRepository(db)
    service = repo.get_by_id(service_id)
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")
    return ServiceResponse(
        id=service.id,
        name=service.name,
        description=service.description,
        listen_port=service.listen_port,
        backend_host=service.backend_host,
        backend_port=service.backend_port,
        protocol=service.protocol,
        created_at=service.created_at,
        updated_at=service.updated_at
    )


@router.put("/{service_id}", response_model=ServiceResponse)
def update_service(service_id: int, service_update: ServiceUpdate, db: Session = Depends(get_db)):
    """Update a service definition."""
    repo = ServiceRepository(db)

    existing = repo.get_by_id(service_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Service not found")

    # Check name uniqueness if updating name
    if service_update.name:
        name_exists = repo.get_by_name(service_update.name)
        if name_exists and name_exists.id != service_id:
            raise HTTPException(status_code=400, detail="Service with this name already exists")

    # Check listen port conflict if updating port or protocol
    if service_update.listen_port is not None or service_update.protocol is not None:
        new_port = service_update.listen_port if service_update.listen_port is not None else existing.listen_port
        new_protocol = service_update.protocol if service_update.protocol is not None else existing.protocol
        port_conflict = repo.get_by_listen_port(new_port, new_protocol)
        if port_conflict and port_conflict.id != service_id:
            raise HTTPException(
                status_code=400,
                detail=f"Listen port {new_port}/{new_protocol.value} already in use by service '{port_conflict.name}'"
            )

    service = repo.update(service_id, **service_update.model_dump(exclude_unset=True))

    return ServiceResponse(
        id=service.id,
        name=service.name,
        description=service.description,
        listen_port=service.listen_port,
        backend_host=service.backend_host,
        backend_port=service.backend_port,
        protocol=service.protocol,
        created_at=service.created_at,
        updated_at=service.updated_at
    )


@router.delete("/{service_id}")
def delete_service(service_id: int, db: Session = Depends(get_db)):
    """Delete a service and its assignments."""
    repo = ServiceRepository(db)
    if not repo.delete(service_id):
        raise HTTPException(status_code=404, detail="Service not found")
    return {"status": "deleted", "service_id": service_id}
