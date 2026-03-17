from queue import SimpleQueue
from threading import Thread

import redis
import redis.exceptions


redisClient: redis.Redis | None = None
_publishQueue: SimpleQueue[tuple[str, str, str]] = SimpleQueue()  # (channel, message, color)


def _publishWorker() -> None:
    """Background thread: applies color and publishes log messages to Redis (non-blocking for callers)."""
    while True:
        channel, message, color = _publishQueue.get()
        if redisClient is not None:
            # Apply ANSI color codes
            if color == "grey":
                colored_msg = f"\033[90m{message}\033[0m"
            elif color == "red":
                colored_msg = f"\033[91m{message}\033[0m"
            elif color == "yellow":
                colored_msg = f"\033[93m{message}\033[0m"
            else:
                colored_msg = message
            try:
                redisClient.publish(channel, colored_msg)
            except Exception:
                pass


_publishThread = Thread(target=_publishWorker, daemon=True)
_publishThread.start()


def initLogger(client: redis.Redis) -> None:
    """Initialize the Redis client for logging. Checks if Redis server is running."""
    global redisClient  # noqa: PLW0603
    try:
        client.ping()
        redisClient = client
    except redis.exceptions.ConnectionError as err:
        raise RuntimeError("Redis server is not running or cannot be reached.") from err

def _publishLog(channel: str, message: str, color: str = "") -> None:
    """Enqueue a log message for background publishing with optional ANSI color (non-blocking)."""
    if redisClient is None:
        raise ValueError("Logger not initialized. Call initLogger() first.")
    _publishQueue.put((channel, message, color))

def log(message: str) -> None:
    """Log a message to the base redis log channel."""
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

def plog(message: str) -> None:
    """Log a packet info message to the redis packet log channel."""
    _publishLog("packetlog", message, color="grey")
