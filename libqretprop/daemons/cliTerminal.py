import asyncio
from collections.abc import AsyncGenerator

import aioconsole

import libqretprop.mylogging as ml
from libqretprop.DeviceControllers import deviceTools
from libqretprop.Devices.SensorMonitor import SensorMonitor


SERVERCOMMANDS = [
    "QUIT",
    "EXIT",
    "DISCOVER",
    "LIST",
    "EXPO",
    "HELP",
    "INFO",
    "SENSORS",
    "CONTROLS",
    "WATCH",
    "REMOVE",
    ]

DEVICECOMMANDS = [
    "GETS",
    "STREAM",
    "STOP",
    "CONTROL",
    "OPEN",
    "CLOSE",
    ]


async def handleServerCommand(command: str, args: list) -> None:
    ml.slog(f"Server command received: {command}")
    cmd = command.upper()
    if cmd in ("QUIT", "EXIT"):
        ml.slog("Shutting down server...")
        await asyncio.sleep(0.1)
    elif cmd == "DISCOVER":
        ml.slog("Sending discovery broadcast...")
        deviceTools.sendDiscoveryBroadcast()
        ml.slog("Discovery sent. Devices will auto-connect.")
    elif cmd == "LIST":
        devices = deviceTools.getRegisteredDevices()
        if not devices:
            ml.slog("No devices connected.")
            ml.slog("  Try: discover")
        else:
            ml.slog(f"Connected devices ({len(devices)}):")
            for device in devices.values():
                ml.slog(f"  {device.name} ({device.type}) - {device.address}")
                if isinstance(device, SensorMonitor):
                    ml.slog(f"    Sensors: {len(device.sensors)}, Controls: {len(device.controls)}")
    elif cmd == "REMOVE":
        if not args:
            ml.slog("Usage: remove <device_name>")
            return
        devices = deviceTools.getRegisteredDevices()
        device = None
        for d in devices.values():
            if d.name.upper() == args[0].upper():
                device = d
                break
        if not device:
            ml.slog(f"Device '{args[0]}' not currently registered. Use \"LIST\" to see devices.")
            return
        deviceTools.removeDevice(device)
        ml.slog(f"Removed device '{device.name}'")

    elif cmd == "INFO":
        if not args:
            ml.slog("Usage: info <device_name>")
            return
        devices = deviceTools.getRegisteredDevices()
        device = None
        for d in devices.values():
            if d.name.upper() == args[0].upper():
                device = d
                break
        if not device:
            ml.slog(f"Device '{args[0]}' not found")
            return
        ml.slog(f"Device: {device.name}")
        ml.slog(f"  Type: {device.type}")
        ml.slog(f"  Address: {device.address}")
        if isinstance(device, SensorMonitor):
            ml.slog(f"  Sensors ({len(device.sensors)}):")
            for idx, name in enumerate(device.sensors.keys()):
                ml.slog(f"    [{idx}] {name}")
            ml.slog(f"  Controls ({len(device.controls)}):")
            for idx, name in enumerate(device.controls.keys()):
                ml.slog(f"    [{idx}] {name}")
    elif cmd == "HELP":
        ml.slog("Available commands:")
        ml.slog("  discover           - Discover devices")
        ml.slog("  list               - Show connected devices")
        ml.slog("  info <device>      - Show device details")
        ml.slog("  stream <dev> <hz>  - Start streaming")
        ml.slog("  stop <device>      - Stop streaming")
        ml.slog("  open <dev> <ctrl>  - Open valve/control")
        ml.slog("  close <dev> <ctrl> - Close valve/control")
        ml.slog("  expo               - Export data to CSV")
        ml.slog("  quit               - Exit")
    elif cmd == "EXPO":
        deviceTools.exportDataToCSV()
        ml.slog("Data exported to test_data/")

async def handleDeviceCommand(command: str, args: list) -> None:
    if not args:
        ml.slog(f"Usage: {command.lower()} <device_name> [args...]")
        return

    device_name = args[0]
    devices = deviceTools.getRegisteredDevices()
    device = None
    for d in devices.values():
        if d.name.lower() == device_name.lower():
            device = d
            break

    if not device:
        ml.slog(f"Device '{device_name}' not found. Use 'list' to see devices.")
        return

    if not isinstance(device, SensorMonitor):
        ml.slog(f"Device is not a sensor monitor")
        return

    cmd = command.upper()
    try:
        if cmd == "GETS":
            await deviceTools.getSingle(device)
            ml.slog(f"Requested data from {device.name}")
        elif cmd == "STREAM":
            if len(args) < 2:
                ml.slog("Usage: stream <device> <frequency_hz>")
                return
            freq = int(args[1])
            await deviceTools.startStreaming(device, freq)
            ml.slog(f"Streaming from {device.name} at {freq} Hz")
        elif cmd == "STOP":
            await deviceTools.stopStreaming(device)
            ml.slog(f"Stopped streaming from {device.name}")
        elif cmd == "CONTROL":
            if len(args) < 3:
                ml.slog("Usage: control <device> <name> <open|close>")
                return
            await deviceTools.setControl(device, args[1], args[2])
            ml.slog(f"Sent {args[2]} to {args[1]} on {device.name}")
        elif cmd == "OPEN":
            if len(args) < 2:
                ml.slog("Usage: open <device> <control_name>")
                return
            await deviceTools.setControl(device, args[1], "OPEN")
            ml.slog(f"Opened {args[1]} on {device.name}")
        elif cmd == "CLOSE":
            if len(args) < 2:
                ml.slog("Usage: close <device> <control_name>")
                return
            await deviceTools.setControl(device, args[1], "CLOSE")
            ml.slog(f"Closed {args[1]} on {device.name}")
    except Exception as e:
        ml.elog(f"Error: {e}")

async def processCommand(command: str) -> None:
    """Process a command and send it to all connected devices."""
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