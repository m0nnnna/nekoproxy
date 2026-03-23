"""
Auto-generates self-signed TLS certificates with IP Subject Alternative Names.

Used by the controller and agent to enable encrypted connections without
requiring manual openssl commands or pre-existing certificates.

Trust model: Trust On First Use (TOFU).
  - Each side generates its own cert on first run and reuses it on restart.
  - The controller exposes its public cert at GET /api/v1/agents/controller-cert.
  - Agents download and cache the controller cert on first registration.
  - After that, connections are verified against the cached cert (verify=True).
"""

import ipaddress
import logging
import os
import socket
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def _detect_local_ips() -> List[str]:
    """Return all local IPv4 addresses detected on this machine."""
    ips: set = {"127.0.0.1"}
    # Primary outbound interface IP (doesn't send any traffic)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    # All addresses bound to the hostname
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr:
                ips.add(addr)
    except Exception:
        pass
    return list(ips)


def cert_fingerprint(cert_path: Path) -> str:
    """Return the SHA-256 fingerprint of a PEM certificate (XX:XX:... format)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        raw = cert.fingerprint(hashes.SHA256()).hex()
        return ":".join(raw[i:i + 2].upper() for i in range(0, len(raw), 2))
    except Exception:
        return "(unavailable)"


def ensure_cert(
    cert_path: Path,
    key_path: Path,
    extra_ips: Optional[List[str]] = None,
    extra_hostnames: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Generate a self-signed TLS certificate if one doesn't already exist.

    The certificate includes all detected local IPs plus any extra IPs/hostnames
    as Subject Alternative Names so it works with raw IP addresses.

    Args:
        cert_path: Where to write the PEM certificate.
        key_path:  Where to write the PEM private key (chmod 600).
        extra_ips: Additional IP addresses to add as SANs.
        extra_hostnames: Additional DNS names to add as SANs.

    Returns:
        (generated, fingerprint)
          generated   — True if a new cert was created, False if it already existed.
          fingerprint — SHA-256 fingerprint of the cert (new or existing).
    """
    if cert_path.is_file() and key_path.is_file():
        return False, cert_fingerprint(cert_path)

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        logger.error(
            "Package 'cryptography' is required for auto TLS cert generation. "
            "Run: pip install cryptography"
        )
        return False, ""

    # ── SANs ──────────────────────────────────────────────────────────────────
    all_ips = list({ip for ip in (_detect_local_ips() + (extra_ips or [])) if ip})
    hostname = socket.gethostname()
    all_hostnames = list({"localhost", hostname} | {h for h in (extra_hostnames or []) if h})

    san_entries = []
    for ip in all_ips:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    for h in all_hostnames:
        san_entries.append(x509.DNSName(h))

    # ── Key ───────────────────────────────────────────────────────────────────
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # ── Certificate ───────────────────────────────────────────────────────────
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NekoProxy"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    # ── Write files ───────────────────────────────────────────────────────────
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass

    fp = cert_fingerprint(cert_path)
    logger.info("Generated TLS certificate: %s  SANs: IPs=%s hosts=%s", cert_path, all_ips, all_hostnames)
    return True, fp
