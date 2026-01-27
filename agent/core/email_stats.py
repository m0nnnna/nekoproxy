"""Email stats collector - parses Postfix mail logs to collect email statistics."""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Optional, Dict, List
from collections import deque
from dataclasses import dataclass, field

import httpx

from agent.config import settings

logger = logging.getLogger(__name__)

# Postfix log patterns
POSTFIX_PATTERNS = {
    # Message queued: postfix/qmgr[1234]: ABC123: from=<sender@example.com>, size=1234, nrcpt=1
    'queued': re.compile(
        r'postfix/qmgr\[\d+\]: ([A-Z0-9]+): from=<([^>]*)>, size=(\d+), nrcpt=(\d+)'
    ),
    # Message sent: postfix/smtp[1234]: ABC123: to=<recipient@example.com>, relay=..., status=sent
    'sent': re.compile(
        r'postfix/smtp\[\d+\]: ([A-Z0-9]+): to=<([^>]*)>.*status=(\w+)'
    ),
    # Message deferred: postfix/smtp[1234]: ABC123: to=<recipient@example.com>, status=deferred
    'deferred': re.compile(
        r'postfix/smtp\[\d+\]: ([A-Z0-9]+): to=<([^>]*)>.*status=deferred'
    ),
    # Message bounced: postfix/bounce[1234]: ABC123: sender non-delivery notification
    'bounced': re.compile(
        r'postfix/bounce\[\d+\]: ([A-Z0-9]+):'
    ),
    # SASL auth: postfix/smtpd[1234]: ABC123: client=...[1.2.3.4], sasl_method=..., sasl_username=...
    'sasl_auth': re.compile(
        r'postfix/smtpd\[\d+\]: ([A-Z0-9]+): client=.*\[(\d+\.\d+\.\d+\.\d+)\].*sasl_username=(\S+)'
    ),
    # Reject: postfix/smtpd[1234]: NOQUEUE: reject: ... from ...[1.2.3.4]
    'reject': re.compile(
        r'postfix/smtpd\[\d+\]: NOQUEUE: reject:.*from.*\[(\d+\.\d+\.\d+\.\d+)\]'
    ),
    # Client connection: postfix/smtpd[1234]: connect from ...[1.2.3.4]
    'connect': re.compile(
        r'postfix/smtpd\[\d+\]: connect from.*\[(\d+\.\d+\.\d+\.\d+)\]'
    ),
}


@dataclass
class EmailMessage:
    """Tracks a single email message through the system."""
    queue_id: str
    sender: str = ""
    recipient: str = ""
    client_ip: str = "unknown"
    size: int = 0
    status: str = "queued"
    timestamp: datetime = field(default_factory=datetime.utcnow)


