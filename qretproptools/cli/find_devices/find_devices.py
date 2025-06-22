import selectors
import sys
from typing import TYPE_CHECKING

from libqretprop.DeviceControllers.searchTools import DeviceSearcher


if TYPE_CHECKING:
    import socket

    from libqretprop.Devices.ESPDevice import ESPDevice




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
    deviceSearcher.sendMulticastDiscovery()
    print("Sent initial SSDP discovery. Press 's' + Enter to resend, Ctrl+C to quit.")

    try:
        while True:
            for key, _ in selector.select(timeout=1.0):
                if key.data == "ssdp":
                    pass
                elif key.data == "stdin":
                    userInput = sys.stdin.readline().strip()
                    if userInput.lower() == "s":
                        deviceSearcher.sendMulticastDiscovery()
                        print("Resent SSDP discovery.")
    except KeyboardInterrupt:
        print("\nStopping device search.")
    finally:
        selector.unregister(ssdpSocket)
        selector.unregister(stdinFd)
        deviceSearcher.closeSocket()


if __name__ == "__main__":
    main()
