# LazyIVQueue

Pokemon IV scouting queue that receives webhooks from Golbat, filters by priority list and Koji geofences, and dispatches scout requests to Dragonite.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
cp LazyIVQueue/config/example.config.json LazyIVQueue/config/config.json
```

## Configuration

### .env
- `LAZYIVQUEUE_HOST` / `LAZYIVQUEUE_PORT` - Server bind address
- `DRAGONITE_API_BASE_URL` - Dragonite Scout API endpoint (e.g., `http://127.0.0.1:7272`)
- `KOJI_URL` - Koji base URL for geofences
- `ALLOWED_IPS` - Comma-separated IPs allowed to POST webhooks
- `HEADERS` - Optional header auth (format: `HeaderName: Value`)

### config.json
- `ivlist` - Priority list of Pokemon to scout (first = highest priority)
  - `"pokemon_id"` - Match any form (e.g., `"1"` matches Bulbasaur any form)
  - `"pokemon_id:form"` - Match specific form only (e.g., `"3:0"` matches Venusaur form 0)
  - Example: `["Pokemon A", "Pokemon B:0", "Pokemon C"]` - A is top priority, then B form 0, then C
- `scout.concurrency` - Max concurrent scout requests
- `scout.timeout_iv` - Seconds to wait for IV data before removing from queue (default: 180)

## Run

```bash
python -m LazyIVQueue.lazyivqueue
```

## Endpoints

- `POST /webhook` - Receives Pokemon webhooks from Golbat
- `GET /health` - Health check
- `GET /stats` - Queue and scout statistics

## Log Prefixes

- `[+]` - Pokemon added to queue
- `[>]` - Scout request sent to Dragonite
- `[<]` - IV match found (scout successful)
- `[x]` - Scout timeout (no IV received)
- `[!]` - Scout request failed
