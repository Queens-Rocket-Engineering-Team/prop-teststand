import time

import keyboard

from libqretprop.esp32interface.DeviceSearcher import DeviceSearcher


def stopSearch(searcher: DeviceSearcher) -> None:
    print("Stopping listener...")
    searcher.stopListening()
    print(f"Found devices: {searcher.deviceList}")


def main() -> None:
    searcher = DeviceSearcher()
    try:
        searcher.searchForDevices()
        while True:
            time.sleep(0.1)  # Keep the main thread alive
            if keyboard.is_pressed("s"):
                print("Sending another broadcast message...")
                searcher.sendBroadcastMessage("SEARCH")

    except KeyboardInterrupt:
        stopSearch(searcher)

if __name__ == "main":
    main()
