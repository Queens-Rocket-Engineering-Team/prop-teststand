import asyncio
from collections.abc import AsyncGenerator

import aioconsole

import libqretprop.mylogging as ml
from libqretprop.DeviceControllers import deviceTools


SERVERCOMMANDS = [
    "QUIT",
    "EXIT",
    "CONN",
    "LIST",
    "SCAN",
    "EXPO",
    ]

DEVICECOMMANDS = [
    "GETS",
    "STRM",
    "STOP",
    ]


async def processCommand(command: str) -> None:
    """Process a command and send it to all connected devices.

    These commands can be device level commands or server level commands.

    Args:
        command: The command string to process and send

    """
    fullCommand = command.strip()

    command = fullCommand.split(" ")[0]  # Get the first word as the command
    args = fullCommand.split(" ")[1:]  # Get the rest as arguments

    if command.upper() in SERVERCOMMANDS:
        ml.slog(f"Server command received: {command}")
        if command.upper() == "QUIT" or command.upper() == "EXIT":
            ml.slog("Shutting down server...")
            await asyncio.sleep(0.1)

        elif command.upper() == "SCAN":
            ml.slog("Sending multicast discovery request.")
            deviceTools.sendMulticastDiscovery()

        elif command.upper() == "CONN":
            if not args:
                ml.slog("No IP address provided for direct connection.")
                return
            deviceIP = args[0]
            await deviceTools.connectToDevice(deviceIP)

        elif command.upper() == "LIST":
            devices = deviceTools.getRegisteredDevices()
            if not devices:
                ml.log("No devices connected.")
            else:
                ml.log("Connected devices:")
                for device in devices.values():
                    ml.log(f"{device.name} ({device.type}) - {device.address}")

        elif command.upper() == "EXPO": # Export data to CSV
            deviceTools.exportDataToCSV()

    elif command.upper() in DEVICECOMMANDS:
        devices = deviceTools.getRegisteredDevices()
        if not devices:
            ml.slog("No devices connected to send command to")
            return

        for device in devices.values():
            try:
                if command.upper() == "GETS": deviceTools.getSingle(device)
                elif command.upper() == "STRM": deviceTools.startStreaming(device)
                elif command.upper() == "STOP": deviceTools.stopStreaming(device)

            except Exception as e:
                ml.elog(f"Error sending command to {device.name}: {e}")

    else:
        ml.elog(f"Unknown command: {command}. Available commands: {', '.join(SERVERCOMMANDS + DEVICECOMMANDS)}")

async def cliReader() -> AsyncGenerator[str, None]:
    """Read commands from CLI input."""
    while True:
        try:
            command = await aioconsole.ainput("QRET> ")
            if command.lower() in ["quit", "exit"]:
                raise KeyboardInterrupt
            yield command.strip()
        except asyncio.CancelledError:
            break

async def commandProcessor() -> None:
    """Process commands from various input sources."""
    ml.slog("Started command processor daemon task.")

    try:
        async for command in cliReader():
            if command:
                await processCommand(command)
    except KeyboardInterrupt:
        ml.slog("Command processor stopped by user")
        raise

