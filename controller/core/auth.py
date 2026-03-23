"""Authentication utilities for the NekoProxy controller.

Two token types:
  - API token  : single shared secret for admin API access and web UI login
  - Agent token: per-agent token returned at registration, required on all agent→controller calls
  - Controller token: token the controller sends when calling agents' ControlAPI

The API token is loaded from:
  1. NEKO_API_TOKEN environment variable
  2. GlobalSettings.api_token in the database (auto-generated on first run)

Usage
-----
FastAPI dependencies:
  - require_api_token   → admin/management endpoints
  - require_agent_token → agent-facing endpoints (heartbeat, config, stats, blocklist/report, alerts)
  - require_any_token   → accepts either admin or agent token

Web session helpers:
  - create_session / validate_session / invalidate_session
"""

import hmac
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Header, HTTPException, Depends, Request
from sqlalchemy.orm import Session

from controller.database.database import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session store  {token: expiry_datetime}
# ---------------------------------------------------------------------------
_sessions: dict[str, datetime] = {}
SESSION_TTL_HOURS = 8


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------

def generate_token() -> str:
    """Generate a cryptographically secure random token (256-bit URL-safe)."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Session management (web UI)
# ---------------------------------------------------------------------------

def create_session() -> str:
    """Create a new web session token; returns the token value."""
    _cleanup_sessions()
    token = generate_token()
    _sessions[token] = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)
    return token


def validate_session(token: Optional[str]) -> bool:
    """Return True if the session token is valid and not expired."""
    if not token or token not in _sessions:
        return False
    if _sessions[token] < datetime.utcnow():
        del _sessions[token]
        return False
    return True


def invalidate_session(token: Optional[str]) -> None:
    """Invalidate a session token (logout)."""
    if token:
        _sessions.pop(token, None)


def _cleanup_sessions() -> None:
    """Remove expired sessions (called lazily on session creation)."""
    now = datetime.utcnow()
    expired = [k for k, v in _sessions.items() if v < now]
    for k in expired:
        del _sessions[k]


# ---------------------------------------------------------------------------
# Token lookup helpers (avoid circular imports: only import DB inside functions)
# ---------------------------------------------------------------------------

def _get_active_api_token(db: Session) -> Optional[str]:
    """Return the active API token: env setting takes precedence over DB."""
    from controller.config import settings
    if settings.api_token:
        return settings.api_token
    from controller.database.repositories import GlobalSettingsRepository
    gs = GlobalSettingsRepository(db).get()
    return gs.api_token if gs else None


def _get_agent_token(agent_id: int, db: Session) -> Optional[str]:
    """Return the stored token for a given agent."""
    from controller.database.repositories import AgentRepository
    agent = AgentRepository(db).get_by_id(agent_id)
    return agent.agent_token if agent else None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def require_api_token(
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Dependency: require valid admin API token.

    Accepts the token via:
      - X-API-Token header
      - Authorization: Bearer <token> header
    """
    token = x_api_token
    if not token and authorization:
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()

    active = _get_active_api_token(db)
    if not active:
        # No token configured yet - block access until initialised
        raise HTTPException(status_code=503, detail="API token not yet initialised; restart controller")

    if not token or not hmac.compare_digest(token, active):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


def require_agent_token(
    agent_id: int,
    x_agent_token: Optional[str] = Header(default=None, alias="X-Agent-Token"),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Dependency: require either a valid per-agent token or admin API token.

    Per-agent token is checked first.  If the admin token is provided and
    correct the request is also allowed (enables admin tools to call agent
    endpoints directly).

    NOTE: agent_id must be extracted from the path before calling this.
    See require_agent_token_for_id() below for inline use.
    """
    # Try admin token first
    admin_token = x_api_token
    if not admin_token and authorization and authorization.lower().startswith("bearer "):
        admin_token = authorization[7:].strip()

    active_admin = _get_active_api_token(db)
    if active_admin and admin_token and hmac.compare_digest(admin_token, active_admin):
        return  # Admin token accepted

    # Try per-agent token
    if x_agent_token:
        stored = _get_agent_token(agent_id, db)
        if stored and hmac.compare_digest(x_agent_token, stored):
            return  # Agent token accepted

    raise HTTPException(status_code=401, detail="Invalid or missing agent token")


def get_web_password(db: Session) -> str:
    """Return the web UI password (API token is used as web password)."""
    return _get_active_api_token(db) or ""
