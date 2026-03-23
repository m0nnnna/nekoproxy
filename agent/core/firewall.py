"""Firewall management using iptables."""

import asyncio
import logging
import subprocess
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass

from shared.models import FirewallRuleResponse
from shared.models.common import FirewallAction, Protocol
from agent.config import settings

logger = logging.getLogger(__name__)

# Chain names for NekoProxy
CHAIN_NAME = "NEKOPROXY"
BLOCKLIST_CHAIN_NAME = "NEKOPROXY_BLOCKLIST"
PUBLIC_BASELINE_CHAIN = "NEKOPROXY_PUBLIC"
WG_BASELINE_CHAIN = "NEKOPROXY_WG"

# Ports we never allow on the public interface (even if added as a proxy service)
# Prevents accidental exposure of SSH, RDP, DBs, etc. Use WireGuard for management.
DANGEROUS_PORTS_ON_PUBLIC = frozenset({
    22,     # SSH
    23,     # Telnet
    3389,   # RDP
    5900,   # VNC
    5432,   # PostgreSQL
    27017,  # MongoDB
    6379,   # Redis
    11211,  # Memcached
    3306,   # MySQL
    1433,   # MSSQL
})


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
        self._blocklist_ips: Set[str] = set()  # IPs in blocklist chain (drop by source)
        self._initialized = False
        self._interface_map: Dict[str, str] = {}  # Cache for interface mappings
        self._baseline_jumps_added = False  # Whether INPUT jump rules for PUBLIC/WG are in place
        self._last_baseline_ports: Set[Tuple[int, str]] = set()  # (port, "tcp"|"udp") for PUBLIC chain

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
        """Ensure our custom chains exist. Order: INPUT -> NEKOPROXY_BLOCKLIST (drop by IP) -> NEKOPROXY (port rules)."""
        # Create blocklist chain first (drop by source IP)
        await self._run_iptables("-N", BLOCKLIST_CHAIN_NAME)
        # Create main chain
        await self._run_iptables("-N", CHAIN_NAME)

        # Insert in reverse order so NEKOPROXY_BLOCKLIST is checked first
        for chain in (CHAIN_NAME, BLOCKLIST_CHAIN_NAME):
            proc = await asyncio.create_subprocess_exec(
                "iptables", "-C", "INPUT", "-j", chain,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            if proc.returncode != 0:
                await self._run_iptables("-I", "INPUT", "-j", chain)
                logger.info(f"Added {chain} chain to INPUT")

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
            # Public = interface facing the internet. Prefer default route, but if that's
            # WireGuard (e.g. VPN-as-gateway) use a physical interface instead.
            wg_iface = self._interface_map.get("wireguard")
            if not wg_iface:
                for name in ["wg0", "wg1", "wg-tunnel"]:
                    if await self._interface_exists(name):
                        wg_iface = name
                        break
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ip", "route", "show", "default",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                output = stdout.decode()
                parts = output.split()
                if "dev" in parts:
                    idx = parts.index("dev")
                    if idx + 1 < len(parts):
                        iface = parts[idx + 1]
                        if iface != wg_iface:
                            self._interface_map[interface_type] = iface
                            return iface
            except Exception as e:
                logger.error(f"Error finding default interface: {e}")

            # Fallback: first physical interface that exists and is not WireGuard
            for iface in ["eth0", "ens3", "ens192", "enp0s3", "eno1"]:
                if iface != wg_iface and await self._interface_exists(iface):
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

    async def sync_blocklist_ips(self, ips: List[str]):
        """Sync iptables blocklist chain to drop traffic from these IPs (aggressive firewall)."""
        if not self._initialized:
            await self.initialize()
        if not self._initialized:
            return
        desired = set(ips)
        to_add = desired - self._blocklist_ips
        to_remove = self._blocklist_ips - desired
        for ip in to_remove:
            await self._run_iptables("-D", BLOCKLIST_CHAIN_NAME, "-s", ip, "-j", "DROP")
            self._blocklist_ips.discard(ip)
        for ip in to_add:
            if await self._run_iptables("-A", BLOCKLIST_CHAIN_NAME, "-s", ip, "-j", "DROP"):
                self._blocklist_ips.add(ip)
                logger.info(f"Blocklist DROP rule added for {ip}")
        if to_add or to_remove:
            logger.info(f"Blocklist chain: {len(self._blocklist_ips)} IPs")

    async def add_blocklist_ip(self, ip: str) -> bool:
        """Add a single IP to the blocklist chain (e.g. after auto-block from alert)."""
        if not self._initialized:
            await self.initialize()
        if not self._initialized or ip in self._blocklist_ips:
            return ip in self._blocklist_ips
        if await self._run_iptables("-A", BLOCKLIST_CHAIN_NAME, "-s", ip, "-j", "DROP"):
            self._blocklist_ips.add(ip)
            logger.info(f"Blocklist DROP added for {ip}")
            return True
        return False

    async def apply_baseline(
        self,
        allowed_ports: List[Tuple[int, str]],
        harden_public: bool = True,
        allow_wg: bool = True,
        allow_dangerous_ports_on_public: bool = False,
    ):
        """Apply auto-generated baseline: default-deny on public (only allowed_ports + established),
        allow WireGuard interface. If allow_dangerous_ports_on_public (internal agent), do not filter
        DANGEROUS_PORTS_ON_PUBLIC so all proxy ports are allowed.
        """
        if not self._initialized:
            await self.initialize()
        if not self._initialized:
            return

        public_iface = await self._get_interface_for_type("public")
        wg_iface = await self._get_interface_for_type("wireguard")

        # Create baseline chains if needed
        await self._run_iptables("-N", PUBLIC_BASELINE_CHAIN)
        await self._run_iptables("-N", WG_BASELINE_CHAIN)

        # Add jump rules once (after BLOCKLIST and NEKOPROXY, so at position 3 and 4)
        if not self._baseline_jumps_added:
            pos = 3
            if public_iface and harden_public:
                await self._run_iptables("-I", "INPUT", str(pos), "-i", public_iface, "-j", PUBLIC_BASELINE_CHAIN)
                pos = 4
            if wg_iface and allow_wg:
                await self._run_iptables("-I", "INPUT", str(pos), "-i", wg_iface, "-j", WG_BASELINE_CHAIN)
            self._baseline_jumps_added = True

        # Helper: accept ESTABLISHED,RELATED (conntrack preferred, fallback to state for older kernels)
        async def add_accept_established(chain: str) -> bool:
            if await self._run_iptables("-A", chain, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"):
                return True
            return await self._run_iptables("-A", chain, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT")

        # PUBLIC chain: on public interface, only allow proxy ports + established/related
        await self._run_iptables("-F", PUBLIC_BASELINE_CHAIN)
        if public_iface and harden_public:
            await add_accept_established(PUBLIC_BASELINE_CHAIN)
            if allow_dangerous_ports_on_public:
                safe_ports = list(allowed_ports)
            else:
                safe_ports = [(p, proto) for p, proto in allowed_ports if p not in DANGEROUS_PORTS_ON_PUBLIC]
            for port, proto in safe_ports:
                await self._run_iptables("-A", PUBLIC_BASELINE_CHAIN, "-p", proto, "--dport", str(port), "-j", "ACCEPT")
            # Explicitly drop the ControlAPI management port before the catch-all DROP so it is
            # always blocked on the public interface even if the default-deny is removed or bypassed.
            mgmt_port = settings.api_port
            await self._run_iptables("-A", PUBLIC_BASELINE_CHAIN, "-p", "tcp", "--dport", str(mgmt_port), "-j", "DROP")
            await self._run_iptables("-A", PUBLIC_BASELINE_CHAIN, "-j", "DROP")
            skipped = len(allowed_ports) - len(safe_ports)
            if allow_dangerous_ports_on_public:
                logger.info(f"Public baseline: {public_iface} internal mode, allow all {len(safe_ports)} proxy ports")
            else:
                if skipped:
                    logger.info(f"Public baseline: excluded {skipped} dangerous port(s) from public allow list")
                logger.info(f"Public baseline: {public_iface} default-deny, allow {len(safe_ports)} ports (SSH/DBs never on public)")

        # WG chain: allow WireGuard interface (controller, SSH over WG, etc.)
        await self._run_iptables("-F", WG_BASELINE_CHAIN)
        if wg_iface and allow_wg:
            await add_accept_established(WG_BASELINE_CHAIN)
            await self._run_iptables("-A", WG_BASELINE_CHAIN, "-j", "ACCEPT")
            logger.info(f"WireGuard baseline: {wg_iface} allow")

    async def clear_all_rules(self):
        """Remove all NekoProxy firewall rules and blocklist chain."""
        if not self._initialized:
            return

        await self._run_iptables("-F", BLOCKLIST_CHAIN_NAME)
        self._blocklist_ips.clear()
        await self._run_iptables("-F", CHAIN_NAME)
        self._current_rules.clear()
        if self._baseline_jumps_added:
            for chain in (PUBLIC_BASELINE_CHAIN, WG_BASELINE_CHAIN):
                await self._run_iptables("-F", chain)
            self._baseline_jumps_added = False
        logger.info("Cleared all firewall rules")

    async def shutdown(self):
        """Clean up firewall rules on shutdown."""
        if not self._initialized:
            return
        await self.clear_all_rules()

        # Remove baseline jump rules (we inserted with -i iface -j CHAIN, so -D must match)
        if self._baseline_jumps_added:
            public_iface = await self._get_interface_for_type("public")
            wg_iface = await self._get_interface_for_type("wireguard")
            if public_iface:
                await self._run_iptables("-D", "INPUT", "-i", public_iface, "-j", PUBLIC_BASELINE_CHAIN)
            if wg_iface:
                await self._run_iptables("-D", "INPUT", "-i", wg_iface, "-j", WG_BASELINE_CHAIN)
            self._baseline_jumps_added = False

        for chain in (BLOCKLIST_CHAIN_NAME, CHAIN_NAME):
            await self._run_iptables("-D", "INPUT", "-j", chain)
            await self._run_iptables("-X", chain)
        for chain in (PUBLIC_BASELINE_CHAIN, WG_BASELINE_CHAIN):
            await self._run_iptables("-X", chain)

        logger.info("Firewall manager shutdown complete")
