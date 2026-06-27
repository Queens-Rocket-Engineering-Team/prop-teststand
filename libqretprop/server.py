import asyncio
import os

import redis

import libqretprop
import libqretprop.config_manager as config
import libqretprop.redis_logging as ml
from libqretprop.api import fastAPI
from libqretprop.daemons.cliTerminal import commandProcessor
from libqretprop.device_controllers import cameraTools, kasaTools
from libqretprop.runtime.services import build_runtime


async def main(noDiscovery: bool = False) -> None:
    """Run the server."""

    # -------
    # INITIALIZATION
    # -------

    # Load server configuration
    configPath = os.getenv("PROP_CONFIG", "./config.yaml")
    config.load_config(configPath)

    # Initialize Redis client for logging
    redisClient = redis.Redis(host=config.serverConfig["services"]["redis"]["ip"],
                              port=config.serverConfig["services"]["redis"]["port"],
                              db=0,
                              username=config.serverConfig["accounts"]["redis"]["username"],
                              password=config.serverConfig["accounts"]["redis"]["password"],
                              decode_responses=True,
                              )

    ml.init_logger(redisClient)
    ml.slog(f"Starting server (version: {libqretprop.__version__})...")

    # Build the runtime object graph (pure construction — no I/O, no tasks yet).
    runtime = build_runtime()

    loop = asyncio.get_event_loop()
    daemons: dict[str, asyncio.Task[None]] = {}

    # Fire up the FastAPI app and add it as a daemon task
    daemons["fastAPI"] = loop.create_task(fastAPI.startAPI(runtime))

    # -------
    # CONFIG OPTIONS
    # -------

    if not noDiscovery:
        # Start ESP/telemetry/state daemon tasks (TCP, UDP, discovery, display flush).
        runtime.start(loop)
        ml.slog("Started ESP/telemetry/state daemon tasks.")

    # Connect to all cameras
    daemons["cameraConnector"] = loop.create_task(cameraTools.connectAllCameras())
    ml.slog("Started cameraConnector daemon task.")

    # Discover all Kasa devices
    daemons["kasaDiscoverer"] = loop.create_task(kasaTools.discoverKasaDevices())
    ml.slog("Started kasaDiscoverer daemon task.")

    # Command line interface daemon
    daemons["commandProcessor"] = loop.create_task(commandProcessor(runtime))


    try:
        # -------
        # MAIN SERVER LOOP
        # -------
        stop_event = asyncio.Event()

        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            ml.slog("KeyboardInterrupt: stopping server.")
            stop_event.set()
        except asyncio.CancelledError:
            ml.slog("Server main loop cancelled.")

    # -------
    # CLEANUP
    # -------
    finally:
        # Write all collected devices to the redis log on exit
        devices = runtime.esp_runtime.get_registered_devices()
        if devices:
            ml.slog(f"Registered devices at shutdown: {', '.join(devices.keys())}")

        # Cancel app-level daemon tasks (fastAPI, camera, kasa, commandProcessor)
        for name, task in daemons.items():
            if not task.done():
                task.cancel()
                ml.slog(f"Cancelled {name} daemon task.")
        await asyncio.gather(*daemons.values(), return_exceptions=True)

        # Stop ESP/telemetry/state daemons and close device connections
        await runtime.stop()

        print("\nServer stopped.\n")

if __name__ == "__main__":
    asyncio.run(main())
