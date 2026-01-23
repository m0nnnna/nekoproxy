"""Email proxy management using Postfix + rspamd + SASL."""

import asyncio
import logging
import os
import shutil
import socket
from typing import Optional, List, Tuple
from dataclasses import dataclass

from shared.models.email import AgentEmailConfig, SaslCredential

logger = logging.getLogger(__name__)


@dataclass
class PostfixConfig:
    """Postfix configuration parameters."""
    mailcow_host: str
    mailcow_port: int
    proxy_ip: str


class EmailProxyManager:
    """Manages Postfix + rspamd + SASL email proxy deployment and configuration."""

    def __init__(self):
        self._deployed = False
        self._current_config: Optional[AgentEmailConfig] = None
        self._config_version: int = 0
        self._postfix_config: Optional[PostfixConfig] = None
        self._hostname: Optional[str] = None

    @property
    def is_deployed(self) -> bool:
        return self._deployed

    async def deploy(self, mailcow_host: str, mailcow_port: int, proxy_ip: str) -> Tuple[bool, Optional[str]]:
        """Install and configure Postfix + rspamd + SASL.

        Args:
            mailcow_host: Mailcow server hostname/IP
            mailcow_port: Mailcow SMTP port
            proxy_ip: This proxy's public IP for header stamping

        Returns:
            Tuple of (success, error_message)
        """
        logger.info("Starting email proxy deployment...")
        logger.info(f"  Mailcow: {mailcow_host}:{mailcow_port}")
        logger.info(f"  Proxy IP: {proxy_ip}")

        self._postfix_config = PostfixConfig(
            mailcow_host=mailcow_host,
            mailcow_port=mailcow_port,
            proxy_ip=proxy_ip
        )

        # Get hostname for SASL realm (must match smtpd_sasl_local_domain)
        self._hostname = socket.gethostname()

        try:
            # Install packages
            try:
                await self._install_packages()
            except Exception as e:
                raise Exception(f"Package installation failed: {e}")

            # Configure SASL
            try:
                await self._configure_sasl()
            except Exception as e:
                raise Exception(f"SASL configuration failed: {e}")

            # Configure Postfix
            try:
                await self._configure_postfix()
            except Exception as e:
                raise Exception(f"Postfix configuration failed: {e}")

            # Configure rspamd
            try:
                await self._configure_rspamd()
            except Exception as e:
                raise Exception(f"rspamd configuration failed: {e}")

            # Start services
            try:
                await self._start_services()
            except Exception as e:
                raise Exception(f"Service startup failed: {e}")

            self._deployed = True
            logger.info("Email proxy deployment complete")
            return True, None

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Email proxy deployment failed: {error_msg}")
            return False, error_msg

    async def _run_command(self, *args, check: bool = True) -> tuple:
        """Run a shell command asynchronously.

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if check and proc.returncode != 0:
            raise Exception(f"Command {args[0]} failed: {stderr.decode()}")

        return proc.returncode, stdout.decode(), stderr.decode()

    async def _install_packages(self):
        """Install Postfix, rspamd, and SASL packages."""
        logger.info("Installing Postfix, rspamd, and SASL packages...")

        # Set non-interactive frontend for apt
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'

        # Pre-configure postfix to avoid interactive prompts
        debconf_settings = [
            "postfix postfix/mailname string localhost",
            "postfix postfix/main_mailer_type string 'Internet Site'"
        ]

        for setting in debconf_settings:
            proc = await asyncio.create_subprocess_shell(
                f"echo '{setting}' | debconf-set-selections",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            await proc.communicate()

        # Update package lists
        await self._run_command("apt-get", "update")

        # Install packages including SASL
        proc = await asyncio.create_subprocess_exec(
            "apt-get", "install", "-y",
            "postfix", "rspamd", "redis-server",
            "sasl2-bin", "libsasl2-modules",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise Exception(f"Failed to install packages: {stderr.decode()}")

        logger.info("Packages installed successfully")

    async def _configure_sasl(self):
        """Configure SASL authentication for Postfix using auxprop/sasldb.

        Uses auxprop (not saslauthd) to support CRAM-MD5 and DIGEST-MD5.
        Postfix runs chrooted, so sasldb must be in the chroot.
        """
        logger.info("Configuring SASL with auxprop/sasldb...")

        # Create SASL directories (both regular and chroot locations)
        os.makedirs("/etc/postfix/sasl", exist_ok=True)
        os.makedirs("/var/spool/postfix/etc", exist_ok=True)

        # SASL configuration for Postfix SMTP server (using auxprop for CRAM-MD5 support)
        # Path is relative to chroot: /var/spool/postfix/etc/sasldb2
        smtpd_conf = """# SASL configuration for Postfix SMTP server
