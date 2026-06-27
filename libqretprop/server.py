import asyncio
import logging
import os

import libqretprop
import libqretprop.config_manager as config
from libqretprop.api import fast_api
from libqretprop.daemons.cli_terminal import command_processor
from libqretprop.runtime.logging import configure_logging
from libqretprop.runtime.services import build_runtime


logger = logging.getLogger(__name__)


async def main(no_discovery: bool = False) -> None:
    """Run the server."""

    # -------
    # INITIALIZATION
    # -------

    # Load server configuration
    config_path = os.getenv("PROP_CONFIG", "./config.yaml")
    config.load_config(config_path)

    # Build the runtime object graph (pure construction; no sockets or tasks yet).
    runtime = build_runtime()
    configure_logging(runtime.log_stream)
    logger.info(f"Starting server (version: {libqretprop.__version__})...")

    loop = asyncio.get_event_loop()
    daemons: dict[str, asyncio.Task[None]] = {}

    # Fire up the FastAPI app and add it as a daemon task
    daemons["fast_api"] = loop.create_task(fast_api.start_api(runtime))

    # -------
    # CONFIG OPTIONS
    # -------

    runtime.start(loop, include_device_daemons=not no_discovery)
    if not no_discovery:
        # Start ESP/telemetry/state daemon tasks (TCP, UDP, discovery, display flush).
        logger.info("Started ESP/telemetry/state daemon tasks.")
    logger.info("Started log_stream daemon task.")

    # Command line interface daemon
    daemons["command_processor"] = loop.create_task(command_processor(runtime))


    try:
        # -------
        # MAIN SERVER LOOP
        # -------
        stop_event = asyncio.Event()

        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt: stopping server.")
            stop_event.set()
        except asyncio.CancelledError:
            logger.info("Server main loop cancelled.")

    # -------
    # CLEANUP
    # -------
    finally:
        # Write all collected devices to the log stream on exit.
        devices = runtime.esp_runtime.get_registered_devices()
        if devices:
            logger.info(f"Registered devices at shutdown: {', '.join(devices.keys())}")

        # Cancel app-level interface tasks.
        for name, task in daemons.items():
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled {name} daemon task.")
        await asyncio.gather(*daemons.values(), return_exceptions=True)

        # Stop runtime tasks and close device connections
        await runtime.stop()

        print("\nServer stopped.\n")

if __name__ == "__main__":
    asyncio.run(main())
