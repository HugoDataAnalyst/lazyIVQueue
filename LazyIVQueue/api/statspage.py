"""Stats page - HTML dashboard for LazyIVQueue stats and rarity rankings."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Tuple

import aiohttp
import jinja2

from LazyIVQueue.utils.logger import logger
from LazyIVQueue.queue.iv_queue import IVQueueManager
from LazyIVQueue.rarity.manager import RarityManager
import LazyIVQueue.config as AppConfig

PACIFIC = ZoneInfo("America/Los_Angeles")

SPRITE_BASE = "https://raw.githubusercontent.com/whitewillem/PogoAssets/main/uicons-outline/pokemon"

# Cache: pokedex number -> name
_pokemon_names: Dict[int, str] = {}

POKEMON_KEY_RE = re.compile(r"^(\d+):(\d+)$")

# Jinja2 environment loaded once
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATE_DIR),
    autoescape=True,
)


async def _load_pokemon_names() -> None:
    """Fetch all Pokemon species names from PokeAPI (once, async)."""
    if _pokemon_names:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://pokeapi.co/api/v2/pokemon-species?limit=1025",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                for entry in data.get("results", []):
                    url = entry["url"].rstrip("/")
                    dex_num = int(url.split("/")[-1])
                    _pokemon_names[dex_num] = entry["name"].capitalize()
    except Exception:
        logger.warning("Failed to load Pokemon names from PokeAPI; numbers will be used")


def _sprite_url(dex: int, form: int) -> str:
    return f"{SPRITE_BASE}/{dex}_f{form}.png"


def _sprite_fallback(dex: int) -> str:
    return f"{SPRITE_BASE}/{dex}.png"


def _replace_pokemon_key(key: str, sprites: Dict[str, Tuple[str, str]]) -> str:
    """Turn '672:3037' into 'Skrelp (#672:3037)' and record sprite URL."""
    m = POKEMON_KEY_RE.match(key)
    if not m:
        return key
    dex = int(m.group(1))
    form = int(m.group(2))
    name = _pokemon_names.get(dex)
    if name:
        display = f"{name} (#{dex}:{form})" if form else f"{name} (#{dex})"
    else:
        display = key
    sprites[display] = (_sprite_url(dex, form), _sprite_fallback(dex))
    return display


TIMESTAMP_KEYS = {"last_ranking_time"}
SORT_BY_VALUE_KEYS = {"queued", "matches", "early_iv", "timeouts"}


def _transform(obj: Any, sprites: Dict[str, Tuple[str, str]], _key: str | None = None) -> Any:
    """Recursively replace Pokemon number keys/values with names."""
    if isinstance(obj, dict):
        result = {
            _replace_pokemon_key(k, sprites): _transform(v, sprites, _key=k)
            for k, v in obj.items()
        }
        if _key in SORT_BY_VALUE_KEYS:
            result = dict(
                sorted(
                    result.items(),
                    key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0,
                    reverse=True,
                )
            )
        return result
    if isinstance(obj, list):
        return [_transform(item, sprites) for item in obj]
    if isinstance(obj, str):
        m = POKEMON_KEY_RE.match(obj)
        if m:
            return _replace_pokemon_key(obj, sprites)
    if _key in TIMESTAMP_KEYS and isinstance(obj, (int, float)):
        dt = datetime.fromtimestamp(obj, tz=timezone.utc).astimezone(PACIFIC)
        return dt.strftime("%Y-%m-%d %I:%M:%S %p PST")
    return obj


def _transform_rarity(data: Dict[str, Any], sprites: Dict[str, Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Transform the rarity response into a flat list of ranking rows."""
    rows: List[Dict[str, Any]] = []
    areas = data.get("areas", {})
    for area_data in areas.values():
        for entry in area_data.get("rankings", []):
            pokemon_raw = entry.get("pokemon", "")
            m = POKEMON_KEY_RE.match(pokemon_raw)
            if m:
                display = _replace_pokemon_key(pokemon_raw, sprites)
            else:
                display = pokemon_raw
            rows.append({
                "rank": entry.get("global_rank", ""),
                "pokemon": display,
                "active_count": entry.get("active_count", ""),
                "would_queue": entry.get("would_queue", ""),
            })
    return rows


