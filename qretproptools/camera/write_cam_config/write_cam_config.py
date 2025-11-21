import os
from libqretprop.DeviceControllers.cameraConfig import cameraConfig

def main() -> None:
    print("Setting up camera configuration")

    fileData = "paths:\n"

    for camera in cameraConfig:
        ip = camera[0]
        fileData += f"  {ip}:\n    sourceOnDemand: true\n    source: rtsp://propcam:propteambestteam@{ip}/stream1\n"

    # Create media-config directory if it doesn't exist
    # Needed to avoid putting the entire /app directory into a docker volume
    os.makedirs("media-config", exist_ok=True)

    with open("media-config/mediamtx.yml", "w") as f:
        f.write(fileData)
        print("Wrote camera configuration to mediamtx.yml")
        f.close()

if __name__ == "__main__":
    main()
