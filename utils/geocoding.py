import requests
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class Geocoder:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_coordinates(self, address: str) -> Optional[Dict[str, float]]:
        """Fetch GPS coordinates from Google Maps Geocoding API"""
        if not self.api_key or not address:
            return None

        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": self.api_key}

        try:
            response = requests.get(url, params=params)
            data = response.json()

            if data["status"] == "OK":
                result = data["results"][0]
                location = result["geometry"]["location"]
                return {
                    "lat": location["lat"],
                    "lng": location["lng"],
                    "formatted_address": result.get("formatted_address"),
                }
            else:
                logger.warning(f"Geocoding failed for {address}: {data['status']}")
                return None
        except Exception as e:
            logger.error(f"Error calling Geocoding API: {e}")
            return None
