"""Firewall management using iptables."""

import asyncio
import logging
import subprocess
from typing import List, Dict, Set, Optional
from dataclasses import dataclass

from shared.models import FirewallRuleResponse
from shared.models.common import FirewallAction, Protocol

logger = logging.getLogger(__name__)

# Chain name for NekoProxy rules
CHAIN_NAME = "NEKOPROXY"


@dataclass
class FirewallRuleKey:
    """Unique identifier for a firewall rule."""
    port: int
    protocol: str
    interface: str
    action: str

    def __hash__(self):
        return hash((self.port, self.protocol, self.interface, self.action))

    def __eq__(self, other):
        if not isinstance(other, FirewallRuleKey):
            return False
        return (self.port == other.port and
                self.protocol == other.protocol and
                self.interface == other.interface and
                self.action == other.action)


class FirewallManager:
    """Manages iptables rules for the agent."""

    def __init__(self):
        self._current_rules: Set[FirewallRuleKey] = set()
        self._initialized = False
        self._interface_map: Dict[str, str] = {}  # Cache for interface mappings

    async def initialize(self):
        """Initialize the firewall chain."""
        if self._initialized:
            return

        # Check if iptables is available
        if not await self._check_iptables():
            logger.error("iptables not available - firewall rules will not be applied")
            return

        # Create custom chain if it doesn't exist
        await self._ensure_chain_exists()
        self._initialized = True
        logger.info("Firewall manager initialized")

    async def _check_iptables(self) -> bool:
        """Check if iptables is available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "iptables", "-V",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.error(f"Error checking iptables: {e}")
            return False

    async def _run_iptables(self, *args) -> bool:
        """Run an iptables command."""
        cmd = ["iptables"] + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                stderr_str = stderr.decode().strip()
                # Ignore "already exists" errors
                if "already exists" not in stderr_str and "Chain already exists" not in stderr_str:
                    logger.warning(f"iptables command failed: {' '.join(cmd)}: {stderr_str}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error running iptables: {e}")
            return False

    async def _ensure_chain_exists(self):
        """Ensure our custom chain exists and is linked to INPUT."""
        # Create chain (ignore if exists)
        await self._run_iptables("-N", CHAIN_NAME)

        # Check if jump rule exists in INPUT
        proc = await asyncio.create_subprocess_exec(
            "iptables", "-C", "INPUT", "-j", CHAIN_NAME,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        if proc.returncode != 0:
            # Add jump rule
            await self._run_iptables("-I", "INPUT", "-j", CHAIN_NAME)
            logger.info(f"Added {CHAIN_NAME} chain to INPUT")

    async def _get_interface_for_type(self, interface_type: str) -> Optional[str]:
        """Get the actual interface name for a type like 'public' or 'wireguard'."""
        # Use cached value if available
        if interface_type in self._interface_map:
            return self._interface_map[interface_type]

        if interface_type == "wireguard":
            # Try common WireGuard interface names
            for iface in ["wg0", "wg1", "wg-tunnel"]:
                if await self._interface_exists(iface):
                    self._interface_map[interface_type] = iface
                    return iface
            return None

        elif interface_type == "public":
            # Try to find the default interface (the one with the default route)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ip", "route", "show", "default",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                output = stdout.decode()
                # Parse "default via X.X.X.X dev ethX" format
                parts = output.split()
                if "dev" in parts:
                    idx = parts.index("dev")
                    if idx + 1 < len(parts):
                        iface = parts[idx + 1]
                        self._interface_map[interface_type] = iface
                        return iface
            except Exception as e:
                logger.error(f"Error finding default interface: {e}")

            # Fallback to common interface names
            for iface in ["eth0", "ens3", "ens192", "enp0s3", "eno1"]:
                if await self._interface_exists(iface):
                    self._interface_map[interface_type] = iface
                    return iface
            return None

        else:
            # Assume it's a direct interface name
            if await self._interface_exists(interface_type):
                return interface_type
            return None

    async def _interface_exists(self, iface: str) -> bool:
        """Check if an interface exists."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "link", "show", iface,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False

    def _rule_to_key(self, rule: FirewallRuleResponse) -> FirewallRuleKey:
        """Convert a rule to a unique key."""
        return FirewallRuleKey(
            port=rule.port,
            protocol=rule.protocol.value,
            interface=rule.interface,
            action=rule.action.value
        )

    async def _add_rule(self, rule: FirewallRuleResponse, interface: str):
        """Add a single iptables rule."""
        protocol = rule.protocol.value  # tcp or udp
        action = "DROP" if rule.action == FirewallAction.BLOCK else "ACCEPT"

        # Build the rule
        args = [
            "-A", CHAIN_NAME,
            "-i", interface,
            "-p", protocol,
            "--dport", str(rule.port),
            "-j", action
        ]

        if await self._run_iptables(*args):
            logger.info(f"Added firewall rule: {action} {protocol}/{rule.port} on {interface}")
            return True
        return False

    async def _remove_rule(self, rule_key: FirewallRuleKey, interface: str):
        """Remove a single iptables rule."""
        action = "DROP" if rule_key.action == "block" else "ACCEPT"

        args = [
            "-D", CHAIN_NAME,
            "-i", interface,
            "-p", rule_key.protocol,
            "--dport", str(rule_key.port),
            "-j", action
        ]

        if await self._run_iptables(*args):
            logger.info(f"Removed firewall rule: {action} {rule_key.protocol}/{rule_key.port} on {interface}")
            return True
        return False

    async def sync_rules(self, rules: List[FirewallRuleResponse]):
        """Synchronize firewall rules with the provided list."""
        if not self._initialized:
            await self.initialize()

        if not self._initialized:
            logger.warning("Firewall not initialized, skipping rule sync")
            return

        # Build set of desired rules
        desired_rules: Dict[FirewallRuleKey, tuple] = {}  # key -> (rule, resolved_interface)

        for rule in rules:
            if not rule.enabled:
                continue

            # Resolve interface type to actual interface
            interface = await self._get_interface_for_type(rule.interface)
            if not interface:
                logger.warning(f"Cannot resolve interface '{rule.interface}' for rule on port {rule.port}")
                continue

            key = self._rule_to_key(rule)
            desired_rules[key] = (rule, interface)

        desired_keys = set(desired_rules.keys())

        # Remove rules that are no longer needed
        for key in list(self._current_rules):
            if key not in desired_keys:
                interface = await self._get_interface_for_type(key.interface)
                if interface:
                    await self._remove_rule(key, interface)
                self._current_rules.discard(key)

        # Add new rules
        for key, (rule, interface) in desired_rules.items():
            if key not in self._current_rules:
                if await self._add_rule(rule, interface):
                    self._current_rules.add(key)

        logger.info(f"Firewall rules synced: {len(self._current_rules)} active rules")

    async def clear_all_rules(self):
        """Remove all NekoProxy firewall rules."""
        if not self._initialized:
            return

        # Flush our chain
        await self._run_iptables("-F", CHAIN_NAME)
        self._current_rules.clear()
        logger.info("Cleared all firewall rules")

    async def shutdown(self):
        """Clean up firewall rules on shutdown."""
        await self.clear_all_rules()

        # Remove jump rule from INPUT
        await self._run_iptables("-D", "INPUT", "-j", CHAIN_NAME)

        # Delete our chain
        await self._run_iptables("-X", CHAIN_NAME)

        logger.info("Firewall manager shutdown complete")
