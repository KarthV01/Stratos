import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TLS = os.getenv("MQTT_TLS", "false").lower() in {"1", "true", "yes"}
TASK_STREAM = "tasks"
EVENT_STREAM = "task-events"
WORKER_GROUP = "workers"
TASK_INDEX = "task-index"
DEVICE_INDEX = "device-index"
DEVICE_EVENT_STREAM = "device-events"
DEVICE_TELEMETRY_STREAM = "device-telemetry"
DEVICE_COMMAND_STREAM = "device-commands"
DEVICE_COMMAND_INDEX = "device-command-index"
MQTT_BRIDGE_GROUP = "mqtt-bridges"
