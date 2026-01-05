"""Dragonite Scout API endpoints."""
from typing import Any, List, Tuple

from LazyIVQueue.DragoniteAPI.utils.http_api import APIClient
from LazyIVQueue.utils.logger import logger


async def scout_coordinates(
    client: APIClient, coordinates: List[Tuple[float, float]]
) -> Any:
    """
    POST /scout - Submit scout coordinates to Dragonite.

    Args:
        client: APIClient instance
        coordinates: List of (lat, lon) tuples

    Returns:
        API response
    """
    # Format: [[lat, lon], [lat, lon], ...]
    # I need to test this actually..
    payload = [[lat, lon] for lat, lon in coordinates]

    logger.debug(f"[scout] Sending {len(coordinates)} coordinate(s) to Dragonite")
    response = await client.post("/scout", json=payload)

    return response


async def scout_single(client: APIClient, lat: float, lon: float) -> Any:
    """
    Scout a single coordinate.

    Args:
        client: APIClient instance
        lat: Latitude
        lon: Longitude

    Returns:
        API response
    """
    return await scout_coordinates(client, [(lat, lon)])


async def get_scout_queue(client: APIClient) -> Any:
    """
    GET /scout/queue - Get current scout queue length.

    Args:
        client: APIClient instance

    Returns:
        Queue status response
    """
    logger.debug("[scout] Getting queue status")
    response = await client.get("/scout/queue")
    return response


async def clear_scout_queue(client: APIClient) -> Any:
    """
    GET /scout/clear - Clear the scout queue.

    Args:
        client: APIClient instance

    Returns:
        API response
    """
    logger.debug("[scout] Clearing queue")
    response = await client.get("/scout/clear")
    return response
