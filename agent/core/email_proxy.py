"""Email proxy management using Postfix + SASL."""

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
    hostname: str  # FQDN for Postfix and SSL certs (e.g., mail.example.com)
    mailcow_ip: str  # Mailcow's WireGuard/internal IP for transport
    mailcow_port: int
    proxy_ip: str


class EmailProxyManager:
    """Manages Postfix + SASL email proxy deployment and configuration."""

    def __init__(self):
        self._deployed = False
        self._current_config: Optional[AgentEmailConfig] = None
        self._config_version: int = 0
        self._postfix_config: Optional[PostfixConfig] = None
        self._hostname: Optional[str] = None

    @property
    def is_deployed(self) -> bool:
        return self._deployed

    async def deploy(self, hostname: str, mailcow_ip: str, mailcow_port: int, proxy_ip: str) -> Tuple[bool, Optional[str]]:
        """Install and configure Postfix + SASL (no rspamd - mailcow handles filtering).

        Args:
            hostname: FQDN for this proxy (for Postfix myhostname and SSL certs)
            mailcow_ip: Mailcow's WireGuard/internal IP for transport routing
            mailcow_port: Mailcow SMTP port (usually 25)
            proxy_ip: This proxy's public IP for header stamping

        Returns:
            Tuple of (success, error_message)
        """
        logger.info("Starting email proxy deployment...")
        logger.info(f"  Hostname: {hostname}")
        logger.info(f"  Mailcow IP: {mailcow_ip}:{mailcow_port}")
        logger.info(f"  Proxy IP: {proxy_ip}")

        self._postfix_config = PostfixConfig(
            hostname=hostname,
            mailcow_ip=mailcow_ip,
            mailcow_port=mailcow_port,
            proxy_ip=proxy_ip
        )

        # Use the provided hostname for SASL realm (must match smtpd_sasl_local_domain)
        self._hostname = hostname

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

            # Configure Postfix (no rspamd - mailcow handles filtering)
            try:
                await self._configure_postfix()
            except Exception as e:
                raise Exception(f"Postfix configuration failed: {e}")

            # Start services (only postfix, no rspamd)
            try:
                await self._start_services()
            except Exception as e:
                raise Exception(f"Service startup failed: {e}")

            self._deployed = True

            # Check if SSL is configured
            ssl_warning = None
            cert_path = f"/etc/letsencrypt/live/{hostname}/fullchain.pem"
            if not os.path.exists(cert_path):
                ssl_warning = (
                    f"SSL not configured. For TLS support, run on the agent server:\n"
                    f"  1. apt install certbot\n"
                    f"  2. certbot certonly --standalone -d {hostname}\n"
                    f"  3. chmod 755 /etc/letsencrypt/live/ /etc/letsencrypt/archive/\n"
                    f"  4. systemctl restart postfix"
                )
                logger.warning("=" * 60)
                logger.warning("SSL NOT CONFIGURED - TLS disabled")
                logger.warning(ssl_warning)
                logger.warning("=" * 60)

            logger.info("Email proxy deployment complete")
            return True, ssl_warning

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
        """Install Postfix and SASL packages (no rspamd - mailcow handles filtering)."""
        logger.info("Installing Postfix and SASL packages...")

        # Set up environment for apt - ensure we have a proper environment
        # when running from systemd service
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'
        env['DEBCONF_NONINTERACTIVE_SEEN'] = 'true'
        env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
        env['LC_ALL'] = 'C'
        env['LANG'] = 'C'

        # Clear any stale lock files that might be left from crashed processes
        logger.info("Clearing any stale lock files...")
        for lock_file in [
            "/var/lib/dpkg/lock",
            "/var/lib/dpkg/lock-frontend",
            "/var/cache/apt/archives/lock",
            "/var/lib/apt/lists/lock"
        ]:
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                    logger.info(f"Removed stale lock file: {lock_file}")
                except Exception as e:
                    logger.warning(f"Could not remove {lock_file}: {e}")

        # First, attempt to fix any broken dpkg/apt state
        logger.info("Checking and repairing package manager state...")

        # Configure any partially installed packages
        proc = await asyncio.create_subprocess_exec(
            "dpkg", "--configure", "-a", "--force-confdef", "--force-confold",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"dpkg --configure -a returned non-zero: {stderr.decode()}")

        # Clean apt cache to remove any corrupted packages
        proc = await asyncio.create_subprocess_exec(
            "apt-get", "clean",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        await proc.communicate()

        # Fix broken dependencies
        proc = await asyncio.create_subprocess_exec(
            "apt-get", "-f", "install", "-y", "--fix-missing",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"apt-get -f install returned non-zero: {stderr.decode()}")

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
        logger.info("Updating package lists...")
        proc = await asyncio.create_subprocess_exec(
            "apt-get", "update",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"apt-get update returned non-zero: {stderr.decode()}")

        # Install packages: postfix and SASL
        # No rspamd - mailcow handles filtering
        # No certbot - SSL setup is manual
        logger.info("Installing postfix and SASL packages...")
        proc = await asyncio.create_subprocess_exec(
            "apt-get", "install", "-y", "--no-install-recommends",
            "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold",
            "postfix",
            "sasl2-bin", "libsasl2-modules",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_output = stderr.decode()
            logger.error(f"Package installation failed. stdout: {stdout.decode()}")
            logger.error(f"Package installation failed. stderr: {error_output}")
            raise Exception(f"Failed to install packages: {error_output}")

        logger.info("Packages installed successfully")

    async def _obtain_ssl_cert(self):
        """Obtain Let's Encrypt SSL certificate via certbot.

        Requires DNS A record for hostname to point to this server's public IP.
        """
        if not self._postfix_config:
            raise Exception("PostfixConfig not set")

        hostname = self._postfix_config.hostname
        cert_path = f"/etc/letsencrypt/live/{hostname}/fullchain.pem"

        # Check if cert already exists and is valid
        if os.path.exists(cert_path):
            logger.info(f"SSL certificate already exists for {hostname}")
            # Fix permissions just in case
            await self._fix_cert_permissions()
            return

        logger.info(f"Obtaining SSL certificate for {hostname}...")

        # Stop any services that might be using port 80
        await self._run_command("systemctl", "stop", "nginx", check=False)
        await self._run_command("systemctl", "stop", "apache2", check=False)

        # Run certbot in standalone mode
        proc = await asyncio.create_subprocess_exec(
            "certbot", "certonly",
            "--standalone",
            "--non-interactive",
            "--agree-tos",
            "--email", f"admin@{hostname.split('.', 1)[-1] if '.' in hostname else hostname}",
            "-d", hostname,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_output = stderr.decode() if stderr else stdout.decode()
            raise Exception(f"certbot failed: {error_output}")

        logger.info(f"SSL certificate obtained for {hostname}")

        # Fix permissions for Postfix to read certs
        await self._fix_cert_permissions()

    async def _fix_cert_permissions(self):
        """Fix Let's Encrypt directory permissions for Postfix access."""
        if not self._postfix_config:
            return

        # Make live and archive directories readable
        for path in ["/etc/letsencrypt/live", "/etc/letsencrypt/archive"]:
            if os.path.exists(path):
                os.chmod(path, 0o755)

        hostname = self._postfix_config.hostname
        for base in ["/etc/letsencrypt/live", "/etc/letsencrypt/archive"]:
            host_path = os.path.join(base, hostname)
            if os.path.exists(host_path):
                os.chmod(host_path, 0o755)

        logger.info("SSL certificate permissions fixed")

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
        """Configure Postfix as mail relay with SASL auth and proper routing.

        Key design:
        - Outbound: Delivers directly to destination MX (no relayhost)
        - Inbound: Routes mail for relay_domains to Mailcow via transport_maps
        - No milters: Mailcow handles all filtering
        - TLS: Configured for Let's Encrypt certs (user installs certbot manually)
        """
        if not self._postfix_config:
            raise Exception("PostfixConfig not set")

        logger.info("Configuring Postfix...")

        config = self._postfix_config
        hostname = config.hostname

        # Main configuration - TLS paths are set for Let's Encrypt
        # User needs to install certbot and obtain certs manually after deployment
        main_cf = f"""# NekoProxy Email Relay Configuration
# Automatically managed - do not edit manually

# Basic settings
smtpd_banner = $myhostname ESMTP
biff = no
append_dot_mydomain = no
compatibility_level = 2

# TLS parameters - Let's Encrypt certificates
# NOTE: You must install certbot and obtain certificates for TLS to work:
#   1. apt install certbot
#   2. certbot certonly --standalone -d {hostname}
#   3. chmod 755 /etc/letsencrypt/live/ /etc/letsencrypt/archive/
#   4. systemctl restart postfix
smtpd_tls_cert_file = /etc/letsencrypt/live/{hostname}/fullchain.pem
smtpd_tls_key_file = /etc/letsencrypt/live/{hostname}/privkey.pem
smtpd_tls_security_level = may
smtpd_tls_auth_only = yes
smtp_tls_CApath = /etc/ssl/certs
smtp_tls_security_level = may
smtp_tls_session_cache_database = btree:${{data_directory}}/smtp_scache

# Network settings
myhostname = {hostname}
myorigin = $myhostname
mydestination =
mynetworks = 127.0.0.0/8, {config.mailcow_ip}
inet_interfaces = all
inet_protocols = ipv4

# NO relayhost - deliver directly to internet for outbound
relayhost =

# Relay configuration - inbound mail for domains routes to Mailcow
relay_domains = hash:/etc/postfix/relay_domains
transport_maps = hash:/etc/postfix/transport
relay_recipient_maps = hash:/etc/postfix/relay_recipients

# SASL Authentication
cyrus_sasl_config_path = /etc/postfix/sasl
smtpd_sasl_path = smtpd
smtpd_sasl_auth_enable = yes
smtpd_sasl_security_options = noanonymous
smtpd_sasl_local_domain = $myhostname
broken_sasl_auth_clients = yes

# Restrictions
smtpd_helo_required = yes

smtpd_relay_restrictions =
    permit_sasl_authenticated,
    permit_mynetworks,
    reject_unauth_destination

smtpd_recipient_restrictions =
    permit_sasl_authenticated,
    permit_mynetworks,
    reject_unauth_destination

smtpd_sender_restrictions =
    permit_sasl_authenticated,
    reject_unknown_sender_domain

# NO milters - mailcow handles filtering
smtpd_milters =
non_smtpd_milters =

# Bounce handling - prevent double-bounce loops
soft_bounce = no
notify_classes = resource, software
bounce_queue_lifetime = 0
maximal_queue_lifetime = 1d
2bounce_notice_recipient = postmaster

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

        # Initial empty transport map (routes domains to mailcow)
        with open("/etc/postfix/transport", "w") as f:
            f.write("# Transport map - managed by NekoProxy\n")

        # Initial empty relay recipients map
        with open("/etc/postfix/relay_recipients", "w") as f:
            f.write("# Relay recipients - managed by NekoProxy\n")

        # Compile all maps
        await self._run_command("postmap", "/etc/postfix/sender_access")
        await self._run_command("postmap", "/etc/postfix/relay_domains")
        await self._run_command("postmap", "/etc/postfix/transport")
        await self._run_command("postmap", "/etc/postfix/relay_recipients")

        # Validate configuration
        await self._run_command("postfix", "check")

        logger.info("Postfix configured with Let's Encrypt TLS and SASL support")

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
        """Start and enable Postfix service (no rspamd - mailcow handles filtering)."""
        logger.info("Starting Postfix service...")

        await self._run_command("systemctl", "enable", "postfix", check=False)
        await self._run_command("systemctl", "restart", "postfix", check=False)

        logger.info("Postfix service started")

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

        # Update relay domains (also updates transport and relay_recipients maps)
        await self._update_relay_domains(config.relay_domains)

        # Note: No blocklist updates - mailcow handles all filtering

        # Reload Postfix to apply changes
        await self._reload_services()

        self._current_config = config
        self._config_version = config.config_version

        logger.info(f"Email config applied: {len(config.authorized_senders)} authorized senders, "
                    f"{len(config.sasl_users)} SASL users, "
                    f"{len(config.relay_domains)} relay domains")

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
        """Update Postfix relay domains, transport, and relay_recipients maps.

        For each domain:
        - relay_domains: domain OK (accept mail for this domain)
        - transport: domain smtp:[mailcow_ip]:port (route to mailcow)
        - relay_recipients: @domain OK (accept all recipients at domain)
        """
        if not self._postfix_config:
            logger.warning("PostfixConfig not set, cannot update relay domains")
            return

        mailcow_ip = self._postfix_config.mailcow_ip
        mailcow_port = self._postfix_config.mailcow_port

        # relay_domains map
        relay_content = "# Relay domains - managed by NekoProxy\n"
        for domain in relay_domains:
            relay_content += f"{domain}    OK\n"

        with open("/etc/postfix/relay_domains", "w") as f:
            f.write(relay_content)

        # transport map - routes inbound mail for domains to mailcow
        transport_content = "# Transport map - managed by NekoProxy\n"
        for domain in relay_domains:
            transport_content += f"{domain}    smtp:[{mailcow_ip}]:{mailcow_port}\n"

        with open("/etc/postfix/transport", "w") as f:
            f.write(transport_content)

        # relay_recipients map - accept all recipients at relay domains
        recipients_content = "# Relay recipients - managed by NekoProxy\n"
        for domain in relay_domains:
            recipients_content += f"@{domain}    OK\n"

        with open("/etc/postfix/relay_recipients", "w") as f:
            f.write(recipients_content)

        # Compile all maps
        await self._run_command("postmap", "/etc/postfix/relay_domains")
        await self._run_command("postmap", "/etc/postfix/transport")
        await self._run_command("postmap", "/etc/postfix/relay_recipients")

        logger.info(f"Updated {len(relay_domains)} relay domains with transport and recipient maps")

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
        """Reload Postfix to apply changes (no rspamd - mailcow handles filtering)."""
        await self._run_command("postfix", "reload", check=False)

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
