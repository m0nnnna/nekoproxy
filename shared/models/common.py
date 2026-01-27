from enum import Enum


class Protocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class FirewallAction(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


class AlertSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(str, Enum):
    BAD_PORT_ACCESS = "bad_port_access"  # Access attempt on wrong interface
    REPEATED_BLOCKS = "repeated_blocks"  # Same IP blocked multiple times
    SUSPICIOUS_SCAN = "suspicious_scan"  # Port scanning detected
    AUTH_FAILURE = "auth_failure"  # Authentication failure
    RATE_LIMIT = "rate_limit"  # Too many connections
    SSH_BRUTE_FORCE = "ssh_brute_force"  # SSH brute force attempt
    MAIL_AUTH_FAILURE = "mail_auth_failure"  # Mail authentication failure
    INVALID_USER = "invalid_user"  # Invalid user login attempt
    CONNECTION_REFUSED = "connection_refused"  # Connection refused/blocked by firewall
    MAIL_RELAY_DENIED = "mail_relay_denied"  # Unauthorized mail relay attempt
    MAIL_SPAM_ATTEMPT = "mail_spam_attempt"  # Spam/abuse attempt detected
    MAIL_REJECTED = "mail_rejected"  # Mail rejected (bad sender/recipient)


class EmailBlocklistType(str, Enum):
    ADDRESS = "address"      # Single email address
    DOMAIN = "domain"        # Entire domain (@example.com)
    IP = "ip"               # Single IP
    IP_RANGE = "ip_range"   # CIDR notation (192.168.1.0/24)


class EmailDeploymentStatus(str, Enum):
    NOT_DEPLOYED = "not_deployed"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
