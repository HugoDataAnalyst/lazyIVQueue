from typing import Any, Dict, List
from LazyIVQueue.utils.logger import logger
from LazyIVQueue.DragoniteAPI.utils.http_api import APIClient

async def get_status(client: APIClient) -> List[Dict[str, Any]]:
    """
    GET /status
    Returns list of areas with worker_managers/workers info.
    """
    return await client.get("/status")

async def reload_global(client: APIClient) -> Dict[str, Any]:
    return await client.get("/reload")
