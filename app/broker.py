from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.config import REDIS_URL, TASK_STREAM, WORKER_GROUP


def redis_client() -> Redis:
    return Redis.from_url(REDIS_URL, decode_responses=True)


async def ensure_worker_group(redis: Redis) -> None:
    """Create the worker group once; MKSTREAM also creates an empty task stream."""
    try:
        await redis.xgroup_create(TASK_STREAM, WORKER_GROUP, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

