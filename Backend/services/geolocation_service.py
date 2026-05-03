"""
Geolocation service with Haversine distance checks.
"""
import math
from typing import Optional, Tuple


class GeolocationService:
    # Minimal centroid map for fallback when precise geocoding is unavailable.
    CITY_COORDS = {
        "mumbai": (19.0760, 72.8777),
        "delhi": (28.6139, 77.2090),
        "bengaluru": (12.9716, 77.5946),
        "chennai": (13.0827, 80.2707),
        "pune": (18.5204, 73.8567),
        "hyderabad": (17.3850, 78.4867),
    }

    def haversine_km(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        radius_km = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius_km * c

    def resolve_declared_coords(self, report: Optional[dict]) -> Optional[Tuple[float, float]]:
        if not report:
            return None
        city = (
            report.get("kyc", {}).get("city")
            or report.get("address", {}).get("city")
            or ""
        )
        if not city:
            return None
        return self.CITY_COORDS.get(str(city).strip().lower())

    def resolve_ip_coords(self, ip_address: Optional[str]) -> Optional[Tuple[float, float]]:
        # Placeholder for MaxMind integration; keeps deterministic fallback behavior.
        if not ip_address:
            return None
        if ip_address.startswith("127.") or ip_address == "localhost":
            return self.CITY_COORDS["mumbai"]
        return None


geolocation_service = GeolocationService()
