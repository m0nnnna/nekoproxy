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
