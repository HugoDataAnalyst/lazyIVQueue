"""LazyIVQueue - Pokemon IV Scouting Coordinator.

Main entry point for the application. Orchestrates:
- Geofence loading from Koji
- Webhook server for receiving Golbat data
- IV queue management
- Scout coordination with Dragonite API
"""
from __future__ import annotations

import asyncio
import signal
import sys
from typing import Optional

import LazyIVQueue.config as AppConfig
from LazyIVQueue import __version__
from LazyIVQueue.utils.logger import logger, setup_logging
from LazyIVQueue.utils.koji_geofences import KojiGeofenceManager
from LazyIVQueue.queue.iv_queue import IVQueueManager
from LazyIVQueue.scout.coordinator import ScoutCoordinator
from LazyIVQueue.api.server import LazyIVQueueServer


class LazyIVQueueApp:
    """
    Main application orchestrator.

    Lifecycle:
    1. Initialize logging
    2. Load geofences immediately from Koji
    3. Start geofence refresh background task
    4. Initialize IV queue
    5. Start webhook server
    6. Start scout coordinator loop
    7. Handle graceful shutdown
    """

    def __init__(self) -> None:
        self._geofence_manager: Optional[KojiGeofenceManager] = None
        self._queue_manager: Optional[IVQueueManager] = None
        self._server: Optional[LazyIVQueueServer] = None
        self._scout_coordinator: Optional[ScoutCoordinator] = None
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start all application components."""
        logger.info("=" * 60)
        logger.info(f"LazyIVQueue v{__version__} - Pokemon IV Scouting Coordinator")
        logger.info("=" * 60)

        # 1. Initialize geofences (fetch immediately)
        logger.info("Loading geofences from Koji...")
        self._geofence_manager = await KojiGeofenceManager.get_instance()
        await self._geofence_manager.initialize()

        geofence_count = self._geofence_manager.get_geofence_count()
        if geofence_count > 0:
            logger.info(f"Loaded {geofence_count} geofences")
            for name in self._geofence_manager.get_all_geofence_names():
                logger.debug(f"  - {name}")
        else:
            logger.warning("No geofences loaded - all Pokemon will be rejected!")

        # 2. Initialize IV queue
        logger.info("Initializing IV queue...")
        self._queue_manager = await IVQueueManager.get_instance()

        # 3. Start API server (webhooks + admin endpoints)
        logger.info("Starting API server...")
        self._server = LazyIVQueueServer()
        await self._server.start()

        # 4. Start scout coordinator
        logger.info("Starting scout coordinator...")
        self._scout_coordinator = await ScoutCoordinator.get_instance()
        await self._scout_coordinator.start()

        # 5. Start cleanup task for expired entries
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        # Log startup summary
        logger.info("-" * 60)
        logger.info("LazyIVQueue started successfully")
        logger.info(f"  Server: http://{AppConfig.lazyivqueue_host}:{AppConfig.lazyivqueue_port}")
        logger.info(f"  Webhook: http://{AppConfig.lazyivqueue_host}:{AppConfig.lazyivqueue_port}/webhook")
        logger.info(f"  Scout concurrency: {AppConfig.concurrency_scout}")
        logger.info(f"  IV list entries: {len(AppConfig.ivlist)}")
        if AppConfig.ivlist:
            logger.info(f"  Priority order: {', '.join(AppConfig.ivlist[:5])}...")
        logger.info("-" * 60)

    async def _cleanup_loop(self) -> None:
        """Periodically clean up expired and timed-out queue entries."""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                if self._queue_manager:
                    # Clean up expired entries (disappear_time passed)
                    await self._queue_manager.cleanup_expired()
                    # Clean up scouts that didn't receive IV data within timeout
                    await self._queue_manager.cleanup_timed_out_scouts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def run(self) -> None:
        """Run the application until shutdown signal."""
        await self.start()

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown of all components."""
        logger.info("Shutting down LazyIVQueue...")

        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Stop in reverse order
        if self._scout_coordinator:
            await self._scout_coordinator.stop()

        if self._server:
            await self._server.shutdown()

        if self._geofence_manager:
            await self._geofence_manager.shutdown()

        logger.info("LazyIVQueue shutdown complete")

    def trigger_shutdown(self) -> None:
        """Trigger application shutdown (called from signal handler)."""
        logger.info("Shutdown signal received")
        self._shutdown_event.set()


def setup_signal_handlers(app: LazyIVQueueApp, loop: asyncio.AbstractEventLoop) -> None:
    """Setup signal handlers for graceful shutdown."""
    if sys.platform == "win32":
        # Windows doesn't support add_signal_handler
        # Use signal.signal instead
        def handler(signum, frame):
            app.trigger_shutdown()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
    else:
        # Unix-like systems
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, app.trigger_shutdown)


async def main() -> None:
    """Main entry point."""
    # Initialize logging
    setup_logging(
        AppConfig.log_level,
        {"to_file": AppConfig.log_file, "show_function": True},
    )

    # Create and run application
    app = LazyIVQueueApp()

    # Setup signal handlers
    loop = asyncio.get_event_loop()
    setup_signal_handlers(app, loop)

    try:
        await app.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        await app.shutdown()
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
