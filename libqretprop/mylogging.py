import contextlib
import time
from queue import Empty, Full, Queue
from threading import Thread

import redis
import redis.exceptions


redisClient: redis.Redis | None = None

# Max amount of log messages to buffer in memory for batch publishing before dropping oldest entries
# Safety measured to ensure we have the newest data under extreme log volume
_MAX_LOG_QUEUE_SIZE = 50000
_publishQueue: Queue[tuple[str, str, str]] = Queue(maxsize=_MAX_LOG_QUEUE_SIZE)  # (channel, message, color)

_PIPELINE_BATCH_SIZE = 256 # Max number of log messages to publish in one Redis pipeline transaction
_FRESHNESS_INTERVAL = 0.5 # Minimum interval (in seconds) between updates to the "sensors:last_publish" freshness timestamp in Redis
_last_freshness_update: float = 0.0


def _applyColor(message: str, color: str) -> str:
    if color == "grey":
        return f"\033[90m{message}\033[0m"
    if color == "red":
        return f"\033[91m{message}\033[0m"
    if color == "yellow":
        return f"\033[93m{message}\033[0m"
    return message


def _publishWorker() -> None:
    """Background thread: batches and publishes log messages to Redis (non-blocking for callers)."""
    global _last_freshness_update
    while True:
        first = _publishQueue.get()

        if redisClient is None:
            continue

        batch = [first]
        while len(batch) < _PIPELINE_BATCH_SIZE:
            try:
                batch.append(_publishQueue.get_nowait())
            except Empty:
                break

        try:
            now = time.monotonic()

            # Update freshness timestamp if any log messages are published for observability
            # We limit the update frequency to avoid excessive Redis writes under high log volume
            has_sensor_data = any(channel == "log" for channel, _, _ in batch)
            update_freshness = has_sensor_data and (now - _last_freshness_update >= _FRESHNESS_INTERVAL)

            pipe = redisClient.pipeline(transaction=False)
            for channel, message, color in batch:
                pipe.publish(channel, _applyColor(message, color))

            # Update last_publish timestamp for observability
            if update_freshness:
                pipe.set("sensors:last_publish", time.time())

            pipe.execute()

            if update_freshness:
                _last_freshness_update = now

        except Exception as e:
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
    item = (channel, message, color)
    try:
        _publishQueue.put_nowait(item)
    except Full:
        # Prefer newest logs under overload: evict one oldest entry, then enqueue latest.
        with contextlib.suppress(Empty):
            _publishQueue.get_nowait()
        with contextlib.suppress(Full):
            _publishQueue.put_nowait(item)


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