import asyncio
from enum import Enum

import redis
import os

import libqretprop.mylogging as ml
from libqretprop.API import fastAPI
from libqretprop.daemons.cliTerminal import commandProcessor
from libqretprop.DeviceControllers import deviceTools


PI_IP = "192.168.1.100"

# Server state enumeration using Enum
class ServerState(Enum):
    INITIALIZING = 0
    WAITING = 1
    READY = 2

async def main(directIP: str | None = None,
               noDiscovery: bool = False,
               cmdLine: bool = True, # FIXME change to default false later
               ) -> None:
    """Run the server."""

    state = ServerState.INITIALIZING

    # -------
    # INITIALIZATION
    # -------

    # Read Redis host from environment variable (for docker compose) or default to localhost
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")

    # Initialize Redis client for logging
    redisClient = redis.Redis(host=REDIS_HOST,
                              port=6379,
                              db=0,
                              username="server",
                              password="propteambestteam",
                              decode_responses=True,
                              )

    ml.initLogger(redisClient)
    ml.log("Starting server...")

    loop = asyncio.get_event_loop()
    daemons: dict[str, asyncio.Task[None]] = {}

    # Fire up the FastAPI app and add it as a daemon task
    daemons["fastAPI"] = loop.create_task(fastAPI.startAPI())

    # -------
    # CONFIG OPTIONS
    # -------

    if not noDiscovery:
        # Listener daemon will run in the background to listen for SSDP responses and update the device registry
        daemons["deviceListener"] = loop.create_task(deviceTools.deviceListener())
        ml.slog("Started deviceListener daemon task.")

        # Send a multicast discovery request immediately
        await asyncio.sleep(0.1)  # Give the listener time to start
        deviceTools.sendMulticastDiscovery()

    # Command line interface daemon
    if cmdLine: daemons["commandProcessor"] = loop.create_task(commandProcessor())

    # If a direct IP is provided, connect to the device directly
    if directIP:
        await deviceTools.connectToDevice(directIP)
        ml.slog(f"Connecting directly to device at {directIP}.")


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
