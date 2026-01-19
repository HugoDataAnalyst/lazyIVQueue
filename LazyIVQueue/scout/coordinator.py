"""Scout Coordinator - Async loop coordinating scouting operations with Dragonite API."""
from __future__ import annotations

import asyncio
from typing import Optional, Dict, Any

from LazyIVQueue.utils.logger import logger
from LazyIVQueue.utils.s2_utils import generate_9_point_grid, generate_honeycomb_coords, get_s2_cell_id
from LazyIVQueue.queue.iv_queue import IVQueueManager, QueueEntry
from LazyIVQueue.DragoniteAPI import get_dragonite_client
from LazyIVQueue.DragoniteAPI.utils.http_api import APIClient
from LazyIVQueue.DragoniteAPI.endpoints.scout import scout_single, scout_v2
import LazyIVQueue.config as AppConfig


class ScoutCoordinator:
    """
    Coordinates scouting operations with Dragonite API.

    Features:
    - Async loop checking queue for available scout slots
    - Respects concurrency limit via IVQueueManager semaphore
    - Handles scout API errors gracefully
    - Tracks scout success/failure metrics
    """

    _instance: Optional[ScoutCoordinator] = None

    def __init__(self) -> None:
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[APIClient] = None
        self._check_interval: float = 0.5  # seconds between queue checks

        # Metrics
        self._total_scouts: int = 0
        self._successful_scouts: int = 0
        self._failed_scouts: int = 0

    @classmethod
    async def get_instance(cls) -> ScoutCoordinator:
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = ScoutCoordinator()
        return cls._instance

    async def start(self) -> None:
        """Start the scout coordinator loop."""
        if self._running:
            logger.warning("ScoutCoordinator already running")
            return

        self._running = True

        # Create Dragonite API client
        self._client = get_dragonite_client()

        # Start the API client session
        await self._client.__aenter__()

        # Start the main loop
        self._task = asyncio.create_task(self._run_loop())

        logger.info(
            f"ScoutCoordinator started (concurrency: {AppConfig.concurrency_scout})"
        )

    async def _run_loop(self) -> None:
        """Main coordinator loop - continuously checks for entries to scout."""
        queue = await IVQueueManager.get_instance()

        while self._running:
            try:
                # Get next entry to scout (respects concurrency limit)
                entry = await queue.get_next_for_scout()

                if entry:
                    # Spawn scout task (don't await - let it run concurrently)
                    asyncio.create_task(self._execute_scout(entry))
                else:
                    # No entries available or at concurrency limit
                    await asyncio.sleep(self._check_interval)

            except asyncio.CancelledError:
                logger.debug("Scout coordinator loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in scout coordinator loop: {e}")
                await asyncio.sleep(self._check_interval)

    async def _execute_scout(self, entry: QueueEntry) -> None:
        """
        Execute a single scout operation.

        For nearby_cell: Uses honeycomb pattern (7 coordinates)
        For wild/nearby_stop: Uses single coordinate

        Calls Dragonite API v2: POST /v2/scout with {username, locations, options}
        """
        queue = await IVQueueManager.get_instance()
        success = False

        try:
            # Determine coordinates based on seen_type
            if entry.seen_type == "nearby_cell":
                # Generate s2cell id
                s2_cell_int = get_s2_cell_id(entry.lat, entry.lon, level=15)
                # Generate 9x9 grid
                coordinates = generate_9_point_grid(s2_cell_int)

                # coordinates = generate_honeycomb_coords(entry.lat, entry.lon)
                coord_count = len(coordinates)
                logger.debug(
                    f"Sending S2 Grid scout request: Pokemon {entry.pokemon_display} "
                    f"at ({entry.lat:.6f}, {entry.lon:.6f}) in {entry.area} "
                    f"[s2_cell: {entry.s2_cell_id}] ({coord_count} coords)"
                )
                response = await scout_v2(self._client, coordinates)
            else:
                # Single coordinate for wild/nearby_stop
                coord_count = 1
                logger.debug(
                    f"Sending scout request: Pokemon {entry.pokemon_display} "
                    f"at ({entry.lat:.6f}, {entry.lon:.6f}) in {entry.area} "
                    f"[encounter_id: {entry.encounter_id}]"
                )
                response = await scout_single(self._client, entry.lat, entry.lon)

            self._total_scouts += 1
            success = True
            self._successful_scouts += 1

            # Log based on seen_type
            if entry.seen_type == "nearby_cell":
                logger.info(
                    f"[>] Scout sent: Pokemon {entry.pokemon_display} in {entry.area} "
                    f"at ({entry.lat:.6f}, {entry.lon:.6f}) [s2_cell: {entry.s2_cell_id}] "
                    f"(honeycomb: {coord_count} coords)"
                )
            else:
                logger.info(
                    f"[>] Scout sent: Pokemon {entry.pokemon_display} in {entry.area} "
                    f"at ({entry.lat:.6f}, {entry.lon:.6f}) [encounter_id: {entry.encounter_id}]"
                )
            logger.debug(
                f"Scout response: {response}"
            )
            logger.debug(
                f"Scout stats: total={self._total_scouts}, success={self._successful_scouts}, "
                f"queue={queue.get_queue_size()}, active={queue.get_active_scouts_count()}"
            )

        except Exception as e:
            self._total_scouts += 1
            self._failed_scouts += 1
            logger.error(
                f"[!] Scout failed: Pokemon {entry.pokemon_display} "
                f"[encounter_id: {entry.encounter_id}] - {e}"
            )

        finally:
            # Mark scout complete and release semaphore slot
            await queue.mark_scout_complete(entry, success)

    async def stop(self) -> None:
        """Stop the coordinator gracefully."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._client:
            await self._client.__aexit__(None, None, None)

        logger.info(
            f"ScoutCoordinator stopped. "
            f"Total: {self._total_scouts}, "
            f"Success: {self._successful_scouts}, "
            f"Failed: {self._failed_scouts}"
        )

    def get_stats(self) -> Dict[str, Any]:
        """Return coordinator statistics."""
        return {
            "total_scouts": self._total_scouts,
            "successful_scouts": self._successful_scouts,
            "failed_scouts": self._failed_scouts,
            "running": self._running,
        }
