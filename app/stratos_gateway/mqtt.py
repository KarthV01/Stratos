"""MQTT adapter exported from the Stratos gateway package."""

import asyncio

from app.mqtt_bridge import DeviceMessageProcessor, MqttBridge, main

__all__ = ["DeviceMessageProcessor", "MqttBridge", "main"]


if __name__ == "__main__":
    asyncio.run(main())
