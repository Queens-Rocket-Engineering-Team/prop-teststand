from libqretprop import server


def main() -> None:
    """Start the QRET server."""
    server.main()

if __name__ == "__main__":

    print("Starting QRET server...")
    try:
        main()
    except KeyboardInterrupt:
        print("\nServer stopped by user.")