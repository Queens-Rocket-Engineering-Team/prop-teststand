import argparse
import asyncio

from libqretprop import server


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Start the QRET server.")

    return parser.parse_args()

def main() -> None:
    """Start the QRET server."""
    asyncio.run(server.main())

if __name__ == "__main__":

    print("Starting QRET server...")
    try:
        main()
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
