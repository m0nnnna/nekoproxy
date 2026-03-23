"""iptables connection counter monitor - reads per-rule packet/byte counters from NEKOPROXY chain."""

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional, Dict
from collections import deque

import httpx

from agent.config import settings

logger = logging.getLogger(__name__)

# Chain name must match firewall.py
CHAIN_NAME = "NEKOPROXY"


class IptablesMonitor:
    """Monitors iptables NEKOPROXY chain counters and reports deltas to controller."""

    # Regex to parse iptables -L NEKOPROXY -v -n -x output
    # Example line:
    #   1234  567890 DROP  tcp  --  eth0  *  0.0.0.0/0  0.0.0.0/0  tcp dpt:8080
    RULE_PATTERN = re.compile(
        r'^\s*(\d+)\s+(\d+)\s+(ACCEPT|DROP)\s+(tcp|udp)\s+--\s+(\S+)\s+\*\s+\S+\s+\S+\s+(?:tcp|udp)\s+dpt:(\d+)',
        re.MULTILINE
    )

    def __init__(self, agent_id: int):
        self.agent_id = agent_id
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._stats_queue: deque = deque(maxlen=10000)

        # Previous counters for computing deltas
        # Key: (protocol, interface, port, action) -> (packets, bytes)
        self._previous_counters: Dict[tuple, tuple] = {}

    async def start(self):
        """Start the iptables monitor."""
        # Verify iptables is available and chain exists
        if not await self._check_chain_exists():
            logger.info("NEKOPROXY chain not found yet - iptables monitoring will start when rules are applied")
            # Still start the loop - it will retry checking for the chain
            self._running = True
            ssl_verify = getattr(settings, "controller_ssl_ca_cert", None) or getattr(settings, "controller_ssl_verify", False)
            self._client = httpx.AsyncClient(timeout=10.0, verify=ssl_verify)
            self._task = asyncio.create_task(self._monitor_loop())
            return

        self._running = True
        ssl_verify = getattr(settings, "controller_ssl_ca_cert", None) or getattr(settings, "controller_ssl_verify", False)
        self._client = httpx.AsyncClient(timeout=10.0, verify=ssl_verify)

        # Read initial counters as baseline (don't report these)
        await self._read_counters(initial=True)

        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"iptables monitor started (interval: {settings.stats_report_interval}s)")

    async def stop(self):
        """Stop the iptables monitor."""
        self._running = False

        # Flush remaining stats
        await self._send_batch()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("iptables monitor stopped")

    async def _check_chain_exists(self) -> bool:
        """Check if the NEKOPROXY chain exists."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "iptables", "-L", CHAIN_NAME, "-n",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            return False
        except Exception:
            return False

    async def _monitor_loop(self):
        """Main monitoring loop."""
        chain_initialized = bool(self._previous_counters)

        while self._running:
            await asyncio.sleep(settings.stats_report_interval)
            try:
                # If chain wasn't available at start, check again
                if not chain_initialized:
                    if await self._check_chain_exists():
                        await self._read_counters(initial=True)
                        chain_initialized = True
                        logger.info("NEKOPROXY chain detected - iptables monitoring active")
                    continue

                await self._read_counters(initial=False)
                await self._send_batch()
            except Exception as e:
                logger.error(f"Error in iptables monitor: {e}")

    async def _read_counters(self, initial: bool = False):
        """Read iptables counters and compute deltas."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "iptables", "-L", CHAIN_NAME, "-v", "-n", "-x",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.warning(f"Failed to read iptables counters: {stderr.decode().strip()}")
                return

            output = stdout.decode()
            current_counters = self._parse_output(output)

            if not initial:
                # Compute deltas
                now = datetime.utcnow().isoformat()
                for key, (packets, bytes_count) in current_counters.items():
                    prev_packets, prev_bytes = self._previous_counters.get(key, (0, 0))

                    delta_packets = packets - prev_packets
                    delta_bytes = bytes_count - prev_bytes

                    # Handle counter reset (chain recreation during sync_rules)
                    if delta_packets < 0:
                        delta_packets = packets
                    if delta_bytes < 0:
                        delta_bytes = bytes_count

                    # Only report if there's activity
                    if delta_packets > 0 or delta_bytes > 0:
                        protocol, interface, port, action = key
                        self._stats_queue.append({
                            "port": port,
                            "protocol": protocol,
                            "interface": interface,
                            "action": "block" if action == "DROP" else "allow",
                            "packets": delta_packets,
                            "bytes": delta_bytes,
                            "timestamp": now
                        })

            # Store current as previous for next delta
            self._previous_counters = current_counters

        except FileNotFoundError:
            logger.debug("iptables not available")
        except Exception as e:
            logger.error(f"Error reading iptables counters: {e}")

    def _parse_output(self, output: str) -> Dict[tuple, tuple]:
        """Parse iptables -L NEKOPROXY -v -n -x output into counters dict."""
        counters = {}
        for match in self.RULE_PATTERN.finditer(output):
            packets = int(match.group(1))
            bytes_count = int(match.group(2))
            action = match.group(3)    # DROP or ACCEPT
            protocol = match.group(4)  # tcp or udp
            interface = match.group(5) # eth0, wg0, etc.
            port = int(match.group(6))

            key = (protocol, interface, port, action)
            counters[key] = (packets, bytes_count)

        return counters

    async def _send_batch(self):
        """Send a batch of firewall stats to controller."""
        if not self._stats_queue:
            return

        batch = []
        while self._stats_queue and len(batch) < settings.stats_batch_size:
            batch.append(self._stats_queue.popleft())

        if not batch:
            return

        url = f"{settings.controller_url}/api/v1/stats/firewall"
        payload = {
            "agent_id": self.agent_id,
            "rules": batch
        }

        try:
            _headers = {"X-Agent-Token": settings.agent_token} if settings.agent_token else {}
            response = await self._client.post(url, json=payload, headers=_headers)
            response.raise_for_status()
            logger.info(f"Reported {len(batch)} firewall stats to controller")
        except httpx.RequestError as e:
            # Put stats back in queue for retry
            for stat in reversed(batch):
                self._stats_queue.appendleft(stat)
            logger.warning(f"Failed to report firewall stats (will retry): {e}")
        except Exception as e:
            logger.error(f"Error reporting firewall stats: {e}")
