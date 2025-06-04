import selectors
import sys
from typing import TYPE_CHECKING

from libqretprop.ESPObjects.DeviceSearcher import DeviceSearcher


if TYPE_CHECKING:
    import socket

    from libqretprop.ESPObjects.ESPDevice.ESPDevice import ESPDevice


def stopSearch(searcher: DeviceSearcher) -> None:
    print("Stopping listener...")
    searcher.stopListening()
    print(f"Found devices: {searcher.deviceList}")

def handleSsdpResponses(deviceSearcher: DeviceSearcher) -> None:
    """Check for SSDP responses and process any new device."""
    try:
        device: ESPDevice | None = deviceSearcher.handleDeviceCallback()
        if device:
            print(f"Discovered device: {device.name}")
    except BlockingIOError:
        # No data available
        pass

def main() -> None:
    selector = selectors.DefaultSelector()

    # Instantiate DeviceSearcher and set socket to non-blocking
    deviceSearcher = DeviceSearcher()
    ssdpSocket: socket.socket = deviceSearcher.SSDPSock
    ssdpSocket.setblocking(False)
    selector.register(ssdpSocket, selectors.EVENT_READ, data="ssdp")

    # Register stdin for user input (keypress)
    stdinFd = sys.stdin
    selector.register(stdinFd, selectors.EVENT_READ, data="stdin")

    # Send initial discovery packet
    deviceSearcher.sendDiscovery()
    print("Sent initial SSDP discovery. Press 's' + Enter to resend, Ctrl+C to quit.")

    try:
        while True:
            for key, _ in selector.select(timeout=1.0):
                if key.data == "ssdp":
                    handleSsdpResponses(deviceSearcher)
                elif key.data == "stdin":
                    userInput = sys.stdin.readline().strip()
                    if userInput.lower() == "s":
                        deviceSearcher.sendDiscovery()
                        print("Resent SSDP discovery.")
    except KeyboardInterrupt:
        print("\nStopping device search.")
    finally:
        selector.unregister(ssdpSocket)
        selector.unregister(stdinFd)
        deviceSearcher.stopListening()


if __name__ == "__main__":
    main()
