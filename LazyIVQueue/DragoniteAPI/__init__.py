"""Dragonite API package."""
from LazyIVQueue.DragoniteAPI.utils.http_api import APIClient
import LazyIVQueue.config as AppConfig
from LazyIVQueue.utils.logger import logger


def get_dragonite_client() -> APIClient:
    """
    Builds an APIClient using DRAGONITE_* envs.
    - DRAGONITE_API_BASE_URL (required)
    - DRAGONITE_API_USERNAME (optional)
    - DRAGONITE_API_PASSWORD (optional)
    - DRAGONITE_API_KEY (optional secret)
    - DRAGONITE_BEARER_KEY (optional bearer)
    Adjust as needed.
    """
    base = AppConfig.DRAGONITE_API_BASE_URL or ""
    username = AppConfig.DRAGONITE_API_USERNAME or None
    password = AppConfig.DRAGONITE_API_PASSWORD or None
    bearer = AppConfig.DRAGONITE_BEARER_KEY or None
    secret = AppConfig.DRAGONITE_API_KEY or None
    return APIClient(base, username=username, password=password, bearer=bearer, secret=secret)


__all__ = ["get_dragonite_client", "APIClient"]
