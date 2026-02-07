import os
import json
from webbrowser import get
import dotenv

from LazyIVQueue.utils.logger import logger
from typing import List, Optional, Dict

# load config.json

CONFIG_PATH = os.path.join(os.getcwd(), "LazyIVQueue", "config", "config.json")

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

# Auto Rarity settings
auto_rarity_enabled: bool = get_env_var("AUTO_RARITY", "FALSE").upper() == "TRUE"
auto_rarity_config = config.get("auto_rarity", {})
calibration_minutes: int = auto_rarity_config.get("calibration_minutes", 5)
iv_threshold: int = auto_rarity_config.get("iv_threshold", 50)
cell_threshold: int = auto_rarity_config.get("cell_threshold", 20)
ranking_interval_seconds: int = auto_rarity_config.get("ranking_interval_seconds", 300)
cleanup_interval_seconds: int = auto_rarity_config.get("cleanup_interval_seconds", 60)

# Koji
koji_bearer_token = get_env_var("KOJI_TOKEN")
koji_ip = get_env_var("KOJI_IP", "127.0.0.1")
koji_port = get_env_int("KOJI_PORT", 8080)
koji_project_name = get_env_var("KOJI_PROJECT_NAME")
koji_geofence_api_url = f"http://{koji_ip}:{koji_port}/api/v1/geofence/feature-collection/{koji_project_name}"
koji_url_base = get_env_var("KOJI_URL")
koji_url = f"{koji_url_base}/api/v1/geofence/feature-collection/{koji_project_name}" if koji_url_base else None

# Filter with Koji geofences (if False, only ivlist filtering is applied)
filter_with_koji: bool = get_env_var("FILTER_WITH_KOJI", "TRUE").upper() == "TRUE"

# Extract geofence settings
geofence_expire_cache_seconds = config.get("geofences", {}).get("expire_cache_seconds", 3600)
geofence_refresh_cache_seconds = config.get("geofences", {}).get("refresh_cache_seconds", 3500)


# Log Level
log_level = get_env_var("LOG_LEVEL", "INFO").upper()
log_file = get_env_var("LOG_FILE", "FALSE").upper() == "TRUE"

# IVQueue - Priority list of Pokemon to scout
# Format: ["1", "3:0", "10:0"] where "1" = pokemon_id 1 any form, "3:0" = pokemon_id 3 form 0
# Lower index = higher priority (first item is highest priority)
ivlist: List[str] = config.get("ivlist", [])
celllist: List[str] = config.get("celllist", [])
timeout_iv: int = config.get("scout", {}).get("timeout_iv", 180)

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
celllist_parsed: Dict[str, int] = parse_ivlist(celllist)


def reload_config() -> Dict[str, any]:
    """
    Hot-reload config.json values without restarting the application.

    Reloadable values:
    - ivlist, celllist (priority lists)
    - auto_rarity settings (thresholds, intervals)
    - scout concurrency and timeout
    - geofence cache settings

    NOT reloadable (require restart):
    - Server host/port
    - Dragonite API settings
    - Koji credentials
    - LOG_LEVEL, LOG_FILE
    - AUTO_RARITY enable/disable
    - FILTER_WITH_KOJI

    Returns:
        Dict with old and new values for changed settings
    """
    global config, ivlist, celllist, ivlist_parsed, celllist_parsed
    global auto_rarity_config, calibration_minutes, iv_threshold, cell_threshold
    global ranking_interval_seconds, cleanup_interval_seconds
    global concurrency_scout, timeout_iv
    global geofence_expire_cache_seconds, geofence_refresh_cache_seconds

    changes = {}

    # Reload config.json
    new_config = load_config()

    # Track ivlist changes
    new_ivlist = new_config.get("ivlist", [])
    if new_ivlist != ivlist:
        changes["ivlist"] = {"old": ivlist, "new": new_ivlist}
        ivlist = new_ivlist
        ivlist_parsed = parse_ivlist(ivlist)

    # Track celllist changes
    new_celllist = new_config.get("celllist", [])
    if new_celllist != celllist:
        changes["celllist"] = {"old": celllist, "new": new_celllist}
        celllist = new_celllist
        celllist_parsed = parse_ivlist(celllist)

    # Track auto_rarity changes
    new_auto_rarity = new_config.get("auto_rarity", {})

    new_calibration = new_auto_rarity.get("calibration_minutes", 5)
    if new_calibration != calibration_minutes:
        changes["calibration_minutes"] = {"old": calibration_minutes, "new": new_calibration}
        calibration_minutes = new_calibration

    new_iv_threshold = new_auto_rarity.get("iv_threshold", 50)
    if new_iv_threshold != iv_threshold:
        changes["iv_threshold"] = {"old": iv_threshold, "new": new_iv_threshold}
        iv_threshold = new_iv_threshold

    new_cell_threshold = new_auto_rarity.get("cell_threshold", 20)
    if new_cell_threshold != cell_threshold:
        changes["cell_threshold"] = {"old": cell_threshold, "new": new_cell_threshold}
        cell_threshold = new_cell_threshold

    new_ranking_interval = new_auto_rarity.get("ranking_interval_seconds", 300)
    if new_ranking_interval != ranking_interval_seconds:
        changes["ranking_interval_seconds"] = {"old": ranking_interval_seconds, "new": new_ranking_interval}
        ranking_interval_seconds = new_ranking_interval

    new_cleanup_interval = new_auto_rarity.get("cleanup_interval_seconds", 60)
    if new_cleanup_interval != cleanup_interval_seconds:
        changes["cleanup_interval_seconds"] = {"old": cleanup_interval_seconds, "new": new_cleanup_interval}
        cleanup_interval_seconds = new_cleanup_interval

    # Track scout settings changes
    new_concurrency = new_config.get("scout", {}).get("concurrency", 5)
    if new_concurrency != concurrency_scout:
        changes["concurrency_scout"] = {"old": concurrency_scout, "new": new_concurrency}
        concurrency_scout = new_concurrency

    new_timeout = new_config.get("scout", {}).get("timeout_iv", 180)
    if new_timeout != timeout_iv:
        changes["timeout_iv"] = {"old": timeout_iv, "new": new_timeout}
        timeout_iv = new_timeout

    # Track geofence cache settings
    new_expire = new_config.get("geofences", {}).get("expire_cache_seconds", 3600)
    if new_expire != geofence_expire_cache_seconds:
        changes["geofence_expire_cache_seconds"] = {"old": geofence_expire_cache_seconds, "new": new_expire}
        geofence_expire_cache_seconds = new_expire

    new_refresh = new_config.get("geofences", {}).get("refresh_cache_seconds", 3500)
    if new_refresh != geofence_refresh_cache_seconds:
        changes["geofence_refresh_cache_seconds"] = {"old": geofence_refresh_cache_seconds, "new": new_refresh}
        geofence_refresh_cache_seconds = new_refresh

    # Update the global config dict
    config = new_config
    auto_rarity_config = new_auto_rarity

    if changes:
        logger.info(f"Config reloaded with {len(changes)} change(s): {list(changes.keys())}")
    else:
        logger.info("Config reloaded - no changes detected")

    return changes

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

# LazyIVQueue Admin API
lazyivqueue_host = get_env_var("LAZYIVQUEUE_HOST", "0.0.0.0")
lazyivqueue_port = get_env_int("LAZYIVQUEUE_PORT", 7070)

# Security
allowed_ips = get_env_list("ALLOWED_IPS", None)
headers = get_env_var("HEADERS", None)
