from __future__ import annotations
import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import aioconsole

import libqretprop.redis_logging as ml
from libqretprop.device_controllers import deviceTools


if TYPE_CHECKING:
    from libqretprop.runtime.services import RuntimeServices


SERVERCOMMANDS = [
    "QUIT",
    "EXIT",
    "DISCOVER",
    "AUTODISCOVERY",
    "AUTOD",
    "LIST",
    "HELP",
    "INFO",
    "SENSORS",
    "CONTROLS",
    "WATCH",
    "REMOVE",
    "ESTOP",
]

DEVICECOMMANDS = [
    "GETS",
    "STREAM",
    "STOP",
    "CONTROL",
    "OPEN",
    "CLOSE",
    "STATUS",
]


async def handleServerCommand(runtime: RuntimeServices, command: str, args: list) -> None:
    ml.slog(f"Server command received: {command}")
    cmd = command.upper()
    if cmd in ("QUIT", "EXIT"):
        ml.slog("Shutting down server...")
        await asyncio.sleep(0.1)
    elif cmd == "DISCOVER":
        ml.slog("Sending discovery broadcast...")
        runtime.discovery_service.discover()
        ml.slog("Discovery sent. Devices will auto-connect.")
    elif cmd in ("AUTODISCOVERY", "AUTOD"):
        if not args:
            ml.slog(
                f"Autodiscovery: enabled={runtime.discovery_service.periodic_enabled}, interval={runtime.discovery_service.periodic_interval_s}s",
            )
            ml.slog("Usage: autodiscovery <on|off|interval <seconds>|status>")
            return

        sub = args[0].lower()
        if sub in ("status", "show"):
            ml.slog(
                f"Autodiscovery: enabled={runtime.discovery_service.periodic_enabled}, interval={runtime.discovery_service.periodic_interval_s}s",
            )
        elif sub in ("on", "enable", "enabled", "true"):
            runtime.discovery_service.periodic_enabled = True
            ml.slog("Autodiscovery enabled")
        elif sub in ("off", "disable", "disabled", "false"):
            runtime.discovery_service.periodic_enabled = False
            ml.slog("Autodiscovery disabled")
        elif sub == "interval":
            if len(args) < 2:
                ml.slog("Usage: autodiscovery interval <seconds>")
                return
            try:
                interval = float(args[1])
            except ValueError:
                ml.slog(f"Invalid interval: {args[1]!r}. Must be a positive number.")
                return

            if interval <= 0:
                ml.slog("Autodiscovery interval must be greater than 0 seconds")
                return

            runtime.discovery_service.periodic_interval_s = interval
            ml.slog(f"Autodiscovery interval set to {interval}s")
        else:
            ml.slog("Usage: autodiscovery <on|off|interval <seconds>|status>")
    elif cmd == "LIST":
        devices = deviceTools.getRegisteredDevices(runtime)
        if not devices:
            ml.slog("No devices connected.")
            ml.slog("  Try: discover")
        else:
            ml.slog(f"Connected devices ({len(devices)}):")
            for device in devices.values():
                ml.slog(f"  {device.name} ({device.type}) - {device.address}")
                ml.slog(f"    Sensors: {len(device.sensors)}, Controls: {len(device.controls)}")
    elif cmd == "REMOVE":
        if not args:
            ml.slog("Usage: remove <device_name>")
            return
        devices = deviceTools.getRegisteredDevices(runtime)
        device = None
        for d in devices.values():
            if d.name.upper() == args[0].upper():
                device = d
                break
        if not device:
            ml.slog(f"Device '{args[0]}' not currently registered. Use \"LIST\" to see devices.")
            return
        deviceTools.remove_device(runtime, device)
        ml.slog(f"Removed device '{device.name}'")

    elif cmd == "INFO":
        if not args:
            ml.slog("Usage: info <device_name>")
            return
        devices = deviceTools.getRegisteredDevices(runtime)
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
        ml.slog(f"  Sensors ({len(device.sensors)}):")
        for idx, name in enumerate(device.sensors.keys()):
            ml.slog(f"    [{idx}] {name}")
        ml.slog(f"  Controls ({len(device.controls)}):")
        for idx, name in enumerate(device.controls.keys()):
            ml.slog(f"    [{idx}] {name}")
    elif cmd == "HELP":
        ml.slog("Available commands:")
        ml.slog("  discover           - Discover devices")
        ml.slog("  autodiscovery      - Show autodiscovery status")
        ml.slog("  autodiscovery on   - Enable periodic discovery")
        ml.slog("  autodiscovery off  - Disable periodic discovery")
        ml.slog("  autodiscovery interval <seconds> - Set discovery interval")
        ml.slog("  list               - Show connected devices")
        ml.slog("  info <device>      - Show device details")
        ml.slog("  stream <dev> <hz>  - Start streaming")
        ml.slog("  stop <device>      - Stop streaming")
        ml.slog("  open <dev> <ctrl>  - Open valve/control")
        ml.slog("  close <dev> <ctrl> - Close valve/control")
        ml.slog("  status <device>    - Get device status / control states")
        ml.slog("  quit               - Exit")
    elif cmd == "ESTOP":
        devices = deviceTools.getRegisteredDevices(runtime)
        for device in devices.values():
            await deviceTools.emergencyStop(runtime, device)
        ml.slog("Emergency stop sent to all devices")


