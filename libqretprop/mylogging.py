from datetime import datetime
from zoneinfo import ZoneInfo

import redis
import redis.exceptions


redisClient: redis.Redis | None = None

def initLogger(client: redis.Redis) -> None:
    """Initialize the Redis client for logging. Checks if Redis server is running."""
    global redisClient  # noqa: PLW0603
    try:
        client.ping()
        redisClient = client
    except redis.exceptions.ConnectionError as err:
        raise RuntimeError("Redis server is not running or cannot be reached.") from err

def _publishLog(channel: str, message: str, color: str) -> None:
    """Publish a time stamped log message to a specific Redis channel with a color."""
    if redisClient is None:
        raise ValueError("Logger not initialized. Call initLogger() first.")

    now = datetime.now(ZoneInfo("America/New_York"))
    # Format: YYYY-MM-DDTHH:MM:SS.s-TZ (1 decimal place for seconds)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    timestamp_str = f"\033[90m[{timestamp}]\033[0m"  # Always dark grey

    # Apply color formatting to the message only
    if color == "grey":
        message_str = f"\033[90m{message}\033[0m"  # Dark grey
    elif color == "red":
        message_str = f"\033[91m{message}\033[0m"  # Red
    elif color == "yellow":
        message_str = f"\033[93m{message}\033[0m"  # Light yellow
    else:
        message_str = message

    logString = f"{timestamp_str} {message_str}"

    redisClient.publish(channel, logString)

def log(message: str) -> None:
    """Log a message to the base redis log channel with a timestamp."""
    _publishLog("log", message, color="")

def slog(message: str) -> None:
    """Log a message to the redis system log channel."""
    _publishLog("syslog", message, color="grey")

def elog(message: str) -> None:
    """Log an error message to the redis error log channel."""
    _publishLog("errlog", message, color="red")

def dlog(message: str) -> None:
    """Log a debug message to the redis debug log channel."""
    _publishLog("debuglog", message, color="yellow")
