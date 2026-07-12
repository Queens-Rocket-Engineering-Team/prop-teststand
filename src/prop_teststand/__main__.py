import asyncio

from prop_teststand import server


def main() -> None:
    """Start the QRET server."""
    print("Starting QRET server...")
    try:
        asyncio.run(server.main())
    except KeyboardInterrupt:
        print("\nServer stopped by user.")


if __name__ == "__main__":
    main()
