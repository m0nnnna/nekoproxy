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
