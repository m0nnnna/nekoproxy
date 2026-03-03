"""Optional GeoIP lookup for allowlist/blocklist by country.
Uses MaxMind GeoLite2-Country.mmdb (download from MaxMind; path via NEKO_AGENT_GEOLITE2_DB).
If geoip2 is not installed or DB path is missing, lookups return None (geo filtering is no-op).
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import geoip2.database
    _GEOIP2_AVAILABLE = True
except ImportError:
    _GEOIP2_AVAILABLE = False


class GeoLookup:
    """Look up country code for an IP using GeoLite2-Country.mmdb."""

    def __init__(self, db_path: Optional[str] = None):
        self._reader = None
        if not _GEOIP2_AVAILABLE:
            logger.debug("geoip2 not installed; Geo filtering disabled")
            return
        path = Path(db_path) if db_path else None
        if not path or not path.is_file():
            logger.debug("GeoLite2 DB path not set or file missing; Geo filtering disabled")
            return
        try:
            self._reader = geoip2.database.Reader(str(path))
            logger.info("GeoIP database loaded: %s", path)
        except Exception as e:
            logger.warning("Failed to load GeoIP database %s: %s", path, e)

    def country_code(self, ip: str) -> Optional[str]:
        """Return ISO 3166-1 alpha-2 country code for IP, or None if unknown/unavailable."""
        if not self._reader:
            return None
        try:
            r = self._reader.country(ip)
            return r.country.iso_code if r and r.country else None
        except Exception:
            return None

    def close(self) -> None:
        if self._reader:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None