async def render_stats_page() -> str:
    """Gather stats + rarity data and render the HTML dashboard."""
    await _load_pokemon_names()

    sprites: Dict[str, Tuple[str, str]] = {}
    stats = None
    rarity_rows: List[Dict[str, Any]] = []
    rarity_meta: Dict[str, Any] = {}
    stats_error = None
    rarity_error = None

    # --- Fetch stats from internal queue manager ---
    try:
        queue = await IVQueueManager.get_instance()
        stats_data: Dict[str, Any] = {"queue": await queue.get_stats()}

        if AppConfig.auto_rarity_enabled:
            rarity_manager = await RarityManager.get_instance()
            stats_data["rarity"] = await rarity_manager.get_stats()

        stats = _transform(stats_data, sprites)
    except Exception as e:
        stats_error = str(e)

    # --- Extract session totals into combined table ---
    session_totals: List[Dict[str, Any]] = []
    pokemon_tables: Dict[str, List[Dict[str, Any]]] = {}
    iv_per_hour: Dict[str, Any] = {}
    if isinstance(stats, dict):
        iv_per_hour = stats.get("queue", {}).pop("iv_per_hour", {})
        session = stats.get("queue", {}).get("session", {})
        # Pull out the four total_* dicts and combine into rows
        total_keys = ["total_queued", "total_matches", "total_early_iv", "total_timeouts"]
        totals_data = {k: session.pop(k, None) for k in total_keys}
        # Build one row per type (total, wild, nearby_stop, nearby_cell)
        type_names = ["total", "wild", "nearby_stop", "nearby_cell"]
        for tn in type_names:
            row = {"type": tn}
            has_data = False
            for tk in total_keys:
                val = totals_data[tk]
                col = tk.replace("total_", "")  # queued, matches, early_iv, timeouts
                if isinstance(val, dict):
                    row[col] = val.get(tn, 0)
                    if row[col]:
                        has_data = True
                else:
                    row[col] = 0
            if has_data or tn == "total":
                session_totals.append(row)

        by_pokemon = session.pop("by_pokemon", None)
        if isinstance(by_pokemon, dict):
            for type_name, type_data in by_pokemon.items():
                if not isinstance(type_data, dict):
                    continue
                queued = type_data.get("queued", {})
                matches = type_data.get("matches", {})
                early_iv = type_data.get("early_iv", {})
                timeouts = type_data.get("timeouts", {})
                all_pokemon = dict.fromkeys(
                    list(queued) + list(matches) + list(early_iv) + list(timeouts)
                )
                rows = []
                for pk in all_pokemon:
                    rows.append({
                        "pokemon": pk,
                        "queued": queued.get(pk, 0),
                        "matches": matches.get(pk, 0),
                        "early_iv": early_iv.get(pk, 0),
                        "timeouts": timeouts.get(pk, 0),
                    })
                rows.sort(key=lambda r: r["queued"], reverse=True)
                if rows:
                    pokemon_tables[type_name] = rows

            # Build a "total" tab that sums across all types
            if pokemon_tables:
                combined: Dict[str, Dict[str, int]] = {}
                for _rows in pokemon_tables.values():
                    for r in _rows:
                        pk = r["pokemon"]
                        if pk not in combined:
                            combined[pk] = {"queued": 0, "matches": 0, "early_iv": 0, "timeouts": 0}
                        combined[pk]["queued"] += r["queued"]
                        combined[pk]["matches"] += r["matches"]
                        combined[pk]["early_iv"] += r["early_iv"]
                        combined[pk]["timeouts"] += r["timeouts"]
                total_rows = [
                    {"pokemon": pk, **vals} for pk, vals in combined.items()
                ]
                total_rows.sort(key=lambda r: r["queued"], reverse=True)
                pokemon_tables = {"total": total_rows, **pokemon_tables}

    # --- Fetch rarity rankings ---
    if AppConfig.auto_rarity_enabled:
        try:
            rarity_manager = await RarityManager.get_instance()
            rarity_data = await rarity_manager.get_rankings()
            rarity_rows = _transform_rarity(rarity_data, sprites)
            rarity_meta = {
                "status": rarity_data.get("status", ""),
                "threshold": rarity_data.get("threshold", ""),
                "total_tracked": rarity_data.get("total_tracked_globally", ""),
                "would_queue_count": rarity_data.get("would_queue_globally", ""),
            }
        except Exception as e:
            rarity_error = str(e)

    template = _jinja_env.get_template("stats.html")
    return template.render(
        stats=stats if isinstance(stats, dict) else None,
        raw=stats if isinstance(stats, str) else None,
        error=stats_error,
        sprites=sprites,
        session_totals=session_totals,
        iv_per_hour=iv_per_hour,
        pokemon_tables=pokemon_tables,
        rarity_rows=rarity_rows,
        rarity_meta=rarity_meta,
        rarity_error=rarity_error,
        auto_rarity_enabled=AppConfig.auto_rarity_enabled,
    )
