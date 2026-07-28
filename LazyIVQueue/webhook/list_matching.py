"""Priority list matching - ivlist/celllist/denylist lookups.

Priority Tiers (lower = higher priority):
  - Tier 0 (0-999): VIP lists (celllist + ivlist) - position in list determines sub-priority
  - Tier 1000+: auto_rarity entries - 1000 for unknown, 1000+rank for ranked Pokemon

This ensures ivlist/celllist ALWAYS take priority over auto_rarity.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from LazyIVQueue.webhook.pokemon_data import PokemonData
import LazyIVQueue.config as AppConfig


def _match_priority(
    pokemon_id: int,
    form: Optional[int],
    costume: Optional[int],
    parsed: Dict[AppConfig.IVListKey, int],
) -> Optional[int]:
    """
    Look up priority for a (pokemon_id, form, costume) combination against a
    parsed ivlist/celllist/denylist dict.

    Checks all wildcard-normalized candidate keys (exact form/costume, exact
    form only, exact costume only, any/any) and returns the MINIMUM priority
    among the ones that hit - since priority is list index, this means the
    earliest-configured matching entry wins, regardless of specificity.
    """
    pid = str(pokemon_id)
    form_s = str(form) if form is not None else None
    # Costume 0 and missing/None both mean "no costume" - treat them the same,
    # since a webhook payload may report either depending on message type.
    costume_s = str(costume) if costume else None

    candidates = {
        (pid, form_s, costume_s),
        (pid, form_s, None),
        (pid, None, costume_s),
        (pid, None, None),
    }
    priorities = [parsed[key] for key in candidates if key in parsed]
    return min(priorities) if priorities else None


def is_in_ivlist(pokemon: PokemonData) -> Tuple[bool, Optional[int]]:
    """
    Check if Pokemon matches ivlist.

    Returns:
        (matches: bool, priority: Optional[int])
    """
    priority = _match_priority(pokemon.pokemon_id, pokemon.form, pokemon.costume, AppConfig.ivlist_parsed)
    return priority is not None, priority


def is_in_celllist(pokemon: PokemonData) -> Tuple[bool, Optional[int]]:
    """
    Check if Pokemon matches celllist (for nearby_cell scouting).

    Returns:
        (matches: bool, priority: Optional[int])
        Priority uses tier 0 (highest) for celllist entries.
    """
    priority = _match_priority(pokemon.pokemon_id, pokemon.form, pokemon.costume, AppConfig.celllist_parsed)
    return priority is not None, priority


def is_in_any_list(pokemon: PokemonData) -> bool:
    """Check if Pokemon matches either ivlist or celllist."""
    matches_iv, _ = is_in_ivlist(pokemon)
    matches_cell, _ = is_in_celllist(pokemon)
    return matches_iv or matches_cell


def is_in_denylist(pokemon: PokemonData) -> bool:
    """Check if Pokemon matches the denylist (should not be scouted)."""
    priority = _match_priority(pokemon.pokemon_id, pokemon.form, pokemon.costume, AppConfig.denylist_parsed)
    return priority is not None
