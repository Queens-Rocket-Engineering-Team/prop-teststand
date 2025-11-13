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
    "HELP",
    "INFO",
    "SENSORS",
    "CONTROLS",
    "WATCH",
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
    elif cmd == "SCAN":
        ml.log("Sending discovery broadcast...")
        deviceTools.sendMulticastDiscovery()
        ml.log("✓ Discovery sent. Devices will auto-connect.")
    elif cmd == "CONN":
        if not args:
            ml.log("Usage: conn <ip_address>")
            return
        deviceIP = args[0]
        await deviceTools.connectToDevice(deviceIP)
    elif cmd == "LIST":
        devices = deviceTools.getRegisteredDevices()
        if not devices:
            ml.log("⚠ No devices connected.")
            ml.log("  Try: scan")
        else:
            ml.log(f"Connected devices ({len(devices)}):")
            for device in devices.values():
                ml.log(f"  • {device.name} ({device.type}) - {device.address}")
                if isinstance(device, SensorMonitor):
                    ml.log(f"    Sensors: {len(device.sensors)}, Controls: {len(device.controls)}")
    elif cmd == "INFO":
        if not args:
            ml.log("Usage: info <device_name>")
            return
        devices = deviceTools.getRegisteredDevices()
        device = None
        for d in devices.values():
            if d.name.lower() == args[0].lower():
                device = d
                break
        if not device:
            ml.log(f"✗ Device '{args[0]}' not found")
            return
        ml.log(f"Device: {device.name}")
        ml.log(f"  Type: {device.type}")
        ml.log(f"  Address: {device.address}")
        if isinstance(device, SensorMonitor):
            ml.log(f"  Sensors ({len(device.sensors)}):")
            for idx, name in enumerate(device.sensors.keys()):
                ml.log(f"    [{idx}] {name}")
            ml.log(f"  Controls ({len(device.controls)}):")
            for idx, name in enumerate(device.controls.keys()):
                ml.log(f"    [{idx}] {name}")
    elif cmd == "HELP":
        ml.log("Available commands:")
        ml.log("  scan              - Discover devices")
        ml.log("  list              - Show connected devices")
        ml.log("  info <device>     - Show device details")
        ml.log("  stream <dev> <hz> - Start streaming")
        ml.log("  stop <device>     - Stop streaming")
        ml.log("  open <dev> <ctrl> - Open valve/control")
        ml.log("  close <dev> <ctrl>- Close valve/control")
        ml.log("  expo              - Export data to CSV")
        ml.log("  quit              - Exit")
    elif cmd == "EXPO":
        deviceTools.exportDataToCSV()
        ml.log("✓ Data exported to test_data/")

async def handleDeviceCommand(command: str, args: list) -> None:
    if not args:
        ml.log(f"Usage: {command.lower()} <device_name> [args...]")
        return

    device_name = args[0]
    devices = deviceTools.getRegisteredDevices()
    device = None
    for d in devices.values():
        if d.name.lower() == device_name.lower():
            device = d
            break

    if not device:
        ml.log(f"✗ Device '{device_name}' not found. Use 'list' to see devices.")
        return

    if not isinstance(device, SensorMonitor):
        ml.log(f"✗ Device is not a sensor monitor")
        return

    cmd = command.upper()
    try:
        if cmd == "GETS":
            await deviceTools.getSingle(device)
            ml.log(f"✓ Requested data from {device.name}")
        elif cmd == "STREAM":
            if len(args) < 2:
                ml.log("Usage: stream <device> <frequency_hz>")
                return
            freq = int(args[1])
            await deviceTools.startStreaming(device, freq)
            ml.log(f"✓ Streaming from {device.name} at {freq} Hz")
        elif cmd == "STOP":
            await deviceTools.stopStreaming(device)
            ml.log(f"✓ Stopped streaming from {device.name}")
        elif cmd == "CONTROL":
            if len(args) < 3:
                ml.log("Usage: control <device> <name> <open|close>")
                return
            await deviceTools.setControl(device, args[1], args[2])
            ml.log(f"✓ Sent {args[2]} to {args[1]} on {device.name}")
        elif cmd == "OPEN":
            if len(args) < 2:
                ml.log("Usage: open <device> <control_name>")
                return
            await deviceTools.setControl(device, args[1], "OPEN")
            ml.log(f"✓ Opened {args[1]} on {device.name}")
        elif cmd == "CLOSE":
            if len(args) < 2:
                ml.log("Usage: close <device> <control_name>")
                return
            await deviceTools.setControl(device, args[1], "CLOSE")
            ml.log(f"✓ Closed {args[1]} on {device.name}")
    except Exception as e:
        ml.elog(f"Error: {e}")

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

