import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TASK_STREAM = "tasks"
EVENT_STREAM = "task-events"
WORKER_GROUP = "workers"
TASK_INDEX = "task-index"

