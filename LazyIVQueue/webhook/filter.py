"""Webhook Filter - Dual filter logic for IV and non-IV Pokemon.

Priority Tiers (lower = higher priority):
  - Tier 0 (0-999): VIP lists (celllist + ivlist) - position in list determines sub-priority
  - Tier 1000+: auto_rarity entries - 1000 for unknown, 1000+rank for ranked Pokemon

This ensures ivlist/celllist ALWAYS take priority over auto_rarity.
"""
from __future__ import annotations

import time as time_module
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from LazyIVQueue.utils.logger import logger
from LazyIVQueue.utils.koji_geofences import KojiGeofenceManager
from LazyIVQueue.utils.geo_utils import is_within_distance, COORDINATE_MATCH_THRESHOLD_METERS
from LazyIVQueue.utils.s2_utils import get_s2_cell_id
from LazyIVQueue.queue.iv_queue import IVQueueManager, QueueEntry
from LazyIVQueue.rarity.manager import RarityManager
import LazyIVQueue.config as AppConfig


@dataclass
class PokemonData:
    """Parsed Pokemon webhook data."""

    pokemon_id: int
    form: Optional[int]
    latitude: float
    longitude: float
    spawnpoint_id: Optional[str]
    individual_attack: Optional[int]  # None = no IV data
    individual_defense: Optional[int]
    individual_stamina: Optional[int]
    encounter_id: Optional[str]
    disappear_time: Optional[int]
    seen_type: str  # "wild", "nearby_stop", or "nearby_cell"

    @property
    def has_iv(self) -> bool:
        """Check if Pokemon has IV data."""
        return self.individual_attack is not None

    @property
    def ivlist_key(self) -> str:
        """Get key for ivlist lookup (pokemon_id:form)."""
        if self.form is not None:
            return f"{self.pokemon_id}:{self.form}"
        return str(self.pokemon_id)

    @property
    def ivlist_key_any_form(self) -> str:
        """Get key for any-form lookup (just pokemon_id)."""
        return str(self.pokemon_id)

    @property
    def pokemon_display(self) -> str:
        """Human-readable pokemon identifier."""
        if self.form is not None:
            return f"{self.pokemon_id}:{self.form}"
        return str(self.pokemon_id)

    @property
    def iv_total(self) -> int:
        """Total IV value (0-45)."""
        if not self.has_iv:
            return 0
        return (
            (self.individual_attack or 0)
            + (self.individual_defense or 0)
            + (self.individual_stamina or 0)
        )

    @property
    def iv_percent(self) -> float:
        """IV percentage (0-100)."""
        return round(self.iv_total / 45 * 100, 1)


def parse_pokemon_data(raw: Dict[str, Any]) -> Optional[PokemonData]:
    """
    Parse raw webhook payload into PokemonData.

    Expected fields from Golbat:
    - pokemon_id: int
    - form: int (optional)
    - latitude: float
    - longitude: float
    - spawnpoint_id: str (optional)
    - individual_attack: int (optional, None if not scanned)
    - individual_defense: int (optional)
    - individual_stamina: int (optional)
    - encounter_id: str
    - disappear_time: int (unix timestamp)
    """
    try:
        pokemon_id = raw.get("pokemon_id")
        latitude = raw.get("latitude")
        longitude = raw.get("longitude")

        # Validate required fields
        if pokemon_id is None or latitude is None or longitude is None:
            logger.debug(f"Missing required Pokemon fields: {raw.keys()}")
            return None

        return PokemonData(
            pokemon_id=int(pokemon_id),
            form=raw.get("form"),
            latitude=float(latitude),
            longitude=float(longitude),
            spawnpoint_id=raw.get("spawnpoint_id"),
            individual_attack=raw.get("individual_attack"),
            individual_defense=raw.get("individual_defense"),
            individual_stamina=raw.get("individual_stamina"),
            encounter_id=raw.get("encounter_id"),
            disappear_time=raw.get("disappear_time"),
            seen_type=raw.get("seen_type", "wild"),
        )
    except (ValueError, TypeError) as e:
        logger.warning(f"Error parsing Pokemon data: {e}")
        return None


