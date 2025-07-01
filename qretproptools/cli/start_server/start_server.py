import argparse
import asyncio

from libqretprop import server


def parseArgs() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Start the QRET server.")

    parser.add_argument(
        "--direct", "-d",
        type=str,
        help="Direct TCP connection to a device. Specify as <IP> to connect directly to a device without discovery.",
    )

    parser.add_argument(
        "--no-discovery", "-nd",
        action="store_true",
        default=False,
        help="Disable device discovery. Use this if you want to disable SSDP discovery and only connect to devices via direct TCP.",
    )

    return parser.parse_args()

def main() -> None:
    """Start the QRET server."""
    args = parseArgs()
    asyncio.run(server.main(
        directIP=args.direct if args.direct else None,
        noDiscovery=args.no_discovery
    ))

if __name__ == "__main__":

    print("Starting QRET server...")
    try:
        main()
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
