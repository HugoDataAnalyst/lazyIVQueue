# LazyIVQueue

Pokemon IV scouting queue that receives webhooks from Golbat, filters by priority list and Koji geofences, and dispatches scout requests to Dragonite.

## Setup

Requires Python 3.12+

```bash
# Recommended:
# Create virtual environment
py -3.12 -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (Linux/Mac)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy config files
cp .env.example .env
cp LazyIVQueue/config/example.config.json LazyIVQueue/config/config.json
```

## Configuration

### .env

**Logging**
- `LOG_LEVEL` - Log level (default: `INFO`)
- `LOG_FILE` - Log to file (default: `FALSE`)

**Server**
- `LAZYIVQUEUE_HOST` / `LAZYIVQUEUE_PORT` - Server bind address (default: `0.0.0.0:7070`)

**Dragonite Scout API**
- `DRAGONITE_API_BASE_URL` - Dragonite Scout API endpoint (e.g., `http://127.0.0.1:7272`)
- `DRAGONITE_API_USERNAME` / `DRAGONITE_API_PASSWORD` - Basic auth credentials (optional)
- `DRAGONITE_API_KEY` - API key auth (optional)
- `DRAGONITE_BEARER_KEY` - Bearer token auth (optional)

**Auto Rarity**
- `AUTO_RARITY` - Enable dynamic rarity-based queueing (default: `FALSE`). See Auto Rarity section below

**Koji Geofences**
- `FILTER_WITH_KOJI` - Enable geofence filtering (default: `TRUE`). Set to `FALSE` to skip geofence checks
- `KOJI_URL` - Full Koji base URL (e.g., `http://koji.example.com:8080`). Alternative to KOJI_IP/KOJI_PORT
- `KOJI_IP` / `KOJI_PORT` - Koji host and port (default: `127.0.0.1:8080`)
- `KOJI_TOKEN` - Koji bearer token for authentication
- `KOJI_PROJECT_NAME` - Koji project name containing the geofences to use

**Security**
- `ALLOWED_IPS` - Comma-separated IPs allowed to POST webhooks (e.g., `127.0.0.1,192.168.1.100`)
- `HEADERS` - Header auth (format: `HeaderName: Value`). Example Golbat config:
```toml
[[webhooks]]
url = "http://localhost:7070/webhook"
types = ["pokemon"]
headers = ["HeaderName: Value"]
```

### config.json
- `ivlist` - Priority list of Pokemon to scout for `wild`/`nearby_stop` seen_types (first = highest priority)
  - `"pokemon_id"` - Match any form (e.g., `"1"` matches Bulbasaur any form)
  - `"pokemon_id:form"` - Match specific form only (e.g., `"3:0"` matches Venusaur form 0)
  - Example: `["Pokemon A", "Pokemon B:0", "Pokemon C"]` - A is top priority, then B form 0, then C
- `celllist` - Priority list for `nearby_cell` seen_type (same format as ivlist)
  - Celllist entries are always processed before ivlist entries, so only insert really important ones here
  - Uses 9x9 pattern (9 coordinates) to cover S2 level-15 cell
- `scout.concurrency` - Max concurrent scout requests - Should match the number of scouts you have set in Dragonite
- `scout.timeout_iv` - Seconds to wait for IV data before removing from queue - Liberating the scout to work (default: 180)
- `geofences.expire_cache_seconds` - How long to cache geofences before expiring (default: 1800)
- `geofences.refresh_cache_seconds` - How often to refresh geofences from Koji (default: 1800)
- `auto_rarity` - Dynamic rarity settings (when `AUTO_RARITY=TRUE`):
  - `calibration_minutes` - Minutes to collect spawn data before rankings are used (default: 5)
  - `iv_threshold` - Queue Pokemon with rarity rank below this (default: 50, lower = rarer)
  - `cell_threshold` - Cell scout threshold (default: 20)
  - `ranking_interval_seconds` - How often to recalculate rankings (default: 120)
  - `cleanup_interval_seconds` - How often to remove despawned Pokemon from tracking (default: 60)

## Auto Rarity

When `AUTO_RARITY=TRUE`, LazyIVQueue dynamically tracks Pokemon spawn rarity and queues rare Pokemon automatically.

### How it works

1. **Census Webhook**: Configure Golbat to send ALL Pokemon spawns to `/webhook/census`
2. **Rarity Tracking**: The system tracks active spawns per area (or globally if Koji disabled)
3. **Calibration**: During the calibration period, only ivlist/celllist Pokemon are queued
4. **Dynamic Queueing**: After calibration, Pokemon with rarity rank below the threshold are queued

### Priority System (lower = higher priority)

- **Tier 0 (0-999)**: VIP lists (celllist + ivlist) - position in list determines sub-priority
- **Tier 1000+**: auto_rarity entries - 1000 for unknown, 1000+rank for ranked Pokemon

This ensures ivlist/celllist entries ALWAYS take priority over auto_rarity entries.

### Golbat Configuration

You need TWO webhook configurations in Golbat:

```toml
# Existing webhook for ivlist/celllist filtering
[[webhooks]]
url = "http://localhost:7070/webhook"
types = ["pokemon"]
headers = ["HeaderName: Value"]

# Census webhook for rarity tracking (ALL spawns)
[[webhooks]]
url = "http://localhost:7070/webhook/census"
types = ["pokemon"]
headers = ["HeaderName: Value"]
```

## Run

### Local
```bash
python -m LazyIVQueue.lazyivqueue
```

### Docker
```bash
cp example.docker-compose.yml docker-compose.yml
docker-compose up -d --build
```

## Endpoints

- `POST /webhook` - Receives Pokemon webhooks from Golbat (ivlist/celllist filtering)
- `POST /webhook/census` - Receives ALL Pokemon spawns for rarity tracking
- `GET /health` - Health check
- `GET /stats` - Queue, scout, and rarity statistics
- `GET /queue` - Queue preview (next N entries, use `?count=N`)
- `GET /rarity` - Auto Rarity rankings per area (use `?area=AreaName&limit=100`)
- `GET /config` - Current configuration summary
- `POST /reload` - Hot-reload config.json values without restarting

### Hot Reload

The `/reload` endpoint allows you to update config.json values without restarting the service.

**Reloadable settings:**
- `ivlist`, `celllist` - Priority lists
- `auto_rarity` settings - thresholds, intervals
- `scout.concurrency`, `scout.timeout_iv` - Scout settings
- `geofences` cache settings

**Requires restart:**
- Server host/port (`.env`)
- Dragonite API settings (`.env`)
- Koji credentials (`.env`)
- `AUTO_RARITY`, `FILTER_WITH_KOJI` (`.env`)
- `LOG_LEVEL`, `LOG_FILE` (`.env`)

Example:
```bash
curl -X POST http://localhost:7070/reload
```

## Log Prefixes

### Queue Operations
- `[+]` - Pokemon added to queue
- `[>]` - Scout request sent to Dragonite
- `[<]` - IV match found (scout successful) or Early IV (received before scout)
- `[x]` - Scout timeout (no IV received within timeout_iv seconds)
- `[!]` - Scout request failed

### Census/Rarity (when AUTO_RARITY=TRUE)
- `[*]` - Census status during calibration / New area discovered
- `[~]` - Census status after calibration / Census cleanup