def is_in_ivlist(pokemon: PokemonData) -> Tuple[bool, Optional[int]]:
    """
    Check if Pokemon matches ivlist.

    Returns:
        (matches: bool, priority: Optional[int])
    """
    # First check exact match (pokemon_id:form)
    if pokemon.form is not None:
        key = f"{pokemon.pokemon_id}:{pokemon.form}"
        if key in AppConfig.ivlist_parsed:
            return True, AppConfig.ivlist_parsed[key]

    # Then check any-form match (just pokemon_id)
    key = str(pokemon.pokemon_id)
    if key in AppConfig.ivlist_parsed:
        return AppConfig.ivlist_parsed[key] is not None, AppConfig.ivlist_parsed.get(key)

    return False, None


def is_in_celllist(pokemon: PokemonData) -> Tuple[bool, Optional[int]]:
    """
    Check if Pokemon matches celllist (for nearby_cell scouting).

    Returns:
        (matches: bool, priority: Optional[int])
        Priority uses tier 0 (highest) for celllist entries.
    """
    # First check exact match (pokemon_id:form)
    if pokemon.form is not None:
        key = f"{pokemon.pokemon_id}:{pokemon.form}"
        if key in AppConfig.celllist_parsed:
            # Tier 0: celllist (highest priority)
            # Sub-priority within tier based on position in list
            return True, AppConfig.celllist_parsed[key]

    # Then check any-form match (just pokemon_id)
    key = str(pokemon.pokemon_id)
    if key in AppConfig.celllist_parsed:
        if AppConfig.celllist_parsed[key] is not None:
            return True, AppConfig.celllist_parsed[key]

    return False, None


def is_in_any_list(pokemon: PokemonData) -> bool:
    """Check if Pokemon matches either ivlist or celllist."""
    matches_iv, _ = is_in_ivlist(pokemon)
    matches_cell, _ = is_in_celllist(pokemon)
    return matches_iv or matches_cell


async def process_pokemon_webhook(raw_data: Dict[str, Any]) -> None:
    """
    Main entry point for processing Pokemon webhooks.
    Routes to appropriate filter based on IV presence.
    """
    pokemon = parse_pokemon_data(raw_data)
    if not pokemon:
        return

    if pokemon.has_iv:
        await filter_iv_pokemon(pokemon)
    else:
        await filter_non_iv_pokemon(pokemon)


