# Import required modules
import asyncio
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import redis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter()      # Create a router for log streaming

# Connect to Redis
redisClient: redis.Redis | None = None

def initWSLogger(client: redis.Redis) -> None:
    global redisClient
    try:
        client.ping()
        redisClient = client
    except redis.exceptions.ConnectionError as err:
        raise RuntimeError("Redis server is not running or cannot be reached.") from err


# Listen to Redis channels and forward messages to WebSocket
async def redis_listener(pubsub, websocket: WebSocket):
    try:
        for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    now = datetime.now(ZoneInfo("America/New_York"))
                    timestamp = now.strftime("%H:%M:%S")
                    await websocket.send_text(json.dumps({"channel": message["channel"], "data": message["data"], "timestamp_ws": timestamp}))
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    print(f"Error sending message to WebSocket: {e}")
                    raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"Redis listener error: {e}")
        raise

# WebSocket endpoint for log streaming
@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    global redisClient

    if redisClient is None:
        raise ValueError("WS Logger not initialized. Call initWSLogger() first.")

    print("WebSocket: client trying to connect")
    await websocket.accept()
    print("WebSocket: client accepted")

    try:
        pubsub = redisClient.pubsub()
        print(pubsub)
        pubsub.subscribe("log", "errlog", "debuglog", "syslog")
        print("Subscribed to Redis log channels")

        listener_task = asyncio.create_task(redis_listener(pubsub, websocket))

        try:
            while True:
                await websocket.receive_text()  # Keep the connection alive
        except WebSocketDisconnect:
            print("WebSocket: client disconnected")
        except Exception as e:
            print(f"WebSocket: error occurred - {e}")
        finally:
            listener_task.cancel()
            await pubsub.unsubscribe("log", "errlog", "debuglog", "syslog")
            await pubsub.close()
            await redisClient.close()
            print("WebSocket: connection closed")
    except Exception as e:
        print(f"WebSocket setup error: {e}")
        await websocket.close()
