"""LazyIVQueue API Server - Single HTTP server for webhooks, stats, health, and management."""
from __future__ import annotations

from typing import Optional, Set, List
from aiohttp import web

from LazyIVQueue.utils.logger import logger
from LazyIVQueue.webhook.filter import process_pokemon_webhook, process_census_webhook
from LazyIVQueue.rarity.manager import RarityManager
from LazyIVQueue.queue.iv_queue import IVQueueManager
import LazyIVQueue.config as AppConfig


class LazyIVQueueServer:
    """
    Main API server for LazyIVQueue.

    Provides endpoints for:
    - POST /webhook - Receive Pokemon webhooks from Golbat (secured)
    - GET /health - Health check
    - GET /stats - Queue statistics
    - GET /queue - Queue preview (next N entries)
    - GET /config - Current configuration summary

    Security:
    - ALLOWED_IPS: Restrict webhook access to specific IPs
    - HEADERS: Require specific header for webhook access (format: "HeaderName: Value")
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ):
        self.host = host or AppConfig.lazyivqueue_host
        self.port = port or AppConfig.lazyivqueue_port
        self.allowed_ips = self._parse_allowed_ips(AppConfig.allowed_ips)
        self.auth_header = AppConfig.headers
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    def _parse_allowed_ips(self, ips: Optional[List[str]]) -> Set[str]:
        """Parse and validate allowed IPs list."""
        if not ips:
            return set()

        result = set()
        for ip in ips:
            ip = ip.strip()
            if ip:
                result.add(ip)

        return result

    def _validate_ip(self, request: web.Request) -> bool:
        """Validate client IP against whitelist."""
        if not self.allowed_ips:
            return True  # No whitelist = allow all

        # Get client IP
        client_ip = request.remote

        # Handle X-Forwarded-For if behind proxy
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Take the first IP (original client)
            client_ip = forwarded.split(",")[0].strip()

        return client_ip in self.allowed_ips

    def _validate_auth(self, request: web.Request) -> bool:
        """Validate Authorization header."""
        if not self.auth_header:
            return True

        # Parse expected header (format: "HeaderName: Value")
        if ":" in self.auth_header:
            header_name, expected_value = self.auth_header.split(":", 1)
            header_name = header_name.strip()
            expected_value = expected_value.strip()

            actual_value = request.headers.get(header_name)
            return actual_value == expected_value

        return True

    async def handle_webhook(self, request: web.Request) -> web.Response:
        """
        Handle incoming webhook POST requests.

        Expected payload format from Golbat:
        {
            "type": "pokemon",
            "message": { ... pokemon data ... }
        }
        or array of messages:
        [{"type": "pokemon", "message": {...}}, ...]
        """
        # Validate IP
        if not self._validate_ip(request):
            client_ip = request.remote
            logger.warning(f"Rejected webhook from unauthorized IP: {client_ip}")
            return web.Response(status=403, text="Forbidden")

        # Validate auth
        if not self._validate_auth(request):
            logger.warning(f"Rejected webhook with invalid auth from: {request.remote}")
            return web.Response(status=401, text="Unauthorized")

        try:
            payload = await request.json()
            await self._process_payload(payload)
            return web.Response(status=200, text="OK")
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return web.Response(status=500, text="Internal Error")

    async def _process_payload(self, payload) -> None:
        """Process webhook payload - handle single message or array."""
        messages = payload if isinstance(payload, list) else [payload]

        for msg in messages:
            msg_type = msg.get("type")

            # We only care about pokemon messages
            if msg_type == "pokemon":
                pokemon_data = msg.get("message", {})
                if pokemon_data:
                    await process_pokemon_webhook(pokemon_data)

    async def handle_census(self, request: web.Request) -> web.Response:
        """
        Handle incoming census webhook POST requests.
        Receives ALL Pokemon spawns for rarity tracking.

        Expected payload format from Golbat:
        {
            "type": "pokemon",
            "message": { ... pokemon data ... }
        }
        or array of messages.
        """
        # Validate IP
        if not self._validate_ip(request):
            client_ip = request.remote
            logger.warning(f"Rejected census webhook from unauthorized IP: {client_ip}")
            return web.Response(status=403, text="Forbidden")

        # Validate auth
        if not self._validate_auth(request):
            logger.warning(f"Rejected census webhook with invalid auth from: {request.remote}")
            return web.Response(status=401, text="Unauthorized")

        try:
            payload = await request.json()
            await self._process_census_payload(payload)
            return web.Response(status=200, text="OK")
        except Exception as e:
            logger.error(f"Error processing census webhook: {e}")
            return web.Response(status=500, text="Internal Error")

    async def _process_census_payload(self, payload) -> None:
        """Process census webhook payload - feeds RarityManager."""
        messages = payload if isinstance(payload, list) else [payload]

        for msg in messages:
            msg_type = msg.get("type")

            # We only care about pokemon messages good sir
            if msg_type == "pokemon":
                pokemon_data = msg.get("message", {})
                if pokemon_data:
                    await process_census_webhook(pokemon_data)

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "healthy"})

    async def handle_stats(self, request: web.Request) -> web.Response:
        """Queue and rarity statistics endpoint."""
        try:
            queue = await IVQueueManager.get_instance()
            stats = {
                "queue": await queue.get_stats(),
            }

            # Add rarity stats if auto_rarity is enabled
            if AppConfig.auto_rarity_enabled:
                rarity_manager = await RarityManager.get_instance()
                stats["rarity"] = await rarity_manager.get_stats()

            return web.json_response(stats)
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_queue_preview(self, request: web.Request) -> web.Response:
        """Queue preview endpoint - shows next N entries."""

        try:
            count = int(request.query.get("count", 10))
            count = min(count, 100)  # Cap at 100

            queue = await IVQueueManager.get_instance()
            preview = queue.get_next_entries_preview(count)

            return web.json_response({
                "count": len(preview),
                "entries": preview
            })
        except Exception as e:
            logger.error(f"Error getting queue preview: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_rarity(self, request: web.Request) -> web.Response:
        """
        Auto Rarity rankings endpoint.
        Shows Pokemon ranked by rarity for each area.

        Query params:
            area: Filter to specific area (optional)
            limit: Max Pokemon per area (default 100, max 500)
        """
        if not AppConfig.auto_rarity_enabled:
            return web.json_response(
                {"error": "Auto Rarity is not enabled. Set AUTO_RARITY=TRUE in .env"},
                status=400
            )

        try:
            area = request.query.get("area")
            limit = min(int(request.query.get("limit", 100)), 500)

            rarity_manager = await RarityManager.get_instance()
            rankings = await rarity_manager.get_rankings(area=area, limit=limit)

            return web.json_response(rankings)
        except Exception as e:
            logger.error(f"Error getting rarity rankings: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_config(self, request: web.Request) -> web.Response:
        """Current configuration summary."""
        try:
            config_summary = {
                "server": {
                    "host": self.host,
                    "port": self.port,
                },
                "scout": {
                    "concurrency": AppConfig.concurrency_scout,
                },
                "ivlist": {
                    "count": len(AppConfig.ivlist),
                    "top_5": AppConfig.ivlist[:5] if AppConfig.ivlist else [],
                },
                "geofences": {
                    "expire_cache_seconds": AppConfig.geofence_expire_cache_seconds,
                    "refresh_cache_seconds": AppConfig.geofence_refresh_cache_seconds,
                },
                "security": {
                    "allowed_ips_count": len(self.allowed_ips),
                    "header_auth_enabled": bool(self.auth_header),
                },
            }
            return web.json_response(config_summary)
        except Exception as e:
            logger.error(f"Error getting config: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def start(self) -> None:
        """Start the API server."""
        self._app = web.Application()
        self._app.router.add_post("/webhook", self.handle_webhook)
        self._app.router.add_post("/webhook/census", self.handle_census)
        self._app.router.add_get("/health", self.handle_health)
        self._app.router.add_get("/stats", self.handle_stats)
        self._app.router.add_get("/queue", self.handle_queue_preview)
        self._app.router.add_get("/rarity", self.handle_rarity)
        self._app.router.add_get("/config", self.handle_config)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        logger.info(f"LazyIVQueue server started on http://{self.host}:{self.port}")
        logger.debug(f"  POST /webhook        - Receive Golbat webhooks (secured)")
        logger.debug(f"  POST /webhook/census - Receive ALL spawns for rarity tracking")
        logger.debug(f"  GET  /health         - Health check")
        logger.debug(f"  GET  /stats          - Queue and rarity statistics")
        logger.debug(f"  GET  /queue          - Queue preview (?count=N)")
        logger.debug(f"  GET  /rarity         - Auto Rarity rankings (?area=X&limit=N)")
        logger.debug(f"  GET  /config         - Configuration summary")

        if self.allowed_ips:
            logger.info(f"Webhook IP whitelist: {len(self.allowed_ips)} IPs allowed")
        if self.auth_header:
            logger.info("Webhook header authentication enabled")

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        if self._site:
            await self._site.stop()

        if self._runner:
            await self._runner.cleanup()

        logger.info("LazyIVQueue server stopped")