async def handleDeviceCommand(runtime: RuntimeServices, command: str, args: list) -> None:
    if not args:
        ml.slog(f"Usage: {command.lower()} <device_name> [args...]")
        return

    device_name = args[0]
    devices = deviceTools.getRegisteredDevices(runtime)
    device = None
    for d in devices.values():
        if d.name.lower() == device_name.lower():
            device = d
            break

    if not device:
        ml.slog(f"Device '{device_name}' not found. Use 'list' to see devices.")
        return

    cmd = command.upper()
    try:
        if cmd == "GETS":
            await deviceTools.getSingle(runtime, device)
            ml.slog(f"Requested data from {device.name}")
        elif cmd == "STREAM":
            if len(args) < 2:
                ml.slog("Usage: stream <device> <frequency_hz>")
                return
            freq = int(args[1])
            await deviceTools.startStreaming(runtime, device, freq)
            ml.slog(f"Streaming from {device.name} at {freq} Hz")
        elif cmd == "STOP":
            await deviceTools.stopStreaming(runtime, device)
            ml.slog(f"Stopped streaming from {device.name}")
        elif cmd == "CONTROL":
            if len(args) < 3:
                ml.slog("Usage: control <device> <name> <open|close>")
                return
            await deviceTools.setControl(runtime, device, args[1], args[2])
            ml.slog(f"Sent {args[2]} to {args[1]} on {device.name}")
        elif cmd == "OPEN":
            if len(args) < 2:
                ml.slog("Usage: open <device> <control_name>")
                return
            await deviceTools.setControl(runtime, device, args[1], "OPEN")
            ml.slog(f"Opened {args[1]} on {device.name}")
        elif cmd == "CLOSE":
            if len(args) < 2:
                ml.slog("Usage: close <device> <control_name>")
                return
            await deviceTools.setControl(runtime, device, args[1], "CLOSE")
            ml.slog(f"Closed {args[1]} on {device.name}")
        elif cmd == "STATUS":
            await deviceTools.getStatus(runtime, device)
            ml.slog(f"Requested status from {device.name}")
    except Exception as e:
        ml.elog(f"Error: {e}")


async def processCommand(runtime: RuntimeServices, command: str) -> None:
    """Process a command and send it to all connected devices."""
    fullCommand = command.strip()
    cmd = fullCommand.split(" ")[0]
    args = fullCommand.split(" ")[1:]

    if cmd.upper() in SERVERCOMMANDS:
        await handleServerCommand(runtime, cmd, args)
    elif cmd.upper() in DEVICECOMMANDS:
        await handleDeviceCommand(runtime, cmd, args)
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


async def commandProcessor(runtime: RuntimeServices) -> None:
    """Process commands from various input sources."""
    ml.slog("Started command processor daemon task.")

    try:
        async for command in cliReader():
            if command:
                await processCommand(runtime, command)
    except KeyboardInterrupt:
        ml.slog("Command processor stopped by user")
        raise
