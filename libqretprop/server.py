import asyncio
import os
from enum import Enum

import redis

import libqretprop
import libqretprop.config_manager as config
import libqretprop.redis_logging as ml
from libqretprop.api import fastAPI
from libqretprop.daemons.cliTerminal import commandProcessor
from libqretprop.device_controllers import cameraTools, deviceTools, kasaTools
from libqretprop.runtime.telemetry_display_stream import telemetry_display_stream


# Server state enumeration using Enum
class ServerState(Enum):
    INITIALIZING = 0
    WAITING = 1
    READY = 2

async def main(noDiscovery: bool = False,
               cmdLine: bool = True,
               ) -> None:
    """Run the server."""

    state = ServerState.INITIALIZING

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

    loop = asyncio.get_event_loop()
    daemons: dict[str, asyncio.Task[None]] = {}

    # Fire up the FastAPI app and add it as a daemon task
    daemons["fastAPI"] = loop.create_task(fastAPI.startAPI())

    # -------
    # CONFIG OPTIONS
    # -------

    if not noDiscovery:
        # TCP listener for incoming device connections
        daemons["tcpListener"] = loop.create_task(deviceTools.tcpListener())
        ml.slog("Started TCP listener daemon task.")

        daemons["udpListener"] = loop.create_task(deviceTools.udpListener())
        ml.slog("Started UDP listener daemon task.")

        daemons["telemetryDisplayFlush"] = loop.create_task(telemetry_display_stream.run())
        ml.slog("Started telemetry display stream flush task.")

        # Start SSDP auto-discovery loop for finding devices on the network
        daemons["autoDiscovery"] = loop.create_task(deviceTools.autoDiscoveryLoop())
        ml.slog("Started SSDP auto-discovery daemon task.")

    # Connect to all cameras
    daemons["cameraConnector"] = loop.create_task(cameraTools.connectAllCameras())
    ml.slog("Started cameraConnector daemon task.")

    # Discover all Kasa devices
    daemons["kasaDiscoverer"] = loop.create_task(kasaTools.discoverKasaDevices())
    ml.slog("Started kasaDiscoverer daemon task.")

    # Command line interface daemon
    if cmdLine:
        daemons["commandProcessor"] = loop.create_task(commandProcessor())


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
        devices = deviceTools.getRegisteredDevices()
        if devices:
            ml.slog(f"Registered devices at shutdown: {', '.join(devices.keys())}")

        # Cancel all daemon tasks
        for name, task in daemons.items():
            if not task.done():
                task.cancel()
                ml.slog(f"Cancelled {name} daemon task.")
        await asyncio.gather(*daemons.values(), return_exceptions=True)

        # Close all open device sockets
        deviceTools.closeDeviceConnections()

        print("\nServer stopped.\n")

if __name__ == "__main__":
    asyncio.run(main())
