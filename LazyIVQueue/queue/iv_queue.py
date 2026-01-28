"""IV Queue Manager - In-memory priority queue for Pokemon needing IV data."""
from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from LazyIVQueue.utils.logger import logger
from LazyIVQueue.utils.geo_utils import is_within_distance, COORDINATE_MATCH_THRESHOLD_METERS
import LazyIVQueue.config as AppConfig


@dataclass(order=True)
class QueueEntry:
    """
    Entry in the IV queue.

    Ordering is by (priority, timestamp) for heapq.
    Lower priority number = higher priority (processed first).
    """

    # Comparison fields (used for heap ordering)
    priority: int = field(compare=True)
    timestamp: float = field(compare=True, default_factory=time.time)

    # Non-comparison fields
    pokemon_id: int = field(compare=False, default=0)
    form: Optional[int] = field(compare=False, default=None)
    area: str = field(compare=False, default="")
    lat: float = field(compare=False, default=0.0)
    lon: float = field(compare=False, default=0.0)
    spawnpoint_id: Optional[str] = field(compare=False, default=None)
    encounter_id: Optional[str] = field(compare=False, default=None)
    disappear_time: Optional[int] = field(compare=False, default=None)

    # Seen type for scouting strategy
    seen_type: str = field(compare=False, default="wild")  # "wild", "nearby_stop", or "nearby_cell"
    s2_cell_id: Optional[str] = field(compare=False, default=None)  # S2 level-15 cell ID (for nearby_cell)

    # Source list for tracking
    list_type: str = field(compare=False, default="unknown")  # "ivlist", "celllist", or "auto_rarity"

    # Tracking fields
    is_removed: bool = field(compare=False, default=False)
    is_scouting: bool = field(compare=False, default=False)
    was_scouted: bool = field(compare=False, default=False)  # True after scout sent, waiting for IV
    scout_started_at: Optional[float] = field(compare=False, default=None)

    @property
    def unique_key(self) -> str:
        """Unique identifier for deduplication."""
        if self.encounter_id:
            return self.encounter_id
        if self.spawnpoint_id:
            return f"{self.spawnpoint_id}:{self.pokemon_id}"
        return f"{self.lat:.6f}:{self.lon:.6f}:{self.pokemon_id}"

    @property
    def pokemon_display(self) -> str:
        """Human-readable pokemon identifier."""
        if self.form is not None:
            return f"{self.pokemon_id}:{self.form}"
        return str(self.pokemon_id)


