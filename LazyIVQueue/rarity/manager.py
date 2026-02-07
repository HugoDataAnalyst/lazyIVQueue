"""Rarity Manager - Dynamic rarity tracking for Pokemon spawns."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple
from LazyIVQueue.utils.logger import logger
import LazyIVQueue.config as AppConfig


class RarityManager:
    """
    Singleton manager for dynamic rarity tracking.

    Modes:
    - Area-based (Koji enabled): Separate rankings per geofence area
    - Global (Koji disabled): Single "GLOBAL" bucket

    Features:
    - Tracks active spawns by area
    - Periodically ranks Pokemon by rarity (count ASC = rarest first)
    - Provides rarity rank lookup for queue prioritization
    - Calibration period before rankings are used
    """

    _instance: Optional[RarityManager] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self._status: str = "CALIBRATING"  # or "READY"
        self._start_time: float = time.time()

        # Active spawns: {area: {pokemon_key: [despawn_times]}}
        # pokemon_key format: "pokemon_id" or "pokemon_id:form"
        self._actives: Dict[str, Dict[str, List[int]]] = {}

        # Ranked lists: {area: [(pokemon_key, count), ...]} sorted by count ASC
        self._rankings: Dict[str, List[Tuple[str, int]]] = {}

        # Rank lookup cache: {area: {pokemon_key: rank}}
        self._rank_cache: Dict[str, Dict[str, int]] = {}

        # Global ranking: [(pokemon_key, area, count), ...] sorted by count ASC
        self._global_rankings: List[Tuple[str, str, int]] = []
        # Global rank lookup: {(pokemon_key, area): global_rank}
        self._global_rank_cache: Dict[Tuple[str, str], int] = {}

        self._manager_lock: asyncio.Lock = asyncio.Lock()
        self._initialized: bool = False

        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._ranking_task: Optional[asyncio.Task] = None

        # Stats
        self._total_spawns_tracked: int = 0
        self._last_ranking_time: Optional[float] = None
        self._last_log_count: int = 0  # For periodic logging

    @classmethod
    async def get_instance(cls) -> RarityManager:
        """Get or create singleton instance."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = RarityManager()
                await cls._instance.initialize()
            return cls._instance

    async def initialize(self) -> None:
        """Initialize the rarity manager and start background tasks."""
        if self._initialized:
            return

        self._initialized = True
        self._start_time = time.time()

        # Start background tasks
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._ranking_task = asyncio.create_task(self._ranking_loop())

        logger.info(
            f"RarityManager initialized. "
            f"Calibration: {AppConfig.calibration_minutes} min, "
            f"IV threshold: {AppConfig.iv_threshold}, "
            f"Cell threshold: {AppConfig.cell_threshold}"
        )

    async def add_spawn(
        self,
        pokemon_id: int,
        form: Optional[int],
        area: str,
        despawn_time: int,
    ) -> None:
        """
        Add a spawn to the census tracking.

        Args:
            pokemon_id: Pokemon ID
            form: Pokemon form (None for any form)
            area: Geofence area name or "GLOBAL"
            despawn_time: Unix timestamp when spawn despawns
        """
        # Build pokemon key
        if form is not None:
            pokemon_key = f"{pokemon_id}:{form}"
        else:
            pokemon_key = str(pokemon_id)

        should_log = False
        async with self._manager_lock:
            # Initialize area if needed
            if area not in self._actives:
                self._actives[area] = {}
                logger.opt(colors=True).info(f"<yellow>[*]</yellow> Census: New area discovered: {area}")

            # Initialize pokemon list if needed
            if pokemon_key not in self._actives[area]:
                self._actives[area][pokemon_key] = []

            # Add spawn
            self._actives[area][pokemon_key].append(despawn_time)
            self._total_spawns_tracked += 1

            # Log every 100 spawns
            if self._total_spawns_tracked - self._last_log_count >= 100:
                self._last_log_count = self._total_spawns_tracked
                should_log = True

        # Log outside the lock
        if should_log:
            self.log_census_status()

        logger.trace(f"Census: {pokemon_key} in {area} (despawn: {despawn_time})")

    def get_rarity_rank(
        self, pokemon_id: int, form: Optional[int], area: str
    ) -> Optional[int]:
        """
        Get rarity rank for a Pokemon.

        When FILTER_WITH_KOJI=FALSE (global mode), area is ignored - we just
        look up the Pokemon in the single "GLOBAL" bucket.

        Args:
            pokemon_id: Pokemon ID
            form: Pokemon form (None for any form)
            area: Geofence area name or "GLOBAL" (ignored in global mode)

        Returns:
            Rank (1 = rarest), or None if truly unknown.
            Returns a high rank if Pokemon exists but rankings not updated yet.
        """
        # Build pokemon keys - try both with form and without for flexible matching
        keys_to_try = []
        if form is not None:
            keys_to_try.append(f"{pokemon_id}:{form}")  # Exact form match first
        keys_to_try.append(str(pokemon_id))  # Any-form fallback

        # In global mode (no Koji), just use "GLOBAL" area regardless of what was passed
        lookup_area = "GLOBAL" if not AppConfig.filter_with_koji else area

        # Check rank cache with each key
        for pokemon_key in keys_to_try:
            if lookup_area in self._rank_cache and pokemon_key in self._rank_cache[lookup_area]:
                return self._rank_cache[lookup_area][pokemon_key]

        # Still no match - try finding any form of this pokemon in the area
        if lookup_area in self._rank_cache:
            for cached_key, rank in self._rank_cache[lookup_area].items():
                if cached_key == str(pokemon_id) or cached_key.startswith(f"{pokemon_id}:"):
                    return rank

        # Cache miss - check if Pokemon exists in _actives (seen in census but not ranked yet)
        if lookup_area in self._actives:
            for pokemon_key in keys_to_try:
                if pokemon_key in self._actives[lookup_area]:
                    # Pokemon exists but rankings haven't updated yet
                    return len(self._rank_cache.get(lookup_area, {})) + 1000

            # Also check for any form
            for active_key in self._actives[lookup_area]:
                if active_key == str(pokemon_id) or active_key.startswith(f"{pokemon_id}:"):
                    return len(self._rank_cache.get(lookup_area, {})) + 1000

        # Log cache miss for debugging
        logger.debug(
            f"Rarity cache miss: pokemon_id={pokemon_id}, form={form}, lookup_area={lookup_area} | "
            f"Keys tried: {keys_to_try} | "
            f"Areas in cache: {list(self._rank_cache.keys())}"
        )

        return None

    def is_ready(self) -> bool:
        """Check if calibration is complete and rankings are available."""
        return self._status == "READY"

    def get_status(self) -> str:
        """Get current status (CALIBRATING or READY)."""
        return self._status

    async def _cleanup_loop(self) -> None:
        """Background task to remove expired spawns."""
        while True:
            try:
                await asyncio.sleep(AppConfig.cleanup_interval_seconds)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                logger.debug("Rarity cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in rarity cleanup loop: {e}")

    async def _cleanup_expired(self) -> int:
        """Remove spawns that have despawned."""
        current_time = int(time.time())
        removed_count = 0

        async with self._manager_lock:
            for area in list(self._actives.keys()):
                for pokemon_key in list(self._actives[area].keys()):
                    # Filter out expired despawn times
                    original_count = len(self._actives[area][pokemon_key])
                    self._actives[area][pokemon_key] = [
                        t for t in self._actives[area][pokemon_key] if t > current_time
                    ]
                    removed = original_count - len(self._actives[area][pokemon_key])
                    removed_count += removed

                    # Remove empty pokemon entries
                    if not self._actives[area][pokemon_key]:
                        del self._actives[area][pokemon_key]

                # Remove empty area entries
                if not self._actives[area]:
                    del self._actives[area]

        if removed_count > 0:
            logger.opt(colors=True).info(f"<cyan>[~]</cyan> Census cleanup: removed {removed_count} expired spawns")

        return removed_count

    async def _ranking_loop(self) -> None:
        """Background task to recalculate rankings."""
        while True:
            try:
                await asyncio.sleep(AppConfig.ranking_interval_seconds)
                await self._recalculate_rankings()

                # Check if calibration is complete
                elapsed = time.time() - self._start_time
                if self._status == "CALIBRATING":
                    if elapsed >= AppConfig.calibration_minutes * 60:
                        self._status = "READY"
                        logger.info(
                            f"RarityManager calibration complete. "
                            f"Tracking {len(self._actives)} areas."
                        )

                # Log census status
                self.log_census_status()

            except asyncio.CancelledError:
                logger.debug("Rarity ranking task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in rarity ranking loop: {e}")

    async def _recalculate_rankings(self) -> None:
        """Recalculate rarity rankings for all areas and build global ranking."""
        async with self._manager_lock:
            new_rankings: Dict[str, List[Tuple[str, int]]] = {}
            new_cache: Dict[str, Dict[str, int]] = {}

            # Also build global ranking: [(pokemon_key, area, count), ...]
            global_counts: List[Tuple[str, str, int]] = []

            for area, pokemon_dict in self._actives.items():
                # Count active spawns per Pokemon
                counts: List[Tuple[str, int]] = []
                for pokemon_key, despawn_times in pokemon_dict.items():
                    count = len(despawn_times)
                    if count > 0:
                        counts.append((pokemon_key, count))
                        # Add to global list
                        global_counts.append((pokemon_key, area, count))

                # Sort by count ASC (lowest count = rarest)
                counts.sort(key=lambda x: x[1])
                new_rankings[area] = counts

                # Build per-area rank cache (ranks start at 1, 0 is reserved for unknown)
                new_cache[area] = {}
                for idx, (pokemon_key, _) in enumerate(counts):
                    new_cache[area][pokemon_key] = idx + 1  # Start from 1

            # Sort global rankings by count ASC (rarest first across all areas)
            global_counts.sort(key=lambda x: x[2])

            # Build global rank cache (ranks start at 1, 0 is reserved for unknown)
            new_global_cache: Dict[Tuple[str, str], int] = {}
            for idx, (pokemon_key, area, _) in enumerate(global_counts):
                new_global_cache[(pokemon_key, area)] = idx + 1  # Start from 1

            self._rankings = new_rankings
            self._rank_cache = new_cache
            self._global_rankings = global_counts
            self._global_rank_cache = new_global_cache
            self._last_ranking_time = time.time()

        # Log summary
        total_pokemon = sum(len(r) for r in self._rankings.values())
        would_queue = min(len(self._global_rankings), AppConfig.iv_threshold)
        logger.debug(
            f"Rarity rankings updated: {len(self._rankings)} areas, "
            f"{total_pokemon} unique Pokemon tracked, "
            f"{would_queue} would queue globally (threshold={AppConfig.iv_threshold})"
        )

    def log_census_status(self) -> None:
        """Log current census status with area breakdown."""
        elapsed = time.time() - self._start_time

        # Count active spawns
        total_active = 0
        area_summaries = []
        for area, pokemon_dict in self._actives.items():
            area_count = sum(len(times) for times in pokemon_dict.values())
            unique_count = len(pokemon_dict)
            total_active += area_count
            area_summaries.append(f"{area}:{unique_count}u/{area_count}a")

        # Count Pokemon that are actually rare enough to queue (rank <= threshold)
        rare_count = 0
        for area_rankings in self._rankings.values():
            for idx, _ in enumerate(area_rankings):
                if idx + 1 <= AppConfig.iv_threshold:  # rank is 1-indexed
                    rare_count += 1

        status_icon = "<yellow>[*]</yellow>" if self._status == "CALIBRATING" else "<cyan>[~]</cyan>"
        calibration_info = ""
        if self._status == "CALIBRATING":
            remaining = max(0, AppConfig.calibration_minutes * 60 - elapsed)
            calibration_info = f" | Calibrating: {int(remaining)}s remaining"

        # Count unique Pokemon across all areas (deduplicated)
        all_pokemon_keys = set()
        for pokemon_dict in self._actives.values():
            all_pokemon_keys.update(pokemon_dict.keys())
        unique_pokemon = len(all_pokemon_keys)

        logger.opt(colors=True).info(
            f"{status_icon} Census Status: {self._total_spawns_tracked} spawns received | "
            f"{unique_pokemon} unique pokemon | {total_active} active | {len(self._actives)} areas | "
            f"{rare_count} rare (rank<={AppConfig.iv_threshold}){calibration_info}"
        )

        # Log per-area breakdown at debug level
        if area_summaries:
            logger.debug(f"    Areas (unique/active): {', '.join(area_summaries)}")

    async def get_rankings(self, area: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        """
        Get full rarity rankings for areas.

        Args:
            area: Specific area to get rankings for, or None for all areas
            limit: Max Pokemon per area to return (default 100)

        Returns:
            Dict with rankings per area, sorted by rarity (rarest first)
        """
        result: Dict[str, Any] = {
            "status": self._status,
            "threshold": AppConfig.iv_threshold,
            "total_tracked_globally": len(self._global_rankings),
            "would_queue_globally": min(len(self._global_rankings), AppConfig.iv_threshold),
            "areas": {},
        }

        areas_to_check = [area] if area else list(self._rankings.keys())

        for area_name in areas_to_check:
            if area_name not in self._rankings:
                continue

            rankings = self._rankings[area_name][:limit]
            result["areas"][area_name] = {
                "total_pokemon": len(self._rankings[area_name]),
                "rankings": [
                    {
                        "area_rank": idx + 1,  # 1-based ranking (0 = unknown)
                        "global_rank": self._global_rank_cache.get((pk, area_name)),
                        "pokemon": pk,
                        "active_count": count,
                        "would_queue": self._global_rank_cache.get((pk, area_name), 0) <= AppConfig.iv_threshold,
                    }
                    for idx, (pk, count) in enumerate(rankings)
                ],
            }

        return result

    async def get_stats(self) -> Dict[str, Any]:
        """Return rarity manager statistics."""
        elapsed = time.time() - self._start_time
        calibration_remaining = max(0, AppConfig.calibration_minutes * 60 - elapsed)

        # Count active spawns
        total_active = 0
        area_stats = {}
        for area, pokemon_dict in self._actives.items():
            area_count = sum(len(times) for times in pokemon_dict.values())
            total_active += area_count
            area_stats[area] = {
                "unique_pokemon": len(pokemon_dict),
                "active_spawns": area_count,
            }

        # Get top 10 rarest per area
        top_rarest = {}
        for area, rankings in self._rankings.items():
            top_rarest[area] = [
                {"pokemon": pk, "count": count}
                for pk, count in rankings[:10]
            ]

        return {
            "status": self._status,
            "calibration_remaining_seconds": int(calibration_remaining) if self._status == "CALIBRATING" else 0,
            "total_spawns_tracked": self._total_spawns_tracked,
            "total_active_spawns": total_active,
            "areas_tracked": len(self._actives),
            "last_ranking_time": self._last_ranking_time,
            "config": {
                "calibration_minutes": AppConfig.calibration_minutes,
                "iv_threshold": AppConfig.iv_threshold,
                "cell_threshold": AppConfig.cell_threshold,
                "ranking_interval_seconds": AppConfig.ranking_interval_seconds,
            },
            "by_area": area_stats,
            "top_rarest_by_area": top_rarest,
        }

    async def stop(self) -> None:
        """Stop background tasks."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        if self._ranking_task:
            self._ranking_task.cancel()
            try:
                await self._ranking_task
            except asyncio.CancelledError:
                pass

        logger.info("RarityManager stopped")