async def filter_non_iv_pokemon(pokemon: PokemonData) -> None:
    """
    Filter for Pokemon WITHOUT IV data.

    Checks:
    1. Pokemon has NO IV data (individual_attack is None) - already ensured by caller
    2. For nearby_cell: ONLY check celllist (ivlist ignored)
       For wild/nearby_stop: Check ivlist first (VIP), then auto_rarity if enabled
    3. Coordinates inside Koji geofences (if FILTER_WITH_KOJI is enabled)

    If all pass: Add to IV queue and trigger scout
    """
    # Check 2: Determine priority from celllist, ivlist, or auto_rarity
    priority: Optional[int] = None
    seen_type = pokemon.seen_type
    s2_cell_id: Optional[str] = None
    list_type = "unknown"

    # Skip unsupported seen_types (e.g., lure_wild, lure_pokestop)
    supported_seen_types = {"wild", "nearby_stop", "nearby_cell"}
    if seen_type not in supported_seen_types:
        logger.debug(f"Skipping unsupported seen_type: {seen_type}")
        return

    if seen_type == "nearby_cell":
        # nearby_cell: ONLY check celllist (no auto_rarity for cell scouting)
        matches_cell, cell_priority = is_in_celllist(pokemon)
        if matches_cell and cell_priority is not None:
            priority = cell_priority
            s2_cell_id = get_s2_cell_id(pokemon.latitude, pokemon.longitude)
            list_type = "celllist"
        else:
            # Not in celllist = skip entirely (don't fall through to ivlist)
            logger.trace(f"Pokemon {pokemon.pokemon_display} nearby_cell not in celllist, skipping")
            return
    else:
        # wild/nearby_stop: Check ivlist first (VIP override)
        matches_iv, iv_priority = is_in_ivlist(pokemon)
        if matches_iv and iv_priority is not None:
            # Tier 0: ivlist VIP (same tier as celllist, uses position in list)
            priority = iv_priority
            list_type = "ivlist"
        elif AppConfig.auto_rarity_enabled:
            # Auto Rarity fallback
            rarity_manager = await RarityManager.get_instance()

            # Check if calibration complete
            if not rarity_manager.is_ready():
                # Still calibrating - check if we have VIP lists to fall back to
                has_vip_lists = bool(AppConfig.ivlist) or bool(AppConfig.celllist)
                if has_vip_lists:
                    # VIP lists exist but this Pokemon isn't in them - skip during calibration
                    logger.trace(f"Auto Rarity calibrating, skipping non-VIP {pokemon.pokemon_display}")
                    return
                else:
                    # No VIP lists - pause all during calibration
                    logger.trace(f"Auto Rarity calibrating (no VIP lists), skipping {pokemon.pokemon_display}")
                    return

            # Get area for rarity lookup (need to check geofence early for auto_rarity)
            area = "GLOBAL"
            if AppConfig.filter_with_koji:
                geofence_manager = await KojiGeofenceManager.get_instance()
                area = geofence_manager.is_point_in_geofence(pokemon.latitude, pokemon.longitude)
                if not area:
                    logger.debug(
                        f"Pokemon {pokemon.pokemon_display} at ({pokemon.latitude:.6f}, {pokemon.longitude:.6f}) "
                        f"outside geofences, skipping"
                    )
                    return

            # Get rarity rank (None = truly unknown, 1 = rarest, higher = more common)
            # High rank (beyond total tracked) = seen in census but rankings pending update
            rank = rarity_manager.get_rarity_rank(pokemon.pokemon_id, pokemon.form, area)
            if rank is None:
                # Truly unknown Pokemon (never seen in census) = treat as ultra rare
                # Tier 1000: auto_rarity (always lower priority than ivlist/celllist tier 0)
                priority = 1000  # Top priority within auto_rarity tier
                list_type = "auto_rarity(unknown)"
                logger.debug(
                    f"Auto Rarity: {pokemon.pokemon_display} unknown (not in census) in {area} - treating as ultra rare"
                )
            elif rank <= AppConfig.iv_threshold:
                # Known Pokemon within threshold - queue it
                # Tier 1000+: auto_rarity entries by rank (1000 + rank ensures lower priority than VIP tier 0)
                priority = 1000 + rank
                list_type = f"auto_rarity(rank={rank})"
                logger.debug(
                    f"Auto Rarity: {pokemon.pokemon_display} rank {rank} <= threshold {AppConfig.iv_threshold} in {area}"
                )
            else:
                logger.trace(
                    f"Pokemon {pokemon.pokemon_display} rank {rank} > threshold {AppConfig.iv_threshold}, skipping"
                )
                return
        else:
            logger.trace(f"Pokemon {pokemon.pokemon_display} not in ivlist, skipping")
            return

    # Check 3: Geofence check (optional based on config)
    # Note: For auto_rarity, geofence was already checked above. For ivlist/celllist, check now.
    if list_type in ("ivlist", "celllist"):
        area = "GLOBAL"  # Consistent with census tracking
        if AppConfig.filter_with_koji:
            geofence_manager = await KojiGeofenceManager.get_instance()
            area = geofence_manager.is_point_in_geofence(pokemon.latitude, pokemon.longitude)
            if not area:
                logger.debug(
                    f"Pokemon {pokemon.pokemon_display} at ({pokemon.latitude:.6f}, {pokemon.longitude:.6f}) "
                    f"outside geofences, skipping"
                )
                return
    # else: area was already set by auto_rarity logic above

    # All checks passed - add to queue
    # Simplify list_type for storage (remove rank info from auto_rarity)
    stored_list_type = "auto_rarity" if list_type.startswith("auto_rarity") else list_type

    queue = await IVQueueManager.get_instance()
    default_disappear_time = int(time_module.time()) + 600
    entry = QueueEntry(
        pokemon_id=pokemon.pokemon_id,
        form=pokemon.form,
        area=area,
        lat=pokemon.latitude,
        lon=pokemon.longitude,
        spawnpoint_id=pokemon.spawnpoint_id,
        priority=priority,
        encounter_id=pokemon.encounter_id,
        disappear_time=pokemon.disappear_time or default_disappear_time,
        seen_type=seen_type,
        s2_cell_id=s2_cell_id,
        list_type=stored_list_type,
    )

    added = await queue.add(entry)
    if added:
        logger.opt(colors=True).info(
            f"<green>[+]</green> Queued: Pokemon {pokemon.pokemon_display} in {area} "
            f"(priority {priority}, {list_type}, {seen_type})"
        )
        # Log queue status with next entries preview
        queue.log_queue_status()


