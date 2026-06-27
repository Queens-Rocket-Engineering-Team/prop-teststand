import argparse
import asyncio

from libqretprop import server


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Start the QRET server.")

    parser.add_argument(
        "--no-discovery", "-nd",
        action="store_true",
        default=False,
        help="Disable device discovery. Use this if you want to disable SSDP discovery.",
    )

    return parser.parse_args()

def main() -> None:
    """Start the QRET server."""
    args = parse_args()
    asyncio.run(server.main(
        no_discovery=args.no_discovery,
    ))

if __name__ == "__main__":

    print("Starting QRET server...")
    try:
        main()
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