pwcheck_method: auxprop
auxprop_plugin: sasldb
mech_list: PLAIN LOGIN CRAM-MD5 DIGEST-MD5
sasldb_path: /etc/sasldb2
"""
        with open("/etc/postfix/sasl/smtpd.conf", "w") as f:
            f.write(smtpd_conf)

        logger.info("SASL configured with auxprop/sasldb")

    async def _configure_postfix(self):
        """Configure Postfix as relay with SASL auth and IP stamping."""
        if not self._postfix_config:
            raise Exception("PostfixConfig not set")

        logger.info("Configuring Postfix...")

        config = self._postfix_config
        hostname = self._hostname

        # Main configuration with SASL authentication
        main_cf = f"""# NekoProxy Email Relay Configuration
# Automatically managed - do not edit manually

smtpd_banner = $myhostname ESMTP
biff = no
append_dot_mydomain = no

# TLS parameters
smtpd_tls_cert_file=/etc/ssl/certs/ssl-cert-snakeoil.pem
smtpd_tls_key_file=/etc/ssl/private/ssl-cert-snakeoil.key
smtpd_tls_security_level=may
smtp_tls_security_level=may

# Network settings
myhostname = {hostname}
myorigin = $myhostname
mydestination =
mynetworks = 127.0.0.0/8 [::ffff:127.0.0.0]/104 [::1]/128
inet_interfaces = all
inet_protocols = ipv4

# Relay to Mailcow
relayhost = [{config.mailcow_host}]:{config.mailcow_port}
smtp_host_lookup = native

# Relay domains (will be managed dynamically)
relay_domains = hash:/etc/postfix/relay_domains

# SASL Authentication
cyrus_sasl_config_path = /etc/postfix/sasl
smtpd_sasl_path = smtpd
smtpd_sasl_auth_enable = yes
smtpd_sasl_security_options = noanonymous
smtpd_sasl_local_domain = $myhostname
broken_sasl_auth_clients = yes

# Sender restrictions - check SASL auth first
smtpd_sender_restrictions =
    permit_sasl_authenticated,
    check_sender_access hash:/etc/postfix/sender_access,
    reject_unknown_sender_domain

# Relay restrictions - require SASL auth for relay
smtpd_relay_restrictions =
    permit_mynetworks,
    permit_sasl_authenticated,
    reject_unauth_destination

# Recipient restrictions
smtpd_recipient_restrictions =
    permit_mynetworks,
    permit_sasl_authenticated,
    check_sender_access hash:/etc/postfix/sender_access,
    reject_unauth_destination

# Content filtering via rspamd
milter_protocol = 6
milter_default_action = accept
smtpd_milters = inet:localhost:11332
non_smtpd_milters = $smtpd_milters

# IP stamping - ensure proxy IP is in headers
always_add_missing_headers = yes
smtp_header_checks = regexp:/etc/postfix/header_checks

# Logging
maillog_file = /var/log/mail.log
"""

        with open("/etc/postfix/main.cf", "w") as f:
            f.write(main_cf)

        # Header checks for IP stamping
        header_checks = f"""# Replace/add X-Originating-IP with proxy IP
