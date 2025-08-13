import asyncio
from collections.abc import AsyncGenerator

import aioconsole

import libqretprop.mylogging as ml
from libqretprop.DeviceControllers import deviceTools
from libqretprop.Devices.SensorMonitor import SensorMonitor


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
    "STREAM",
    "STOP",
    "CONTROL",
    ]


async def handleServerCommand(command: str, args: list) -> None:
    ml.slog(f"Server command received: {command}")
    cmd = command.upper()
    if cmd in ("QUIT", "EXIT"):
        ml.slog("Shutting down server...")
        await asyncio.sleep(0.1)
    elif cmd == "SCAN":
        ml.slog("Sending multicast discovery request.")
        deviceTools.sendMulticastDiscovery()
    elif cmd == "CONN":
        if not args:
            ml.slog("No IP address provided for direct connection.")
            return
        deviceIP = args[0]
        await deviceTools.connectToDevice(deviceIP)
    elif cmd == "LIST":
        devices = deviceTools.getRegisteredDevices()
        if not devices:
            ml.log("No devices connected.")
        else:
            ml.log("Connected devices:")
            for device in devices.values():
                ml.log(f"{device.name} ({device.type}) - {device.address}")
    elif cmd == "EXPO":
        deviceTools.exportDataToCSV()

async def handleDeviceCommand(command: str, args: list) -> None:
    devices = deviceTools.getRegisteredDevices()
    if not devices:
        ml.slog("No devices connected to send command to")
        return
    cmd = command.upper()
    for device in devices.values():
        if isinstance(device, SensorMonitor):
            try:
                if cmd == "GETS":
                    deviceTools.getSingle(device)
                elif cmd == "STREAM":
                    deviceTools.startStreaming(device, args)
                elif cmd == "STOP":
                    deviceTools.stopStreaming(device)
                elif cmd == "CONTROL":
                    deviceTools.setControl(device, args)
            except Exception as e:
                ml.elog(f"Error sending command to {device.name}: {e}")

async def processCommand(command: str) -> None:
    """Process a command and send it to all connected devices.

    These commands can be device level commands or server level commands.

    Args:
        command: The command string to process and send

    """
    fullCommand = command.strip()
    cmd = fullCommand.split(" ")[0]
    args = fullCommand.split(" ")[1:]

    if cmd.upper() in SERVERCOMMANDS:
        await handleServerCommand(cmd, args)
    elif cmd.upper() in DEVICECOMMANDS:
        await handleDeviceCommand(cmd, args)
    else:
        ml.elog(f"Unknown command: {cmd}. Available commands: {', '.join(SERVERCOMMANDS + DEVICECOMMANDS)}")

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

