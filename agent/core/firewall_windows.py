"""Windows Firewall management using PowerShell NetSecurity module.

Applies blocklist (block by remote IP), controller port rules, and baseline
(allow only proxy ports on Public profile). Requires elevated (admin) rights.
"""

import asyncio
import logging
from typing import List, Set, Tuple
from dataclasses import dataclass

from shared.models import FirewallRuleResponse
from shared.models.common import FirewallAction, Protocol
from agent.config import settings

logger = logging.getLogger(__name__)

RULE_PREFIX = "NekoProxy "
BLOCKLIST_RULE_NAME = "NekoProxy Blocklist"
BASELINE_RULE_PREFIX = "NekoProxy Baseline "
PORT_RULE_PREFIX = "NekoProxy Port "

# Ports we never allow on Public profile unless internal (same as Linux)
DANGEROUS_PORTS_ON_PUBLIC = frozenset({
    22, 23, 3389, 5900, 5432, 27017, 6379, 11211, 3306, 1433,
})


def _interface_to_profile(interface: str) -> str:
    """Map controller interface to Windows Firewall profile."""
    if not interface:
        return "Any"
    i = interface.lower()
    if i == "public":
        return "Public"
    if i in ("wireguard", "wg", "private"):
        return "Private"
    return "Any"


@dataclass(frozen=True)
class _PortRuleKey:
    port: int
    protocol: str
    interface: str
    action: str


