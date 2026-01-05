"""Koji Geofence Manager - Singleton for managing geofence data with automatic refresh."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
from cachetools import TTLCache
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from LazyIVQueue.utils.logger import logger
import LazyIVQueue.config as AppConfig


@dataclass
class GeofenceArea:
    """Represents a single geofence area."""

    name: str
    polygon: Polygon
    prepared_polygon: Any  # PreparedGeometry for faster point-in-polygon checks


class KojiGeofenceManager:
    """
    Singleton manager for Koji geofences.

    Features:
    - TTL-based caching with automatic refresh
    - Immediate fetch on startup (not waiting for first refresh cycle)
    - Shapely-based point-in-polygon checks with prepared geometries
    - Thread-safe singleton pattern for async context
    """

    _instance: Optional[KojiGeofenceManager] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __new__(cls) -> KojiGeofenceManager:
        # This is just for the singleton pattern structure
        # Actual instance creation happens in get_instance()
        return super().__new__(cls)

    def __init__(self) -> None:
        # Prevent re-initialization on subsequent calls
        if hasattr(self, "_initialized") and self._initialized:
            return

        self._geofences: Dict[str, GeofenceArea] = {}
        self._cache: Optional[TTLCache] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._initialized: bool = False
        self._session: Optional[aiohttp.ClientSession] = None

    @classmethod
    async def get_instance(cls) -> KojiGeofenceManager:
        """Get or create the singleton instance."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    async def initialize(self) -> None:
        """
        Initialize the manager:
        1. Create TTL cache
        2. Fetch geofences immediately
        3. Start background refresh task
        """
        if self._initialized:
            logger.warning("KojiGeofenceManager already initialized")
            return

        # Create TTL cache with configured expiration
        self._cache = TTLCache(
            maxsize=1000, ttl=AppConfig.geofence_expire_cache_seconds
        )

        # Create aiohttp session for API calls
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        )

        # Fetch geofences immediately on startup
        logger.info("Fetching geofences from Koji...")
        await self._fetch_and_update_geofences()

        # Start background refresh task
        self._refresh_task = asyncio.create_task(self._refresh_loop())

        self._initialized = True
        logger.info(
            f"KojiGeofenceManager initialized with {len(self._geofences)} geofences, "
            f"refresh interval: {AppConfig.geofence_refresh_cache_seconds}s"
        )

    async def _fetch_geofences(self) -> Dict[str, GeofenceArea]:
        """
        Fetch geofences from Koji API.

        Endpoint: GET {koji_url}/api/v1/geofence/feature-collection/{project_name}
        Headers: Authorization: Bearer {koji_bearer_token}
        Response: GeoJSON FeatureCollection
        """
        # Determine which URL to use
        url = AppConfig.koji_url or AppConfig.koji_geofence_api_url

        if not url:
            logger.error("No Koji URL configured (KOJI_URL or KOJI_IP/KOJI_PORT)")
            return {}

        # Build headers with bearer token if available
        headers = {}
        if AppConfig.koji_bearer_token:
            headers["Authorization"] = f"Bearer {AppConfig.koji_bearer_token}"

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.error(
                        f"Failed to fetch geofences from Koji: HTTP {response.status}"
                    )
                    return {}

                data = await response.json()
                return self._parse_geojson(data)

        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching geofences from Koji: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error fetching geofences: {e}")
            return {}

    def _parse_geojson(self, geojson: Dict) -> Dict[str, GeofenceArea]:
        """
        Parse GeoJSON FeatureCollection into GeofenceArea objects.
        Only processes Polygon features, skips others.
        """
        geofences = {}

        # Handle both direct features array and nested data structure
        features = geojson.get("features", [])
        if not features:
            data = geojson.get("data", {})
            features = data.get("features", [])

        for feature in features:
            try:
                geometry = feature.get("geometry", {})
                geom_type = geometry.get("type")

                # Only process Polygon geometries
                if geom_type != "Polygon":
                    if geom_type:
                        logger.debug(f"Skipping non-Polygon geometry: {geom_type}")
                    continue

                # Get feature name
                properties = feature.get("properties", {})
                name = properties.get("name", "unknown")

                # Get coordinates - GeoJSON Polygon has nested arrays
                # coordinates[0] is the exterior ring
                coords = geometry.get("coordinates", [[]])[0]

                if len(coords) < 3:
                    logger.warning(f"Invalid polygon for {name}: less than 3 coordinates")
                    continue

                # Create Shapely polygon
                # GeoJSON uses [lon, lat], Shapely expects (lon, lat) tuples
                polygon = Polygon(coords)

                if not polygon.is_valid:
                    logger.warning(f"Invalid polygon geometry for {name}, attempting to fix")
                    polygon = polygon.buffer(0)  # Common fix for invalid polygons

                # Create prepared geometry for faster point-in-polygon checks
                prepared = prep(polygon)

                geofences[name] = GeofenceArea(
                    name=name, polygon=polygon, prepared_polygon=prepared
                )

            except Exception as e:
                logger.warning(f"Error parsing geofence feature: {e}")
                continue

        return geofences

    async def _fetch_and_update_geofences(self) -> bool:
        """Fetch geofences and update internal state. Returns True if successful."""
        new_geofences = await self._fetch_geofences()

        if not new_geofences:
            if not self._geofences:
                logger.warning("No geofences loaded and fetch returned empty")
                return False
            logger.warning("Fetch returned empty, keeping existing geofences")
            return False

        # Log changes
        old_names = set(self._geofences.keys())
        new_names = set(new_geofences.keys())

        added = new_names - old_names
        removed = old_names - new_names

        if added:
            logger.info(f"Added geofences: {', '.join(added)}")
        if removed:
            logger.info(f"Removed geofences: {', '.join(removed)}")

        self._geofences = new_geofences
        return True

    async def _refresh_loop(self) -> None:
        """Background task that refreshes geofences periodically."""
        while True:
            try:
                await asyncio.sleep(AppConfig.geofence_refresh_cache_seconds)
                logger.debug("Refreshing geofences...")
                success = await self._fetch_and_update_geofences()
                if success:
                    logger.debug(f"Geofence refresh complete: {len(self._geofences)} geofences")

            except asyncio.CancelledError:
                logger.debug("Geofence refresh loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in geofence refresh loop: {e}")
                # Continue loop even on error

    def is_point_in_geofence(self, lat: float, lon: float) -> Optional[str]:
        """
        Check if a point is within any geofence.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Geofence name if found, None otherwise.
            Uses prepared geometries for performance.
        """
        if not self._geofences:
            logger.debug("No geofences loaded, returning None")
            return None

        # Shapely uses (x, y) = (lon, lat)
        point = Point(lon, lat)

        for name, area in self._geofences.items():
            if area.prepared_polygon.contains(point):
                return name

        return None

    def get_all_geofence_names(self) -> List[str]:
        """Return list of all geofence names."""
        return list(self._geofences.keys())

    def get_geofence_count(self) -> int:
        """Return number of loaded geofences."""
        return len(self._geofences)

    async def shutdown(self) -> None:
        """Cancel refresh task and cleanup."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.close()

        logger.info("KojiGeofenceManager shutdown complete")
