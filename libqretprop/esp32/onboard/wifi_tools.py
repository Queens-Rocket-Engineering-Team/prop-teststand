import time  # noqa: INP001 # This is all micropython code to be executed on the esp32 system level

import network  # type:ignore # This is a micropython library


class WiFiTimeoutError(Exception):
    """Exception raised for if the wifi connection times out."""
    def __init__(self) -> None:
        super().__init__("Wi-Fi connection timed out")

def connectWifi(ssid: str, password: str) -> network.WLAN:
    # Create a WLAN station object
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # Check if already connected
    if wlan.isconnected():
        print("Already connected to:", wlan.ifconfig())
        return wlan

    # Connect to the Wi-Fi network
    print(f"Connecting to {ssid}...")
    wlan.connect(ssid, password)

    # Wait for connection
    timeout = 30  # Timeout in seconds
    while not wlan.isconnected() and timeout > 0:
        print("Trying to connect...")
        time.sleep(1)
        timeout -= 1

    if timeout == 0:
        raise WiFiTimeoutError

    # Check if connected
    if wlan.isconnected():
        print(f"Connected successfully to {ssid}!")
        print("Network config:", wlan.ifconfig())
    else:
        print("Failed to connect to Wi-Fi")

    return wlan

def disconnectWifi(wlan: network.WLAN) -> None:
    wlan.disconnect()
    wlan.active(False)
    print("Disconnected from Wi-Fi network: ", wlan.config("essid")) # This will print the name of the network that was disconnected from
