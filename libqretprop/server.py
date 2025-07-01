import asyncio
import time
from enum import Enum

import redis

import libqretprop.mylogging as ml
from libqretprop.DeviceControllers import discoveryTools


# Server state enumeration using Enum
class ServerState(Enum):
    INITIALIZING = 0
    WAITING = 1
    READY = 2

async def main() -> None:
    """Run the server."""

    # Initialize Redis client for logging
    redisClient = redis.Redis(host="localhost", port=6379, db=0)
    ml.initLogger(redisClient)

    loop = asyncio.get_event_loop()
    daemons: dict[str, asyncio.Task[None]] = {}

    try:
        # Start daemon tasks
        daemons["deviceListener"] = loop.create_task(discoveryTools.deviceListener())
        ml.slog("Started deviceListener daemon task.")

        # daemons["multicastHeartbeat"] = loop.create_task(discoveryTools.continuousMulticastDiscovery())
        # ml.slog("Started multicastHeartbeat daemon task.")

        # Send a multicast discovery request immediately
        await asyncio.sleep(0.1)  # Give the listener time to start
        discoveryTools.sendMulticastDiscovery()

        # Main loop using asyncio.Event for efficient waiting
        stop_event = asyncio.Event()

        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            ml.slog("KeyboardInterrupt: stopping server.")
            stop_event.set()
        except asyncio.CancelledError:
            ml.slog("Server main loop cancelled.")

    finally:
        # Write all collected devices to the redis log on exit
        devices = discoveryTools.getRegisteredDevices()
        if devices:
            ml.slog(f"Registered devices at shutdown: {', '.join(devices.keys())}")

        # Cancel all daemon tasks
        for name, task in daemons.items():
            if not task.done():
                task.cancel()
                ml.slog(f"Cancelled {name} daemon task.")
        await asyncio.gather(*daemons.values(), return_exceptions=True)

        # Close all open device sockets
        discoveryTools.closeDeviceConnections()

if __name__ == "__main__":
    asyncio.run(main())
