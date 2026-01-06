"""Webhook Filter - Dual filter logic for IV and non-IV Pokemon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from LazyIVQueue.utils.logger import logger
from LazyIVQueue.utils.koji_geofences import KojiGeofenceManager
from LazyIVQueue.utils.geo_utils import is_within_distance, COORDINATE_MATCH_THRESHOLD_METERS
from LazyIVQueue.queue.iv_queue import IVQueueManager, QueueEntry
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
    2. Pokemon matches ivlist (by pokemon_id:form or pokemon_id)
    3. Coordinates inside Koji geofences

    If all pass: Add to IV queue and trigger scout
    """
    # Check 2: Match ivlist
    matches, priority = is_in_ivlist(pokemon)
    if not matches or priority is None:
        logger.debug(f"Pokemon {pokemon.pokemon_display} not in ivlist, skipping")
        return

    # Check 3: Geofence check
    geofence_manager = await KojiGeofenceManager.get_instance()
    area = geofence_manager.is_point_in_geofence(pokemon.latitude, pokemon.longitude)
    if not area:
        logger.debug(
            f"Pokemon {pokemon.pokemon_display} at ({pokemon.latitude:.6f}, {pokemon.longitude:.6f}) "
            f"outside geofences, skipping"
        )
        return

    # All checks passed - add to queue
    queue = await IVQueueManager.get_instance()
    entry = QueueEntry(
        pokemon_id=pokemon.pokemon_id,
        form=pokemon.form,
        area=area,
        lat=pokemon.latitude,
        lon=pokemon.longitude,
        spawnpoint_id=pokemon.spawnpoint_id,
        priority=priority,
        encounter_id=pokemon.encounter_id,
        disappear_time=pokemon.disappear_time,
    )

    added = await queue.add(entry)
    if added:
        logger.info(
            f"[+] Queued: Pokemon {pokemon.pokemon_display} in {area} "
            f"(priority {priority})"
        )
        # Log queue status with next entries preview
        queue.log_queue_status()


async def filter_iv_pokemon(pokemon: PokemonData) -> None:
    """
    Filter for Pokemon WITH IV data.

    Checks:
    1. Pokemon HAS IV data - already ensured by caller
    2. Pokemon matches ivlist
    3. Coordinates inside Koji geofences
    4. encounter_id OR coordinates match entry in IV queue (70m proximity)

    If all pass: Log success, remove from queue
    """
    # Check 2: Match ivlist (still need this to avoid processing unwanted Pokemon)
    matches, _ = is_in_ivlist(pokemon)
    if not matches:
        return

    # Check 3: Geofence check
    geofence_manager = await KojiGeofenceManager.get_instance()
    area = geofence_manager.is_point_in_geofence(pokemon.latitude, pokemon.longitude)
    if not area:
        return

    # Check 4: Match against queue (encounter_id first, then proximity)
    queue = await IVQueueManager.get_instance()
    removed = await queue.remove_by_match(
        encounter_id=pokemon.encounter_id,
        lat=pokemon.latitude,
        lon=pokemon.longitude,
    )

    if removed:
        logger.info(
            f"[-] Scouted: Pokemon {pokemon.pokemon_display} in {area} - "
            f"IV: {pokemon.individual_attack}/{pokemon.individual_defense}/{pokemon.individual_stamina} "
            f"({pokemon.iv_percent}%)"
        )
        # Log updated queue status
        queue.log_queue_status()
