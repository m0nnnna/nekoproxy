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
