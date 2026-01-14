# Import required modules
import os
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as redis

router = APIRouter()      # Create a router for log streaming

# Connect to Redis
async def get_redis_client():
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    return redis.Redis(
        host=REDIS_HOST,
        port=6379,
        db=0,
        username="server",
        password="propteambestteam",
        decode_responses=True,
    )

# Listen to Redis channels and forward messages to WebSocket
async def redis_listener(pubsub, websocket: WebSocket):
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    await websocket.send_text(json.dumps({"channel": message["channel"], "data": message["data"]}))
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
    print("WebSocket: client trying to connect")
    await websocket.accept()
    print("WebSocket: client accepted")

    try:
        r = await get_redis_client()
        pubsub = r.pubsub()
        await pubsub.subscribe("log", "errlog", "debuglog", "syslog")
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
            await r.close()
            print("WebSocket: connection closed")
    except Exception as e:
        print(f"WebSocket setup error: {e}") 
        await websocket.close()
