# Import required modules
import asyncio
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import redis.asyncio as redis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import libqretprop.mylogging as ml
import libqretprop.configManager as config


router = APIRouter()      # Create a router for log streaming

# Connect to Redis
async def get_redis_client():
    return redis.Redis(
        host=config.serverConfig["services"]["redis"]["ip"],
        port=config.serverConfig["services"]["redis"]["port"],
        db=0,
        username=config.serverConfig["accounts"]["redis"]["username"],
        password=config.serverConfig["accounts"]["redis"]["password"],
        decode_responses=True,
    )

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

def strip_ansi(s: str) -> str:
    return ANSI_ESCAPE.sub('', s)

# Listen to Redis channels and forward messages to WebSocket
async def redis_listener(pubsub, websocket: WebSocket):
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    now = datetime.now(ZoneInfo("America/New_York"))
                    timestamp = now.strftime("%H:%M:%S")
                    clean_message_data = strip_ansi(message["data"])
                    clean_message_channel = strip_ansi(message["channel"])

                    await websocket.send_text(json.dumps({"channel": clean_message_channel, "data": clean_message_data, "timestamp_ws": timestamp}))
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    ml.elog(f"Error sending message to WebSocket: {e}")
                    raise
    except asyncio.CancelledError:
        raise
    except Exception as e:
        ml.elog(f"Redis listener error: {e}")
        raise

# WebSocket endpoint for log streaming
@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    ml.dlog("WebSocket: client trying to connect")
    await websocket.accept()
    ml.dlog("WebSocket: client accepted")

    try:
        r = await get_redis_client()
        pubsub = r.pubsub()
        await pubsub.subscribe("log", "errlog", "debuglog", "syslog")
        ml.dlog("WebSocket: Subscribed to Redis log channels")

        listener_task = asyncio.create_task(redis_listener(pubsub, websocket))

        try:
            while True:
                await websocket.receive_text()  # Keep the connection alive
        except WebSocketDisconnect:
            ml.dlog("WebSocket: client disconnected")
        except Exception as e:
            ml.elog(f"WebSocket: error occurred - {e}")
        finally:
            listener_task.cancel()
            await pubsub.unsubscribe("log", "errlog", "debuglog", "syslog")
            await pubsub.close()
            await r.close()
            ml.dlog("WebSocket: connection closed")
    except Exception as e:
        ml.elog(f"WebSocket setup error: {e}")
        await websocket.close()