async def filter_iv_pokemon(pokemon: PokemonData) -> None:
    """
    Filter for Pokemon WITH IV data.

    Checks:
    1. Pokemon HAS IV data - already ensured by caller
    2. Pokemon matches celllist, ivlist, OR was queued via auto_rarity
    3. Coordinates inside Koji geofences (if FILTER_WITH_KOJI is enabled)
    4. For nearby_cell: Match by s2_cell_id + pokemon_id
       For wild/nearby_stop: Match by encounter_id OR coordinates (70m proximity)

    If all pass: Log success, remove from queue
    """
    # Check 2: Match celllist or ivlist
    # When auto_rarity is enabled, we also need to match Pokemon that were queued via auto_rarity
    # (which are NOT in ivlist/celllist). We'll check the queue directly for those.
    in_vip_list = is_in_any_list(pokemon)
    if not in_vip_list and not AppConfig.auto_rarity_enabled:
        # Not in VIP list and auto_rarity disabled = skip
        return

    # Check 3: Geofence check (optional based on config)
    area = "GLOBAL"
    if AppConfig.filter_with_koji:
        geofence_manager = await KojiGeofenceManager.get_instance()
        area = geofence_manager.is_point_in_geofence(pokemon.latitude, pokemon.longitude)
        if not area:
            return

    # Check 4: Match against removal
    queue = await IVQueueManager.get_instance()
    removed: Optional[QueueEntry] = None

    removed = await queue.remove_by_match(
        encounter_id=pokemon.encounter_id,
        lat=pokemon.latitude,
        lon=pokemon.longitude,
    )
    if not removed:
        # For nearby_cell: match by s2_cell_id + pokemon_id
        s2_cell_id = get_s2_cell_id(pokemon.latitude, pokemon.longitude)
        removed = await queue.remove_by_cell_match(
            pokemon_id=pokemon.pokemon_id,
            form=pokemon.form,
            s2_cell_id=s2_cell_id,
        )

    if removed:
        # Check if this was scouted by us or received IV before we scouted
        if removed.was_scouted or removed.is_scouting:
            queue.record_match(pokemon.pokemon_display, removed.seen_type)
            logger.opt(colors=True).success(
                f"<green>[<]</green> Match found ({removed.list_type}): Pokemon {pokemon.pokemon_display} in {area} - "
                f"IV: {pokemon.individual_attack}/{pokemon.individual_defense}/{pokemon.individual_stamina} "
                f"({pokemon.iv_percent}%)"
            )
        else:
            queue.record_early_iv(pokemon.pokemon_display, removed.seen_type)
            logger.opt(colors=True).success(
                f"<magenta>[<]</magenta> Early IV ({removed.list_type}): Pokemon {pokemon.pokemon_display} in {area} - "
                f"IV: {pokemon.individual_attack}/{pokemon.individual_defense}/{pokemon.individual_stamina} "
                f"({pokemon.iv_percent}%)"
            )
        # Log updated queue status
        queue.log_queue_status()


async def process_census_webhook(raw_data: Dict[str, Any]) -> None:
    """
    Process census Pokemon data for rarity tracking.
    This receives ALL spawns (not just ivlist/celllist matches).
    Tracks ALL Pokemon (with or without IV) to build accurate rarity rankings.
    """
    import time

    pokemon = parse_pokemon_data(raw_data)
    if not pokemon:
        return

    # Track ALL Pokemon spawns for rarity (not just those with IVs)
    # This ensures rarity rankings are available when queue webhook arrives

    # Skip if despawned
    current_time = int(time.time())
    if pokemon.disappear_time and pokemon.disappear_time < current_time:
        return

    # Determine area
    area = "GLOBAL"
    if AppConfig.filter_with_koji:
        geofence_manager = await KojiGeofenceManager.get_instance()
        found_area = geofence_manager.is_point_in_geofence(pokemon.latitude, pokemon.longitude)
        if found_area:
            area = found_area
        else:
            # Outside geofences - skip for census too
            return

    # Add to rarity manager
    rarity_manager = await RarityManager.get_instance()
    await rarity_manager.add_spawn(
        pokemon_id=pokemon.pokemon_id,
        form=pokemon.form,
        area=area,
        despawn_time=pokemon.disappear_time or (current_time + 1800),
    )
