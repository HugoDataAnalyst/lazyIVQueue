"""Dragonite Scout API v2 endpoints."""
from typing import Any, List, Tuple, Optional

from LazyIVQueue.DragoniteAPI.utils.http_api import APIClient
from LazyIVQueue.utils.logger import logger


# Default scout options for pokemon scanning
DEFAULT_SCOUT_OPTIONS = {
    "pokemon": True,
    "pokemon_encounter_radius": 70,  # meters
    "gmf": False,
    "routes": False,
    "showcases": False,
}


async def scout_v2(
    client: APIClient,
    coordinates: List[Tuple[float, float]],
    username: str = "LazyIVQueue",
    options: Optional[dict] = None,
) -> Any:
    """
    POST /v2/scout - Submit scout coordinates to Dragonite v2 API.

    Args:
        client: APIClient instance
        coordinates: List of (lat, lon) tuples
        username: Username to identify the scout request source
        options: Scout options (pokemon, gmf, routes, etc.)

    Returns:
        API response
    """
    # Use default options if not provided
    scout_options = options or DEFAULT_SCOUT_OPTIONS

    # Format coordinates with 5 decimal precision
    locations = [[round(lat, 5), round(lon, 5)] for lat, lon in coordinates]

    payload = {
        "username": username,
        "locations": locations,
        "options": scout_options,
    }

    logger.debug(f"[scout] POST /scout/v2 - {len(coordinates)} location(s)")
    response = await client.post("/scout/v2", json=payload)

    return response


async def scout_single(
    client: APIClient,
    lat: float,
    lon: float,
    username: str = "LazyIVQueue",
) -> Any:
    """
    Scout a single coordinate using v2 API.

    Args:
        client: APIClient instance
        lat: Latitude
        lon: Longitude
        username: Username to identify the scout request source

    Returns:
        API response
    """
    return await scout_v2(client, [(lat, lon)], username=username)


async def get_scout_queue(client: APIClient) -> Any:
    """
    GET /v2/scout/queue - Get current scout queue status.

    Args:
        client: APIClient instance

    Returns:
        Queue status response
    """
    logger.debug("[scout] GET /scout/v2/queue")
    response = await client.get("/scout/v2/queue")
    return response


async def clear_scout_queue(client: APIClient) -> Any:
    """
    GET /v2/scout/clear - Clear the scout queue.

    Args:
        client: APIClient instance

    Returns:
        API response
    """
    logger.debug("[scout] GET /scout/v2/clear")
    response = await client.get("/scout/v2/clear")
    return response