class WindowsFirewallManager:
    """Manages Windows Firewall rules (blocklist, port rules, baseline). Same logical interface as Linux FirewallManager."""

    def __init__(self):
        self._blocklist_ips: Set[str] = set()
        self._current_rules: Set[_PortRuleKey] = set()
        self._baseline_applied = False
        self._initialized = False

    async def _run_powershell(self, script: str) -> Tuple[bool, str]:
        """Run PowerShell script; return (success, stderr_or_stdout)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            out = (stderr or stdout or b"").decode(errors="replace").strip()
            return proc.returncode == 0, out
        except Exception as e:
            logger.error(f"PowerShell error: {e}")
            return False, str(e)

    async def initialize(self) -> None:
        """Check that we can manage Windows Firewall (requires admin)."""
        if self._initialized:
            return
        # Test: get firewall profile (doesn't modify anything)
        ok, out = await self._run_powershell(
            "Get-NetFirewallProfile -ErrorAction Stop | Select-Object -ExpandProperty Name"
        )
        if not ok:
            logger.error("Windows Firewall not available or not running as admin: %s", out or "unknown")
            return
        self._initialized = True
        logger.info("Windows Firewall manager initialized (Public/Private profiles)")

    async def sync_blocklist_ips(self, ips: List[str]) -> None:
        """Sync blocklist: block inbound traffic from these IPs."""
        if not self._initialized:
            await self.initialize()
        if not self._initialized:
            return
        desired = set(ips)

        # Remove existing blocklist rule so we can recreate with new IP list
        await self._run_powershell(
            "Get-NetFirewallRule | Where-Object { $_.DisplayName -eq '" + BLOCKLIST_RULE_NAME + "' } | Remove-NetFirewallRule"
        )

        self._blocklist_ips.clear()
        if desired:
            # Create one rule with all remote addresses (comma-separated for -RemoteAddress)
            addr_list = ",".join(f"'{a}'" for a in desired)
            script = (
                "$addrs = @(" + addr_list + "); "
                "New-NetFirewallRule -DisplayName '" + BLOCKLIST_RULE_NAME + "' -Direction Inbound -Action Block -RemoteAddress $addrs -ErrorAction Stop"
            )
            ok, err = await self._run_powershell(script)
            if ok:
                self._blocklist_ips = set(desired)
                logger.info("Windows Firewall blocklist: %d IPs", len(self._blocklist_ips))
            else:
                logger.warning("Failed to set blocklist rule: %s", err)
        else:
            logger.info("Windows Firewall blocklist: cleared")

    async def add_blocklist_ip(self, ip: str) -> bool:
        """Add a single IP to the blocklist."""
        if not self._initialized:
            await self.initialize()
        if not self._initialized or ip in self._blocklist_ips:
            return ip in self._blocklist_ips
        self._blocklist_ips.add(ip)
        await self.sync_blocklist_ips(list(self._blocklist_ips))
        return True

    def _rule_to_key(self, rule: FirewallRuleResponse) -> _PortRuleKey:
        return _PortRuleKey(
            port=rule.port,
            protocol=rule.protocol.value,
            interface=rule.interface,
            action=rule.action.value,
        )

    async def sync_rules(self, rules: List[FirewallRuleResponse]) -> None:
        """Sync controller-defined port allow/block rules to Windows Firewall."""
        if not self._initialized:
            await self.initialize()
        if not self._initialized:
            return

        desired: Set[_PortRuleKey] = set()
        for rule in rules:
            if not rule.enabled:
                continue
            desired.add(self._rule_to_key(rule))

        # Remove rules no longer desired
        for key in list(self._current_rules):
            if key not in desired:
                name = f"{PORT_RULE_PREFIX}{key.port} {key.protocol.upper()} {key.action} {_interface_to_profile(key.interface)}"
                esc = name.replace("'", "''")
                await self._run_powershell(
                    "Get-NetFirewallRule | Where-Object { $_.DisplayName -eq '" + esc + "' } | Remove-NetFirewallRule"
                )
                self._current_rules.discard(key)

        # Add new rules
        for key in desired:
            if key not in self._current_rules:
                profile = _interface_to_profile(key.interface)
                action = "Allow" if key.action == "allow" else "Block"
                name = f"{PORT_RULE_PREFIX}{key.port} {key.protocol.upper()} {action} {profile}"
                esc = name.replace("'", "''")
                script = (
                    "New-NetFirewallRule -DisplayName '" + esc + "' -Direction Inbound -Action " + action
                    + " -Protocol " + key.protocol.upper() + " -LocalPort " + str(key.port)
                    + " -Profile " + profile + " -ErrorAction Stop"
                )
                ok, err = await self._run_powershell(script)
                if ok:
                    self._current_rules.add(key)
                else:
                    logger.warning("Failed to add rule %s: %s", name, err)

        logger.info("Windows Firewall rules synced: %d port rules", len(self._current_rules))

    async def apply_baseline(
        self,
        allowed_ports: List[Tuple[int, str]],
        harden_public: bool = True,
        allow_wg: bool = True,
        allow_dangerous_ports_on_public: bool = False,
    ) -> None:
        """On Public profile, allow only these ports (and exclude dangerous unless internal). Private profile left permissive."""
        if not self._initialized:
            await self.initialize()
        if not self._initialized:
            return

        # Remove previous baseline rules (match by prefix)
        await self._run_powershell(
            "Get-NetFirewallRule | Where-Object { $_.DisplayName -like '" + BASELINE_RULE_PREFIX.replace("'", "''") + "*' } | Remove-NetFirewallRule"
        )
        self._baseline_applied = False

        if not harden_public or not allowed_ports:
            return

        if allow_dangerous_ports_on_public:
            safe_ports = list(allowed_ports)
        else:
            safe_ports = [(p, proto) for p, proto in allowed_ports if p not in DANGEROUS_PORTS_ON_PUBLIC]

        for port, proto in safe_ports:
            name = f"{BASELINE_RULE_PREFIX}{port} {proto.upper()} Public"
            esc = name.replace("'", "''")
            script = (
                "New-NetFirewallRule -DisplayName '" + esc + "' -Direction Inbound -Action Allow -Protocol "
                + proto.upper() + " -LocalPort " + str(port) + " -Profile Public -ErrorAction Stop"
            )
            ok, err = await self._run_powershell(script)
            if not ok:
                logger.warning("Failed to add baseline rule %s: %s", name, err)

        # Explicitly block the ControlAPI management port on the Public profile so it is
        # never reachable from the public internet regardless of the profile's default action.
        mgmt_port = settings.api_port
        mgmt_name = f"{BASELINE_RULE_PREFIX}ControlAPI {mgmt_port} Block Public"
        esc_mgmt = mgmt_name.replace("'", "''")
        script_block = (
            "New-NetFirewallRule -DisplayName '" + esc_mgmt + "' -Direction Inbound -Action Block -Protocol TCP"
            " -LocalPort " + str(mgmt_port) + " -Profile Public -ErrorAction Stop"
        )
        ok_b, err_b = await self._run_powershell(script_block)
        if not ok_b:
            logger.warning("Failed to add ControlAPI block rule: %s", err_b)
        else:
            logger.info("Windows Firewall baseline: ControlAPI port %d explicitly blocked on Public profile", mgmt_port)

        self._baseline_applied = True
        logger.info("Windows Firewall baseline: Public profile allows %d proxy port(s)", len(safe_ports))

    async def shutdown(self) -> None:
        """Remove all NekoProxy rules from Windows Firewall."""
        if not self._initialized:
            return
        script = (
            "Get-NetFirewallRule | Where-Object { $_.DisplayName -like '" + RULE_PREFIX.replace("'", "''") + "*' } | Remove-NetFirewallRule"
        )
        await self._run_powershell(script)
        self._blocklist_ips.clear()
        self._current_rules.clear()
        self._baseline_applied = False
        self._initialized = False
        logger.info("Windows Firewall: all NekoProxy rules removed")
