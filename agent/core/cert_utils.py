"""Shared TLS certificate recovery helpers for the agent.

Used by heartbeat, config_sync, and the ControlAPI /regen-cert endpoint
to clear a stale cached controller cert and re-download it (re-TOFU).
"""

import logging
from pathlib import Path
from typing import Optional

import httpx

from agent.config import settings

logger = logging.getLogger(__name__)


async def clear_and_retofu() -> Optional[str]:
    """Clear the cached controller cert and re-download via TOFU.

    Deletes controller-ca.pem from the install dir, resets
    settings.controller_ssl_ca_cert, then fetches the current
    cert from the controller with verify=False.

    Returns the new cert path string on success, None on failure.
    """
    if not settings.controller_url.startswith("https://"):
        return None

    cert_path = settings.install_dir_resolved / "controller-ca.pem"
    try:
        if cert_path.is_file():
            cert_path.unlink()
    except Exception as e:
        logger.warning("Could not delete stale cert %s: %s", cert_path, e)

    settings.controller_ssl_ca_cert = None

    url = f"{settings.controller_url}/api/v1/agents/controller-cert"
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            r = await client.get(url)
            r.raise_for_status()
            cert_path.write_text(r.text, encoding="utf-8")
            settings.controller_ssl_ca_cert = str(cert_path)
            try:
                from shared.tls import cert_fingerprint
                fp = cert_fingerprint(cert_path)
                logger.warning("Re-TOFU complete — new controller cert SHA-256: %s", fp)
            except Exception:
                pass
            return str(cert_path)
    except Exception as e:
        logger.warning("Re-TOFU failed: %s", e)
        return None


def is_ssl_error(exc: Exception) -> bool:
    """Return True if the exception looks like an SSL certificate verification failure."""
    msg = str(exc).lower()
    return any(k in msg for k in ("ssl", "certificate", "cert", "tls", "handshake"))
