import asyncio
from typing import Any


async def handle_task(payload: dict[str, Any], worker_id: str) -> dict[str, Any]:
    """Replace this function with an agent invocation or other real work."""
    delay = max(0.0, min(float(payload.get("delay_seconds", 2)), 30.0))
    await asyncio.sleep(delay)
    return {
        "reply": f"{worker_id} processed: {payload['message']}",
        "worker": worker_id,
    }