class IVQueueManager:
    """
    Priority queue manager for Pokemon needing IV data.

    Features:
    - Heap-based priority queue (lower priority number = higher priority)
    - Deduplication by encounter_id/spawnpoint_id
    - Concurrent scout tracking with semaphore
    - Proximity-based matching for removal
    """

    _instance: Optional[IVQueueManager] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self._heap: List[QueueEntry] = []
        self._entries: Dict[str, QueueEntry] = {}  # key -> entry for O(1) lookup
        self._scout_semaphore: Optional[asyncio.Semaphore] = None
        self._current_concurrency: int = 0
        self._active_scouts: int = 0
        self._queue_lock: asyncio.Lock = asyncio.Lock()
        self._initialized: bool = False

        # Stats counters by seen_type
        self._seen_types = ["wild", "nearby_stop", "nearby_cell"]
        self._queued_by_type: Dict[str, int] = {t: 0 for t in self._seen_types}
        self._matches_by_type: Dict[str, int] = {t: 0 for t in self._seen_types}
        self._early_iv_by_type: Dict[str, int] = {t: 0 for t in self._seen_types}
        self._timeouts_by_type: Dict[str, int] = {t: 0 for t in self._seen_types}

        # Per-Pokemon breakdown by seen_type (key: seen_type -> pokemon_display -> count)
        self._queued_by_pokemon: Dict[str, Dict[str, int]] = {t: {} for t in self._seen_types}
        self._matches_by_pokemon: Dict[str, Dict[str, int]] = {t: {} for t in self._seen_types}
        self._early_iv_by_pokemon: Dict[str, Dict[str, int]] = {t: {} for t in self._seen_types}
        self._timeouts_by_pokemon: Dict[str, Dict[str, int]] = {t: {} for t in self._seen_types}

    @classmethod
    async def get_instance(cls) -> IVQueueManager:
        """Get or create singleton instance."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = IVQueueManager()
                await cls._instance.initialize()
            return cls._instance

    async def initialize(self) -> None:
        """Initialize the queue manager."""
        if self._initialized:
            return

        self._scout_semaphore = asyncio.Semaphore(AppConfig.concurrency_scout)
        self._current_concurrency = AppConfig.concurrency_scout
        self._initialized = True
        logger.info(f"IVQueue initialized with concurrency limit: {AppConfig.concurrency_scout}")

    async def update_concurrency(self, new_concurrency: int) -> None:
        """
        Update scout concurrency limit by recreating the semaphore.

        Note: This is a best-effort operation. Active scouts will continue
        until they complete. New concurrency takes effect for new scouts.

        Args:
            new_concurrency: New concurrency limit
        """
        async with self._queue_lock:
            old_concurrency = self._current_concurrency
            # Create new semaphore with new limit
            self._scout_semaphore = asyncio.Semaphore(new_concurrency)
            self._current_concurrency = new_concurrency
            logger.info(
                f"Scout concurrency updated: {old_concurrency} -> {new_concurrency}"
            )

    async def add(self, entry: QueueEntry) -> bool:
        """
        Add entry to queue.

        Args:
            entry: QueueEntry to add

        Returns:
            True if added, False if duplicate
        """
        async with self._queue_lock:
            key = entry.unique_key

            # Check for duplicate
            if key in self._entries:
                logger.debug(f"Duplicate entry skipped: {key}")
                return False

            # Add to heap and lookup dict
            heapq.heappush(self._heap, entry)
            self._entries[key] = entry

            # Update stats by seen_type
            seen_type = entry.seen_type
            self._queued_by_type[seen_type] = self._queued_by_type.get(seen_type, 0) + 1
            self._queued_by_pokemon[seen_type][entry.pokemon_display] = (
                self._queued_by_pokemon[seen_type].get(entry.pokemon_display, 0) + 1
            )

            logger.debug(
                f"Added to queue: {entry.pokemon_display} in {entry.area} "
                f"[{entry.seen_type}] (priority {entry.priority}, queue size: {len(self._entries)})"
            )
            return True

    async def remove_by_match(
        self, encounter_id: Optional[str], lat: float, lon: float
    ) -> Optional[QueueEntry]:
        """
        Remove entry matching by encounter_id (exact) or coordinates (70m proximity).

        Matching order:
        1. Exact encounter_id match (if provided)
        2. Coordinate proximity match (70m threshold)

        Args:
            encounter_id: Encounter ID to match (exact match, preferred)
            lat: Latitude for proximity match
            lon: Longitude for proximity match

        Returns:
            Removed entry if found, None otherwise
        """
        async with self._queue_lock:
            # First try exact encounter_id match
            if encounter_id:
                for key, entry in list(self._entries.items()):
                    if entry.encounter_id == encounter_id and not entry.is_removed:
                        return self._remove_entry(key)

            # Then try coordinate proximity match (fallback)
            for key, entry in list(self._entries.items()):
                if entry.is_removed:
                    continue
                if is_within_distance(
                    entry.lat, entry.lon, lat, lon, COORDINATE_MATCH_THRESHOLD_METERS
                ):
                    return self._remove_entry(key)

            return None

    async def remove_by_cell_match(
        self, pokemon_id: int, form: Optional[int], s2_cell_id: str
    ) -> Optional[QueueEntry]:
        """
        Remove ONE entry matching pokemon and S2 cell (for nearby_cell scouting).

        Only matches entries that are currently being scouted or have been scouted
        (was_scouted=True OR is_scouting=True).

        Args:
            pokemon_id: Pokemon ID to match
            form: Pokemon form to match (None matches any form)
            s2_cell_id: S2 cell ID to match

        Returns:
            Removed entry if found, None otherwise
        """
        async with self._queue_lock:
            for key, entry in list(self._entries.items()):
                if entry.is_removed:
                    continue
                # Must be a nearby_cell entry with matching s2_cell_id
                if entry.seen_type != "nearby_cell" or entry.s2_cell_id != s2_cell_id:
                    continue
                # Must match pokemon_id
                if entry.pokemon_id != pokemon_id:
                    continue
                # Form matching: if incoming form is not None, must match
                if form is not None and entry.form != form:
                    continue
                # Must be scouting or scouted (not just pending)
                if not entry.is_scouting and not entry.was_scouted:
                    continue
                # Found match - remove only this one
                return self._remove_entry(key)

            return None

    def _remove_entry(self, key: str) -> Optional[QueueEntry]:
        """Remove entry by key (internal, must hold lock). Does not update stats."""
        if key not in self._entries:
            return None

        entry = self._entries.pop(key)
        # Mark as removed for lazy deletion from heap
        entry.is_removed = True

        logger.debug(
            f"Removed from queue: {entry.pokemon_display} "
            f"(queue size: {len(self._entries)})"
        )
        return entry

    def record_match(self, pokemon_display: str, seen_type: str) -> None:
        """Record a successful IV match (after scouting)."""
        self._matches_by_type[seen_type] = self._matches_by_type.get(seen_type, 0) + 1
        self._matches_by_pokemon[seen_type][pokemon_display] = (
            self._matches_by_pokemon[seen_type].get(pokemon_display, 0) + 1
        )

    def record_early_iv(self, pokemon_display: str, seen_type: str) -> None:
        """Record an early IV (received before scouting)."""
        self._early_iv_by_type[seen_type] = self._early_iv_by_type.get(seen_type, 0) + 1
        self._early_iv_by_pokemon[seen_type][pokemon_display] = (
            self._early_iv_by_pokemon[seen_type].get(pokemon_display, 0) + 1
        )

    def record_timeout(self, pokemon_display: str, seen_type: str) -> None:
        """Record a scout timeout."""
        self._timeouts_by_type[seen_type] = self._timeouts_by_type.get(seen_type, 0) + 1
        self._timeouts_by_pokemon[seen_type][pokemon_display] = (
            self._timeouts_by_pokemon[seen_type].get(pokemon_display, 0) + 1
        )

    async def get_next_for_scout(self) -> Optional[QueueEntry]:
        """
        Get next highest priority entry not currently being scouted.

        Uses semaphore to limit concurrent scouts.
        Returns None if no entries available or at concurrency limit.
        """
        # Try to acquire semaphore slot without blocking
        acquired = self._scout_semaphore.locked()
        if acquired:
            # Semaphore is fully locked, check if we can get a slot
            try:
                # Non-blocking acquire attempt
                got_slot = self._scout_semaphore._value > 0
                if not got_slot:
                    return None
            except Exception:
                return None

        # Acquire the semaphore slot
        await self._scout_semaphore.acquire()

        async with self._queue_lock:
            # Clean up heap (lazy deletion) and find valid entry
            while self._heap:
                entry = self._heap[0]
                key = entry.unique_key

                # Skip if already removed, being scouted, or already scouted (waiting for IV)
                if key not in self._entries or entry.is_removed or entry.is_scouting or entry.was_scouted:
                    heapq.heappop(self._heap)
                    continue

                # Found valid entry
                heapq.heappop(self._heap)
                entry.is_scouting = True
                entry.scout_started_at = time.time()
                self._active_scouts += 1

                logger.debug(
                    f"Dispatching for scout: {entry.pokemon_display} in {entry.area} "
                    f"(active scouts: {self._active_scouts})"
                )
                return entry

            # No entries available, release semaphore
            self._scout_semaphore.release()
            return None

    async def mark_scout_complete(self, entry: QueueEntry, success: bool) -> None:
        """
        Mark a scout operation as complete and release semaphore slot.

        Note: This does NOT remove the entry from the queue. The entry stays
        in the queue until a matching IV webhook arrives (remove_by_match).
        This ensures we can properly match returning IV data.

        Args:
            entry: The queue entry that was being scouted
            success: Whether the scout was successful
        """
        async with self._queue_lock:
            # Mark entry as no longer actively scouting, but keep it in queue
            # for matching with incoming IV webhooks
            entry.is_scouting = False
            entry.was_scouted = True  # Mark as scouted, waiting for IV match
            self._active_scouts = max(0, self._active_scouts - 1)

        self._scout_semaphore.release()

        status = "sent" if success else "failed"
        logger.debug(
            f"Scout {status} for {entry.pokemon_display}, "
            f"active scouts: {self._active_scouts}, waiting for IV match"
        )

    def get_active_scouts_count(self) -> int:
        """Return count of currently active scouts."""
        return self._active_scouts

    def get_queue_size(self) -> int:
        """Return current queue size (excluding entries being scouted)."""
        return len(self._entries)

    def get_available_slots(self) -> int:
        """Return number of available scout slots."""
        return AppConfig.concurrency_scout - self._active_scouts

    def _get_total_from_type_dict(self, type_dict: Dict[str, int]) -> int:
        """Sum all values in a seen_type dict."""
        return sum(type_dict.values())

    def _build_type_stats(self, type_dict: Dict[str, int]) -> Dict[str, Any]:
        """Build stats dict with total and per-type breakdown."""
        return {
            "total": self._get_total_from_type_dict(type_dict),
            "wild": type_dict.get("wild", 0),
            "nearby_stop": type_dict.get("nearby_stop", 0),
            "nearby_cell": type_dict.get("nearby_cell", 0),
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Return queue statistics."""
        # Count entries waiting for IV match
        waiting_for_iv = sum(1 for e in self._entries.values() if e.was_scouted and not e.is_removed)
        pending = len(self._entries) - waiting_for_iv

        return {
            "queue_size": len(self._entries),
            "pending": pending,
            "awaiting_iv": waiting_for_iv,
            "active_scouts": self._active_scouts,
            "max_concurrency": AppConfig.concurrency_scout,
            "available_slots": self.get_available_slots(),
            "session": {
                "total_queued": self._build_type_stats(self._queued_by_type),
                "total_matches": self._build_type_stats(self._matches_by_type),
                "total_early_iv": self._build_type_stats(self._early_iv_by_type),
                "total_timeouts": self._build_type_stats(self._timeouts_by_type),
                "by_pokemon": {
                    "wild": {
                        "queued": self._queued_by_pokemon.get("wild", {}),
                        "matches": self._matches_by_pokemon.get("wild", {}),
                        "early_iv": self._early_iv_by_pokemon.get("wild", {}),
                        "timeouts": self._timeouts_by_pokemon.get("wild", {}),
                    },
                    "nearby_stop": {
                        "queued": self._queued_by_pokemon.get("nearby_stop", {}),
                        "matches": self._matches_by_pokemon.get("nearby_stop", {}),
                        "early_iv": self._early_iv_by_pokemon.get("nearby_stop", {}),
                        "timeouts": self._timeouts_by_pokemon.get("nearby_stop", {}),
                    },
                    "nearby_cell": {
                        "queued": self._queued_by_pokemon.get("nearby_cell", {}),
                        "matches": self._matches_by_pokemon.get("nearby_cell", {}),
                        "early_iv": self._early_iv_by_pokemon.get("nearby_cell", {}),
                        "timeouts": self._timeouts_by_pokemon.get("nearby_cell", {}),
                    },
                },
            },
        }

    def get_next_entries_preview(self, count: int = 10) -> List[Dict[str, Any]]:
        """
        Get preview of the next N entries that will be processed.

        Args:
            count: Number of entries to preview (default: 10)

        Returns:
            List of entry info dicts in priority order
        """
        # Build a sorted list of valid entries (not yet scouted)
        valid_entries = []
        for entry in self._entries.values():
            if not entry.is_removed and not entry.is_scouting and not entry.was_scouted:
                valid_entries.append(entry)

        # Sort by priority, then timestamp
        valid_entries.sort(key=lambda e: (e.priority, e.timestamp))

        # Return preview of top N
        preview = []
        for entry in valid_entries[:count]:
            preview.append({
                "pokemon": entry.pokemon_display,
                "area": entry.area,
                "priority": entry.priority,
                "lat": round(entry.lat, 6),
                "lon": round(entry.lon, 6),
                "encounter_id": entry.encounter_id,
            })

        return preview

    def log_queue_status(self) -> None:
        """Log current queue status with next 10 entries preview."""
        queue_size = len(self._entries)
        heap_size = len(self._heap)

        # Count entries waiting for IV match
        waiting_for_iv = sum(1 for e in self._entries.values() if e.was_scouted and not e.is_removed)
        currently_scouting = sum(1 for e in self._entries.values() if e.is_scouting and not e.is_removed)
        pending = queue_size - waiting_for_iv - currently_scouting

        # Calculate totals
        total_queued = self._get_total_from_type_dict(self._queued_by_type)
        total_matches = self._get_total_from_type_dict(self._matches_by_type)
        total_early = self._get_total_from_type_dict(self._early_iv_by_type)
        total_timeouts = self._get_total_from_type_dict(self._timeouts_by_type)

        logger.info(
            f"IVQueue Status: {pending} pending | {currently_scouting} scouting | "
            f"{waiting_for_iv} awaiting IV | heap={heap_size} | "
            f"Session: {total_queued} queued / {total_matches} matches / {total_early} early / {total_timeouts} timeouts"
        )

        if queue_size > 0:
            preview = self.get_next_entries_preview(10)
            if preview:
                logger.debug("Next entries in queue:")
                for i, entry in enumerate(preview, 1):
                    logger.debug(
                        f"  {i}. Pokemon {entry['pokemon']} in {entry['area']} "
                        f"(priority {entry['priority']})"
                    )

    async def cleanup_expired(self) -> int:
        """
        Remove entries that have expired (disappear_time has passed).

        Returns:
            Number of entries removed
        """
        current_time = int(time.time())
        removed_count = 0

        async with self._queue_lock:
            for key, entry in list(self._entries.items()):
                if entry.disappear_time and entry.disappear_time < current_time:
                    entry.is_removed = True
                    del self._entries[key]
                    removed_count += 1

        if removed_count > 0:
            logger.debug(f"Cleaned up {removed_count} expired queue entries")

        return removed_count

    async def cleanup_timed_out_scouts(self) -> int:
        """
        Remove entries that timed out waiting for IV data.

        Any entry with scout_started_at that exceeds timeout_iv is removed.
        This covers both stuck scouts and scouts waiting for IV data.

        Uses AppConfig.timeout_iv to determine timeout threshold.

        Returns:
            Number of entries removed
        """
        current_time = time.time()
        timeout_threshold = AppConfig.timeout_iv
        removed_count = 0

        async with self._queue_lock:
            for key, entry in list(self._entries.items()):
                # Check if scout started and exceeded timeout
                if entry.scout_started_at:
                    elapsed = current_time - entry.scout_started_at
                    if elapsed > timeout_threshold:
                        logger.warning(
                            f"[x] Scout timeout: Pokemon {entry.pokemon_display} in {entry.area} "
                            f"[encounter_id: {entry.encounter_id}] - no IV after {int(elapsed)}s"
                        )
                        pokemon_display = entry.pokemon_display
                        seen_type = entry.seen_type
                        entry.is_removed = True
                        del self._entries[key]
                        removed_count += 1
                        # Update timeout stats by seen_type
                        self._timeouts_by_type[seen_type] = self._timeouts_by_type.get(seen_type, 0) + 1
                        self._timeouts_by_pokemon[seen_type][pokemon_display] = (
                            self._timeouts_by_pokemon[seen_type].get(pokemon_display, 0) + 1
                        )

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} timed out scout entries")

        return removed_count
