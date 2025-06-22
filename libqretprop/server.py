import asyncio
import time

import redis

import libqretprop.mylogging as ml
from libqretprop.DeviceControllers.DeviceSearcher import DeviceSearcher


def main() -> None:

    # Initialize Redis client for logging
    redisClient = redis.Redis(host="localhost", port=6379, db=0)
    ml.initLogger(redisClient)


    searcher = DeviceSearcher()

    searcher.directDiscovery("192.168.1.226")

    while True:
        try:
            searcher.directDiscovery("192.168.1.226")
        except KeyboardInterrupt:
            ml.slog("Stopping device search.")
            break
        except Exception as e:
            ml.elog(f"Error during discovery: {e}")
            break
        time.sleep(1)

if __name__ == "__main__":
    main()
