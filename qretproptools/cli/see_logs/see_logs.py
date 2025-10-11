import argparse
import time

import redis
import redis.exceptions

import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Tap into QRET logs via Redis.")
    parser.add_argument("-e", "--elog", action="store_true", help="Show error logs")
    parser.add_argument("-d", "--debug", action="store_true", help="Show debug logs")
    parser.add_argument("-s", "--slog", action="store_true", help="Show standard logs")
    args = parser.parse_args()

    channels = []
    channels.append("log")  # Always include the base log channel
    if args.elog:   channels.append("errlog")
    if args.debug:  channels.append("debuglog")
    if args.slog:   channels.append("syslog")

    print(f"Listening to channels: {', '.join(channels)}")

    try:
        REDIS_HOST = os.getenv("REDIS_HOST", "localhost")

        while True:
            r = None
            pubsub = None

            try:

                r = redis.Redis(host=REDIS_HOST,
                              port=6379,
                              db=0,
                              username="server",
                              password="propteambestteam",
                              decode_responses=True,
                              )
                pubsub = r.pubsub()
                pubsub.subscribe(*channels)

                for message in pubsub.listen():
                    if message["type"] == "message":
                        print(f"[{message['channel']}] {message['data']}")

            except (redis.exceptions.ConnectionError, ConnectionRefusedError):
                time.sleep(1)
            except Exception as e:
                print("Lost connection to server. Waiting for server...")
                print("Error details:", e)
            finally:
                if pubsub is not None:
                    pubsub.close()
                if r is not None:
                    r.close()

    except KeyboardInterrupt:
        print("\nExiting log listener.")

if __name__ == "__main__":
    main()
