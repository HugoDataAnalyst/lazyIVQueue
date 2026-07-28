"""Pokemon webhook data parsing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from LazyIVQueue.utils.logger import logger


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
    costume: Optional[int] = None

    @property
    def has_iv(self) -> bool:
        """Check if Pokemon has IV data."""
        return self.individual_attack is not None

    @property
    def pokemon_display(self) -> str:
        """Human-readable pokemon identifier."""
        display = f"{self.pokemon_id}:{self.form}" if self.form is not None else str(self.pokemon_id)
        if self.costume:
            display += f":{self.costume}"
        return display

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
    - costume: int (optional, 0 = no costume)
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
            costume=raw.get("costume"),
        )
    except (ValueError, TypeError) as e:
        logger.warning(f"Error parsing Pokemon data: {e}")
        return None