/^X-Originating-IP:/ REPLACE X-Originating-IP: [{config.proxy_ip}]
"""
        with open("/etc/postfix/header_checks", "w") as f:
            f.write(header_checks)

        # Initial empty sender access map (will be populated by apply_config)
        with open("/etc/postfix/sender_access", "w") as f:
            f.write("# Authorized senders - managed by NekoProxy\n")

        # Initial empty relay domains map
        with open("/etc/postfix/relay_domains", "w") as f:
            f.write("# Relay domains - managed by NekoProxy\n")

        # Compile the maps
        await self._run_command("postmap", "/etc/postfix/sender_access")
        await self._run_command("postmap", "/etc/postfix/relay_domains")

        logger.info("Postfix configured with SASL support")

    async def _configure_rspamd(self):
        """Configure rspamd for spam filtering and blocklists."""
        logger.info("Configuring rspamd...")

        # Ensure config directories exist
        os.makedirs("/etc/rspamd/local.d", exist_ok=True)

        # Create custom configuration
        rspamd_local = """# NekoProxy rspamd configuration
options {
    filters = "chartable,dkim,regexp,fuzzy_check"
}
"""
        with open("/etc/rspamd/local.d/options.inc", "w") as f:
            f.write(rspamd_local)

        # Initial empty blocklist files
        for filename in ["blocked_senders.map", "blocked_domains.map", "blocked_ips.map"]:
            filepath = f"/etc/rspamd/local.d/{filename}"
            if not os.path.exists(filepath):
                with open(filepath, "w") as f:
                    f.write("# Managed by NekoProxy\n")

        logger.info("rspamd configured")

    async def _start_services(self):
        """Start and enable services."""
        logger.info("Starting services...")

        for service in ["redis-server", "rspamd", "postfix"]:
            await self._run_command("systemctl", "enable", service, check=False)
            await self._run_command("systemctl", "start", service, check=False)

        logger.info("Services started")

    async def apply_config(self, config: AgentEmailConfig):
        """Apply email configuration updates.

        Args:
            config: Email configuration from controller
        """
        if not self._deployed:
            logger.debug("Email proxy not deployed, skipping config apply")
            return

        if not config.enabled:
            logger.debug("Email config disabled, skipping apply")
            return

        if config.config_version <= self._config_version:
            logger.debug(f"Config version {config.config_version} already applied (current: {self._config_version})")
            return

        logger.info(f"Applying email config version {config.config_version}")

        # Update authorized senders
        await self._update_sender_access(config.authorized_senders)

        # Update SASL users
        await self._update_sasl_users(config.sasl_users)

        # Update relay domains
        await self._update_relay_domains(config.relay_domains)

        # Update blocklists in rspamd
        await self._update_blocklists(
            config.blocklist_addresses,
            config.blocklist_domains,
            config.blocklist_ips
        )

        # Reload services
        await self._reload_services()

        self._current_config = config
        self._config_version = config.config_version

        logger.info(f"Email config applied: {len(config.authorized_senders)} authorized senders, "
                    f"{len(config.sasl_users)} SASL users, "
                    f"{len(config.relay_domains)} relay domains, "
                    f"{len(config.blocklist_addresses)} blocked addresses")

    async def _update_sender_access(self, authorized_senders: List[str]):
        """Update Postfix sender access map."""
        content = "# Authorized senders - managed by NekoProxy\n"
        for sender in authorized_senders:
            content += f"{sender} OK\n"

        with open("/etc/postfix/sender_access", "w") as f:
            f.write(content)

        await self._run_command("postmap", "/etc/postfix/sender_access")

    async def _update_sasl_users(self, sasl_users: List[SaslCredential]):
        """Update SASL user database."""
        if not sasl_users:
            logger.debug("No SASL users to configure")
            return

        # Use raw hostname as realm (must match smtpd_sasl_local_domain = $myhostname)
        hostname = self._hostname or socket.gethostname()

        logger.info(f"Updating {len(sasl_users)} SASL users for realm {hostname}...")

        for user in sasl_users:
            # Use saslpasswd2 to add/update user
            # -p reads password from stdin
            # -c creates user if doesn't exist
            # -u specifies the realm (hostname)
            proc = await asyncio.create_subprocess_exec(
                "saslpasswd2", "-p", "-c", "-u", hostname, user.username,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate(input=user.password.encode())

            if proc.returncode != 0:
                logger.warning(f"Failed to set SASL password for {user.username}: {stderr.decode()}")
            else:
                logger.info(f"Updated SASL user: {user.username}@{hostname}")

        # Copy sasldb to Postfix chroot (Postfix runs chrooted and needs access)
        sasldb_path = "/etc/sasldb2"
        chroot_sasldb_path = "/var/spool/postfix/etc/sasldb2"

        if os.path.exists(sasldb_path):
            # Set permissions on original
            os.chmod(sasldb_path, 0o640)
            await self._run_command("chown", "root:postfix", sasldb_path, check=False)

            # Copy to chroot location
            os.makedirs("/var/spool/postfix/etc", exist_ok=True)
            shutil.copy2(sasldb_path, chroot_sasldb_path)
            os.chmod(chroot_sasldb_path, 0o640)
            await self._run_command("chown", "root:postfix", chroot_sasldb_path, check=False)

            logger.info(f"Copied sasldb to chroot: {chroot_sasldb_path}")

    async def _update_relay_domains(self, relay_domains: List[str]):
        """Update Postfix relay domains map."""
        content = "# Relay domains - managed by NekoProxy\n"
        for domain in relay_domains:
            content += f"{domain} OK\n"

        with open("/etc/postfix/relay_domains", "w") as f:
            f.write(content)

        await self._run_command("postmap", "/etc/postfix/relay_domains")
        logger.debug(f"Updated {len(relay_domains)} relay domains")

    async def _update_blocklists(self, addresses: List[str], domains: List[str], ips: List[str]):
        """Update rspamd blocklists."""
        # Create rspamd multimap configuration
        multimap_conf = """# Email blocklist - managed by NekoProxy
