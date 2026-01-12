"""Mock device CLI entry point."""

import sys
import os

# Add parent directory to path so we can import mock_device
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from mock_device import main as mock_main
import asyncio


def main():
    """CLI entry point for mock device."""
    try:
        asyncio.run(mock_main())
    except KeyboardInterrupt:
        print("\n\nStopped by user")


if __name__ == "__main__":
    main()

