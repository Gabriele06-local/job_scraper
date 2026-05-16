"""Async Nominatim geocoder used by `cmd_geocode` (SDD §A.8).

Public OSM Nominatim has a strict 1 req/sec/IP policy — we enforce it client
side via a per-host throttle. Caller is expected to set a descriptive
`User-Agent` (Nominatim ToS requirement).

The `Geocoder` in `utils/geocoding.py` is a Google Maps wrapper kept for the
legacy scraper code; this module is intentionally separate so the geocoder
backfill job never charges Google for already-imported jobs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

from config import settings

logger = structlog.get_logger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_DEFAULT_USER_AGENT = "DevBoardsGeocoder/1.0 (+https://devboards.io/probe)"
_MIN_INTERVAL_S = 1.0  # Nominatim ToS: 1 request/sec.


@dataclass(frozen=True)
class GeocodeResult:
    """Nominatim hit normalized to our `JobLocation` shape."""

    lat: float
    lng: float
    formatted_address: str
    city: str | None
    country: str | None


class NominatimGeocoder:
    """Async OpenStreetMap Nominatim wrapper with per-process throttling."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._user_agent = user_agent or settings.expiration_user_agent or _DEFAULT_USER_AGENT
        self._timeout_s = timeout_s
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def lookup(self, address: str) -> Optional[GeocodeResult]:
        """Return coordinates for `address`, or None on miss / error."""
        if not address or not address.strip():
            return None

        async with self._lock:
            now = time.monotonic()
            wait = _MIN_INTERVAL_S - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_s,
                    headers={"User-Agent": self._user_agent},
                ) as client:
                    resp = await client.get(
                        _NOMINATIM_URL,
                        params={
                            "q": address,
                            "format": "json",
                            "addressdetails": "1",
                            "limit": "1",
                        },
                    )
            except httpx.HTTPError as exc:
                logger.warning("geocoder.http_error", address=address, error=str(exc)[:200])
                self._last_call = time.monotonic()
                return None
            finally:
                self._last_call = time.monotonic()

        if resp.status_code != 200:
            logger.warning(
                "geocoder.status_error",
                address=address,
                status_code=resp.status_code,
            )
            return None

        try:
            payload = resp.json()
        except ValueError:
            return None

        if not isinstance(payload, list) or not payload:
            return None

        hit = payload[0]
        try:
            lat = float(hit["lat"])
            lng = float(hit["lon"])
        except (KeyError, TypeError, ValueError):
            return None

        addr = hit.get("address", {}) if isinstance(hit, dict) else {}
        city = addr.get("city") or addr.get("town") or addr.get("village")
        country_code = addr.get("country_code")
        return GeocodeResult(
            lat=lat,
            lng=lng,
            formatted_address=hit.get("display_name", address),
            city=city,
            country=country_code.upper() if isinstance(country_code, str) else None,
        )