BLOCKED_SENDERS {
    type = "from";
    map = "/etc/rspamd/local.d/blocked_senders.map";
    action = "reject";
    message = "Sender blocked by policy";
}

BLOCKED_DOMAINS {
    type = "from";
    filter = "email:domain";
    map = "/etc/rspamd/local.d/blocked_domains.map";
    action = "reject";
    message = "Domain blocked by policy";
}

BLOCKED_IPS {
    type = "ip";
    map = "/etc/rspamd/local.d/blocked_ips.map";
    action = "reject";
    message = "IP blocked by policy";
}
"""
        with open("/etc/rspamd/local.d/multimap.conf", "w") as f:
            f.write(multimap_conf)

        # Write map files
        with open("/etc/rspamd/local.d/blocked_senders.map", "w") as f:
            f.write("# Blocked email addresses - managed by NekoProxy\n")
            f.write("\n".join(addresses))

        with open("/etc/rspamd/local.d/blocked_domains.map", "w") as f:
            f.write("# Blocked domains - managed by NekoProxy\n")
            f.write("\n".join(domains))

        with open("/etc/rspamd/local.d/blocked_ips.map", "w") as f:
            f.write("# Blocked IPs - managed by NekoProxy\n")
            f.write("\n".join(ips))

    async def _reload_services(self):
        """Reload Postfix and rspamd to apply changes."""
        await self._run_command("postfix", "reload", check=False)
        await self._run_command("systemctl", "reload", "rspamd", check=False)

    async def delete_sasl_user(self, username: str) -> bool:
        """Delete a SASL user from the database."""
        hostname = self._hostname or socket.gethostname()

        proc = await asyncio.create_subprocess_exec(
            "saslpasswd2", "-d", "-u", hostname, username,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        return proc.returncode == 0

    async def list_sasl_users(self) -> List[str]:
        """List all SASL users in the database."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sasldblistusers2",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0:
                return []

            # Output format: user@realm: userPassword
            users = []
            for line in stdout.decode().strip().split('\n'):
                if line and ':' in line:
                    user_part = line.split(':')[0].strip()
                    if '@' in user_part:
                        username = user_part.split('@')[0]
                        users.append(username)
            return users
        except Exception as e:
            logger.error(f"Failed to list SASL users: {e}")
            return []

    async def shutdown(self):
        """Stop email proxy services."""
        if not self._deployed:
            return

        logger.info("Stopping email proxy services...")
        for service in ["postfix", "rspamd"]:
            await self._run_command("systemctl", "stop", service, check=False)
