import os
import sys
import json
import dotenv

from LazyIVQueue.utils.logger import logger
from typing import List, Optional, Dict

# load config.json

CONFIG_PATH = os.path.join(os.getcwd(), "config", "config.json")

def load_config() -> Dict[str, any]:
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
        logger.info(f"✅ Loaded config from {CONFIG_PATH}")
        return config
    except FileNotFoundError:
        logger.error(f"❌ Config file not found at {CONFIG_PATH}. Using default values.")
        return {}

config = load_config()

# Read environment variables from .env file
env_file = os.path.join(os.getcwd(), ".env")
dotenv.load_dotenv(env_file, override=True)

def get_env_var(name: str, default = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None or value == '':
        logger.warning(f"⚠️ Missing environment variable: {name}. Using default: {default}")
        return default
    return value


def get_env_list(env_var_name: str, default = None) -> List[str]:
    if default is None:
        default = []
    value = os.getenv(env_var_name, '')
    if not value:
        logger.warning(f"⚠️ Missing environment variable: {env_var_name}. Using default: {default}")
        return default
    return [item.strip() for item in value.split(',') if item.strip()]


def get_env_int(name: str, default = None) -> Optional[int]:
    value = os.getenv(name)
    if not value:
        logger.warning(f"⚠️ Missing environment variable: {name}. Using default: {default}")
        return default
    try:
        return int(value)
    except ValueError:
        logger.error(f"❌ Invalid value for environment variable {name}: {value}. Using default: {default}")
        return default

# Koji
koji_bearer_token = get_env_var("KOJI_TOKEN")
koji_ip = get_env_var("KOJI_IP", "127.0.0.1")
koji_port = get_env_int("KOJI_PORT", 8080)
koji_project_name = get_env_var("KOJI_PROJECT_NAME")
koji_geofence_api_url = f"http://{koji_ip}:{koji_port}/api/v1/geofence/feature-collection/{koji_project_name}"
koji_url_base = get_env_var("KOJI_URL")
koji_url = f"{koji_url_base}/api/v1/geofence/feature-collection/{koji_project_name}" if koji_url_base else None

# Extract geofence settings
geofence_expire_cache_seconds = config.get("geofences", {}).get("expire_cache_seconds", 3600)
geofence_refresh_cache_seconds = config.get("geofences", {}).get("refresh_cache_seconds", 3500)


# Log Level
log_level = get_env_var("LOG_LEVEL", "INFO").upper()
log_file = get_env_var("LOG_FILE", "FALSE").upper() == "TRUE"

golbat_webhook = get_env_list("GOLBAT_WEBHOOK", None)
golbat_webhook_url = get_env_list("GOLBAT_WEBHOOK_URL", None)

# IVQueue - Priority list of Pokemon to scout
# Format: ["1", "3:0", "10:0"] where "1" = pokemon_id 1 any form, "3:0" = pokemon_id 3 form 0
# Lower index = higher priority (first item is highest priority)
ivlist: List[str] = config.get("ivlist", [])

def parse_ivlist(raw_list: List[str]) -> Dict[str, int]:
    """
    Parses ivlist into {pokemon_key: priority} mapping.
    Returns dict where key is "pokemon_id" or "pokemon_id:form"
    and value is priority (0 = highest).
    """
    result = {}
    for idx, entry in enumerate(raw_list):
        result[entry.strip()] = idx
    return result

ivlist_parsed: Dict[str, int] = parse_ivlist(ivlist)

def get_pokemon_priority(pokemon_id: int, form: Optional[int]) -> Optional[int]:
    """
    Get priority for a pokemon based on ivlist.
    Returns None if not in ivlist.
    """
    # First check exact match (pokemon_id:form)
    if form is not None:
        key = f"{pokemon_id}:{form}"
        if key in ivlist_parsed:
            return ivlist_parsed[key]

    # Then check any-form match (just pokemon_id)
    key = str(pokemon_id)
    if key in ivlist_parsed:
        return ivlist_parsed[key]

    return None

def is_pokemon_in_ivlist(pokemon_id: int, form: Optional[int]) -> bool:
    """Check if pokemon matches ivlist."""
    return get_pokemon_priority(pokemon_id, form) is not None

# Scout concurrency
concurrency_scout: int = config.get("scout", {}).get("concurrency", 5)

# Dragonite
DRAGONITE_API_BASE_URL = get_env_var("DRAGONITE_API_BASE_URL")
DRAGONITE_API_USERNAME = get_env_var("DRAGONITE_API_USERNAME", None)
DRAGONITE_API_PASSWORD = get_env_var("DRAGONITE_API_PASSWORD", None)
DRAGONITE_API_KEY = get_env_var("DRAGONITE_API_KEY", None)
DRAGONITE_BEARER_KEY = get_env_var("DRAGONITE_BEARER_KEY", None)

# Secuirty
allowed_ips = get_env_list("ALLOWED_IPS", None)
headers = get_env_var("HEADERS", None)