class EmailStatsCollector:
    """Collects email statistics from Postfix mail logs."""

    def __init__(self, agent_id: int, mail_log_path: str = "/var/log/mail.log"):
        self.agent_id = agent_id
        self.mail_log_path = mail_log_path
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._stats_queue: deque = deque(maxlen=10000)

        # Track in-flight messages by queue ID
        self._messages: Dict[str, EmailMessage] = {}

        # Track file position for incremental reading
        self._file_position: int = 0
        self._file_inode: int = 0

    async def start(self):
        """Start the email stats collector."""
        if not os.path.exists(self.mail_log_path):
            logger.warning(f"Mail log not found at {self.mail_log_path} - email stats disabled")
            return

        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)

        # Start at end of file to only process new entries
        try:
            stat = os.stat(self.mail_log_path)
            self._file_position = stat.st_size
            self._file_inode = stat.st_ino
        except Exception as e:
            logger.warning(f"Could not stat mail log: {e}")
            self._file_position = 0

        self._task = asyncio.create_task(self._collect_loop())
        logger.info(f"Email stats collector started (watching {self.mail_log_path})")

    async def stop(self):
        """Stop the email stats collector."""
        self._running = False

        # Send any remaining stats
        await self._send_batch()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("Email stats collector stopped")

    async def _collect_loop(self):
        """Main collection loop - reads mail log and processes entries."""
        while self._running:
            try:
                await self._read_new_log_entries()
                await self._send_batch()
            except Exception as e:
                logger.error(f"Error in email stats collection: {e}")

            await asyncio.sleep(settings.stats_report_interval)

    async def _read_new_log_entries(self):
        """Read new entries from the mail log."""
        if not os.path.exists(self.mail_log_path):
            return

        try:
            # Check if log was rotated (inode changed)
            stat = os.stat(self.mail_log_path)
            if stat.st_ino != self._file_inode:
                logger.info("Mail log rotated, resetting position")
                self._file_position = 0
                self._file_inode = stat.st_ino

            # Check if file was truncated
            if stat.st_size < self._file_position:
                logger.info("Mail log truncated, resetting position")
                self._file_position = 0

            # Read new entries
            with open(self.mail_log_path, 'r') as f:
                f.seek(self._file_position)
                for line in f:
                    self._process_log_line(line.strip())
                self._file_position = f.tell()

        except Exception as e:
            logger.error(f"Error reading mail log: {e}")

    def _process_log_line(self, line: str):
        """Process a single log line and extract email statistics."""
        if not line or 'postfix' not in line:
            return

        # Check for SASL authentication (gives us client IP)
        match = POSTFIX_PATTERNS['sasl_auth'].search(line)
        if match:
            queue_id, client_ip, username = match.groups()
            if queue_id not in self._messages:
                self._messages[queue_id] = EmailMessage(queue_id=queue_id)
            self._messages[queue_id].client_ip = client_ip
            return

        # Check for message queued (gives us sender and size)
        match = POSTFIX_PATTERNS['queued'].search(line)
        if match:
            queue_id, sender, size, nrcpt = match.groups()
            if queue_id not in self._messages:
                self._messages[queue_id] = EmailMessage(queue_id=queue_id)
            self._messages[queue_id].sender = sender
            self._messages[queue_id].size = int(size)
            return

        # Check for message sent (final status)
        match = POSTFIX_PATTERNS['sent'].search(line)
        if match:
            queue_id, recipient, status = match.groups()
            if queue_id in self._messages:
                msg = self._messages[queue_id]
                msg.recipient = recipient
                msg.status = "delivered" if status == "sent" else status
                self._finalize_message(queue_id)
            return

        # Check for deferred
        match = POSTFIX_PATTERNS['deferred'].search(line)
        if match:
            queue_id, recipient = match.groups()
            if queue_id in self._messages:
                msg = self._messages[queue_id]
                msg.recipient = recipient
                msg.status = "deferred"
                # Don't finalize deferred - will be retried
            return

        # Check for bounced
        match = POSTFIX_PATTERNS['bounced'].search(line)
        if match:
            queue_id = match.group(1)
            if queue_id in self._messages:
                self._messages[queue_id].status = "bounced"
                self._finalize_message(queue_id)
            return

        # Check for rejected (no queue ID)
        match = POSTFIX_PATTERNS['reject'].search(line)
        if match:
            client_ip = match.group(1)
            # Create a stat entry for rejected connection
            self._stats_queue.append({
                "client_ip": client_ip,
                "sender": None,
                "recipient": None,
                "status": "blocked",
                "bytes_sent": 0,
                "bytes_received": 0,
                "message_id": None,
                "timestamp": datetime.utcnow().isoformat()
            })
            return

    def _finalize_message(self, queue_id: str):
        """Finalize a message and add to stats queue."""
        if queue_id not in self._messages:
            return

        msg = self._messages.pop(queue_id)

        self._stats_queue.append({
            "client_ip": msg.client_ip,
            "sender": msg.sender or None,
            "recipient": msg.recipient or None,
            "status": msg.status,
            "bytes_sent": msg.size,
            "bytes_received": msg.size,  # Approximate
            "message_id": msg.queue_id,
            "timestamp": msg.timestamp.isoformat()
        })

        logger.debug(f"Email stat recorded: {msg.sender} -> {msg.recipient} ({msg.status})")

    async def _send_batch(self):
        """Send a batch of email stats to controller."""
        if not self._stats_queue:
            return

        # Collect batch
        batch = []
        while self._stats_queue and len(batch) < settings.stats_batch_size:
            batch.append(self._stats_queue.popleft())

        if not batch:
            return

        url = f"{settings.controller_url}/api/v1/stats/email"
        payload = {
            "agent_id": self.agent_id,
            "emails": batch
        }

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            logger.info(f"Reported {len(batch)} email stats to controller")
        except httpx.RequestError as e:
            # Put stats back in queue
            for stat in reversed(batch):
                self._stats_queue.appendleft(stat)
            logger.warning(f"Failed to report email stats (will retry): {e}")
        except Exception as e:
            logger.error(f"Error reporting email stats: {e}")

    def cleanup_stale_messages(self, max_age_seconds: int = 3600):
        """Clean up messages that have been pending too long."""
        now = datetime.utcnow()
        stale_ids = []

        for queue_id, msg in self._messages.items():
            age = (now - msg.timestamp).total_seconds()
            if age > max_age_seconds:
                stale_ids.append(queue_id)

        for queue_id in stale_ids:
            msg = self._messages.pop(queue_id)
            msg.status = "unknown"
            self._finalize_message(queue_id)

        if stale_ids:
            logger.debug(f"Cleaned up {len(stale_ids)} stale email messages")
