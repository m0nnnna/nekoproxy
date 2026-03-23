"""Security monitor for detecting brute force attempts and suspicious activity from system logs.
When thresholds are hit, can auto-block the IP locally and report to controller so blocklist
syncs to all agents (firewall-style management).
"""

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, Dict, Set, Callable, Awaitable
from pathlib import Path

import httpx

from agent.config import settings

logger = logging.getLogger(__name__)

# Event types that trigger auto-block (IP banned) in addition to alert
AUTO_BLOCK_EVENT_TYPES = frozenset({
    "ssh_failed",
    "ssh_invalid_user",
    "mail_auth_failed",
    "mail_relay_denied",
    "mail_spam_attempt",
    "port_scan",
})


class SecurityMonitor:
    """Monitors system logs for security events and reports alerts to controller."""

    # Log files to monitor
    LOG_FILES = [
        "/var/log/auth.log",      # SSH, sudo, etc.
        "/var/log/secure",        # Alternative auth log (RHEL/CentOS)
        "/var/log/mail.log",      # Mail authentication
        "/var/log/syslog",        # General system log
    ]

    # Patterns for detecting security events
    PATTERNS = {
        "ssh_failed": [
            # Failed SSH login
            re.compile(r"sshd\[\d+\]: Failed password for (?:invalid user )?(\S+) from (\d+\.\d+\.\d+\.\d+)"),
            re.compile(r"sshd\[\d+\]: Invalid user (\S+) from (\d+\.\d+\.\d+\.\d+)"),
            re.compile(r"sshd\[\d+\]: Connection closed by (?:invalid user )?(\S+)? ?(\d+\.\d+\.\d+\.\d+).*\[preauth\]"),
            re.compile(r"sshd\[\d+\]: Disconnected from (?:invalid user )?(\S+)? ?(\d+\.\d+\.\d+\.\d+).*\[preauth\]"),
        ],
        "ssh_invalid_user": [
            re.compile(r"sshd\[\d+\]: Invalid user (\S+) from (\d+\.\d+\.\d+\.\d+)"),
        ],
        "mail_auth_failed": [
            # Postfix SASL auth failures
            re.compile(r"postfix/smtpd\[\d+\]: warning:.*\[(\d+\.\d+\.\d+\.\d+)\]: SASL (?:LOGIN|PLAIN) authentication failed"),
            re.compile(r"saslauthd\[\d+\]: do_auth.*: auth failure:.*\[(\d+\.\d+\.\d+\.\d+)\]"),
            # Dovecot auth failures
            re.compile(r"dovecot:.*(?:imap|pop3)-login: Aborted login \(auth failed\).*rip=(\d+\.\d+\.\d+\.\d+)"),
        ],
        "mail_relay_denied": [
            # Relay access denied - someone trying to send without auth
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*Relay access denied"),
            re.compile(r"postfix/smtpd\[\d+\]:.*\[(\d+\.\d+\.\d+\.\d+)\].*Relay access denied"),
            # Client host rejected (no auth)
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*Client host rejected"),
            # Sender/recipient verification failures (spammers probing)
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*Sender address rejected"),
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*Recipient address rejected"),
        ],
        "mail_spam_attempt": [
            # Too many connections/rate limiting
            re.compile(r"postfix/smtpd\[\d+\]:.*\[(\d+\.\d+\.\d+\.\d+)\].*too many connections"),
            re.compile(r"postfix/smtpd\[\d+\]:.*\[(\d+\.\d+\.\d+\.\d+)\].*rate limit"),
            # Blocked by policy (RBL, greylisting, etc.)
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*blocked using"),
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*Service unavailable.*blocked"),
            # Helo/hostname rejected
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*Helo command rejected"),
            # Lost connection during auth/data (spam bots often disconnect)
            re.compile(r"postfix/smtpd\[\d+\]: lost connection after AUTH from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+)"),
            re.compile(r"postfix/smtpd\[\d+\]: lost connection after EHLO from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+)"),
        ],
        "mail_rejected": [
            # Generic NOQUEUE rejections (catch-all for other mail rejections)
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+)"),
            # Milter rejections (rspamd, amavis, etc.)
            re.compile(r"postfix/smtpd\[\d+\]:.*milter-reject:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+)"),
            # Non-SMTP command
            re.compile(r"postfix/smtpd\[\d+\]: NOQUEUE:.*from (?:unknown\[)?(\d+\.\d+\.\d+\.\d+).*non-SMTP command"),
        ],
        "connection_refused": [
            # Firewall/iptables blocks
            re.compile(r"kernel:.*DROPPED.*SRC=(\d+\.\d+\.\d+\.\d+).*DPT=(\d+)"),
            re.compile(r"kernel:.*BLOCKED.*SRC=(\d+\.\d+\.\d+\.\d+).*DPT=(\d+)"),
            re.compile(r"UFW BLOCK.*SRC=(\d+\.\d+\.\d+\.\d+).*DPT=(\d+)"),
        ],
        "port_scan": [
            # Multiple port access in short time detected by fail2ban or similar
            re.compile(r"fail2ban.*\[(\d+\.\d+\.\d+\.\d+)\].*banned"),
        ],
    }

    # Thresholds for alerting
    ALERT_THRESHOLDS = {
        "ssh_failed": 5,           # 5 failed SSH attempts = alert
        "ssh_invalid_user": 3,     # 3 invalid user attempts = alert
        "mail_auth_failed": 5,     # 5 mail auth failures = alert
        "mail_relay_denied": 3,    # 3 relay attempts = alert (likely spammer)
        "mail_spam_attempt": 3,    # 3 spam attempts = alert
        "mail_rejected": 5,        # 5 rejected mails = alert
        "connection_refused": 10,  # 10 blocked connections = alert
    }

    # Time window for counting attempts (seconds)
    TIME_WINDOW = 300  # 5 minutes

    # Stricter thresholds for auto-block (block as soon as we hit this)
    AUTO_BLOCK_THRESHOLDS = {
        "ssh_failed": 3,
        "ssh_invalid_user": 2,
        "mail_auth_failed": 3,
        "mail_relay_denied": 2,
        "mail_spam_attempt": 2,
        "mail_rejected": 5,
        "port_scan": 1,
    }

    def __init__(
        self,
        agent_id: int,
        on_auto_block: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
    ):
        self.agent_id = agent_id
        self.on_auto_block = on_auto_block  # async (ip, reason, event_type) -> None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

        # Track file positions for tailing
        self._file_positions: Dict[str, int] = {}

        # Track attempts per IP per event type
        # {event_type: {ip: [(timestamp, details), ...]}}
        self._attempts: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))

        # Track already alerted IPs (to avoid spam)
        # {event_type: {ip: last_alert_time}}
        self._alerted: Dict[str, Dict[str, datetime]] = defaultdict(dict)

        # Track IPs we've already auto-blocked this run (avoid duplicate report)
        self._auto_blocked: Set[str] = set()

        # Cooldown period before re-alerting same IP for same event
        self._alert_cooldown = timedelta(minutes=30)

    async def start(self):
        """Start security monitoring."""
        self._running = True
        ssl_verify = getattr(settings, "controller_ssl_ca_cert", None) or getattr(settings, "controller_ssl_verify", False)
        self._client = httpx.AsyncClient(timeout=10.0, verify=ssl_verify)

        # Initialize file positions to end of files
        for log_file in self.LOG_FILES:
            if Path(log_file).exists():
                try:
                    self._file_positions[log_file] = Path(log_file).stat().st_size
                except Exception:
                    self._file_positions[log_file] = 0

        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Security monitor started")

    async def stop(self):
        """Stop security monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("Security monitor stopped")

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_logs()
                await self._process_attempts()
            except Exception as e:
                logger.error(f"Error in security monitor: {e}")

            await asyncio.sleep(10)  # Check every 10 seconds

    async def _check_logs(self):
        """Check log files for new entries."""
        for log_file in self.LOG_FILES:
            path = Path(log_file)
            if not path.exists():
                continue

            try:
                current_size = path.stat().st_size
                last_pos = self._file_positions.get(log_file, 0)

                # Handle log rotation (file got smaller)
                if current_size < last_pos:
                    last_pos = 0

                if current_size > last_pos:
                    # Read new lines
                    with open(path, 'r', errors='ignore') as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        self._file_positions[log_file] = f.tell()

                    # Process each line
                    for line in new_lines:
                        await self._parse_line(line.strip())

            except PermissionError:
                # Need root to read some logs
                pass
            except Exception as e:
                logger.debug(f"Error reading {log_file}: {e}")

    async def _parse_line(self, line: str):
        """Parse a log line for security events."""
        if not line:
            return

        now = datetime.utcnow()

        for event_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    groups = match.groups()

                    # Extract IP (usually last group, sometimes second)
                    ip = None
                    port = None
                    user = None

                    for g in groups:
                        if g and re.match(r'\d+\.\d+\.\d+\.\d+', g):
                            ip = g
                        elif g and g.isdigit() and len(g) <= 5:
                            port = int(g)
                        elif g and not g.isdigit():
                            user = g

                    if ip:
                        # Skip local/private IPs
                        if ip.startswith('127.') or ip.startswith('10.') or ip.startswith('192.168.'):
                            continue

                        self._attempts[event_type][ip].append({
                            'timestamp': now,
                            'user': user,
                            'port': port,
                            'line': line[:200]  # Truncate long lines
                        })
                    break

    async def _process_attempts(self):
        """Process accumulated attempts and generate alerts."""
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=self.TIME_WINDOW)

        for event_type, ip_attempts in self._attempts.items():
            threshold = self.ALERT_THRESHOLDS.get(event_type, 5)

            for ip, attempts in list(ip_attempts.items()):
                # Remove old attempts outside time window
                recent_attempts = [a for a in attempts if a['timestamp'] > cutoff]
                ip_attempts[ip] = recent_attempts

                if len(recent_attempts) >= threshold:
                    # Check cooldown
                    last_alert = self._alerted[event_type].get(ip)
                    if last_alert and (now - last_alert) < self._alert_cooldown:
                        continue

                    # Auto-block first for aggressive event types (then report to controller)
                    auto_block_threshold = self.AUTO_BLOCK_THRESHOLDS.get(event_type, threshold)
                    if (
                        event_type in AUTO_BLOCK_EVENT_TYPES
                        and len(recent_attempts) >= auto_block_threshold
                        and self.on_auto_block
                        and ip not in self._auto_blocked
                    ):
                        reason = f"Auto-block: {event_type} ({len(recent_attempts)} attempts)"
                        try:
                            await self.on_auto_block(ip, reason, event_type)
                            self._auto_blocked.add(ip)
                        except Exception as e:
                            logger.warning(f"Auto-block callback failed for {ip}: {e}")

                    # Generate alert (so controller UI shows it)
                    await self._send_alert(event_type, ip, recent_attempts)
                    self._alerted[event_type][ip] = now

                    # Clear attempts after alerting
                    ip_attempts[ip] = []

    async def _send_alert(self, event_type: str, source_ip: str, attempts: list):
        """Send an alert to the controller."""
        # Map event types to alert types
        alert_type_map = {
            "ssh_failed": "ssh_brute_force",
            "ssh_invalid_user": "invalid_user",
            "mail_auth_failed": "mail_auth_failure",
            "mail_relay_denied": "mail_relay_denied",
            "mail_spam_attempt": "mail_spam_attempt",
            "mail_rejected": "mail_rejected",
            "connection_refused": "connection_refused",
            "port_scan": "suspicious_scan",
        }

        # Map event types to severity
        severity_map = {
            "ssh_failed": "high",
            "ssh_invalid_user": "medium",
            "mail_auth_failed": "high",
            "mail_relay_denied": "high",      # Likely spammer trying to relay
            "mail_spam_attempt": "critical",  # Active spam attempt
            "mail_rejected": "medium",        # General rejection
            "connection_refused": "low",
            "port_scan": "high",
        }

        alert_type = alert_type_map.get(event_type, "auth_failure")
        severity = severity_map.get(event_type, "medium")

        # Build description
        count = len(attempts)
        users = set(a.get('user') for a in attempts if a.get('user'))
        ports = set(a.get('port') for a in attempts if a.get('port'))

        description = f"{count} {event_type.replace('_', ' ')} attempts detected"
        if users:
            description += f" (users: {', '.join(users)})"
        if ports:
            description += f" (ports: {', '.join(str(p) for p in ports)})"

        # Get sample port for alert
        port = next(iter(ports), None)

        payload = {
            "alert_type": alert_type,
            "severity": severity,
            "source_ip": source_ip,
            "description": description,
            "port": port,
            "interface": "system",
            "agent_id": self.agent_id
        }

        url = f"{settings.controller_url}/api/v1/alerts"

        try:
            _headers = {"X-Agent-Token": settings.agent_token} if settings.agent_token else {}
            response = await self._client.post(url, json=payload, headers=_headers)
            response.raise_for_status()
            logger.info(f"Security alert sent: {alert_type} from {source_ip} ({count} attempts)")
        except Exception as e:
            logger.warning(f"Failed to send security alert: {e}")

        # Record security events in connection stats
        await self._record_security_stats(event_type, source_ip, attempts)

    async def _record_security_stats(self, event_type: str, source_ip: str, attempts: list):
        """Record security events in the stats system."""
        # Map event types to status strings for connection log
        status_map = {
            "ssh_failed": "blocked",
            "ssh_invalid_user": "blocked",
            "mail_auth_failed": "blocked",
            "mail_relay_denied": "blocked",
            "mail_spam_attempt": "blocked",
            "mail_rejected": "blocked",
            "connection_refused": "blocked",
            "port_scan": "blocked",
        }

        status = status_map.get(event_type, "blocked")

        # Build connection stats for each attempt
        connections = []
        for attempt in attempts:
            connections.append({
                "service_id": None,
                "client_ip": source_ip,
                "status": status,
                "duration": 0,
                "bytes_sent": 0,
                "bytes_received": 0,
                "timestamp": attempt['timestamp'].isoformat()
            })

        if not connections:
            return

        url = f"{settings.controller_url}/api/v1/stats/connections"
        payload = {
            "agent_id": self.agent_id,
            "connections": connections
        }

        try:
            _headers = {"X-Agent-Token": settings.agent_token} if settings.agent_token else {}
            response = await self._client.post(url, json=payload, headers=_headers)
            response.raise_for_status()
            logger.debug(f"Recorded {len(connections)} {event_type} events in stats")
        except Exception as e:
            logger.warning(f"Failed to record security stats: {e}")
