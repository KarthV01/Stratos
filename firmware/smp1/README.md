# SMP/1 firmware codec

`smp1.c` is a portable C99, allocation-free codec for Tier 2 and Tier 3
firmware. It has no RTOS, network-stack, or MQTT-library dependency. Add both
files to the firmware project and pass encoded payloads to the board's MQTT
client.

The application owns a 257-byte receive buffer: reserve the last byte for NUL,
then call `smp1_parse`. The parser modifies that buffer and returns field
pointers into it, so handle the frame before reusing the receive buffer.

```c
char payload[257];
char topic[128];
const char *hello[] = {"esp32-s3", "1.2.0", "2", "temp+relay"};

int length = smp1_encode(payload, sizeof payload, SMP1_HELLO, 1, 4, hello);
smp1_topic(topic, sizeof topic, "greenhouse-07", "up", "hello");
/* mqtt_publish(topic, payload, length, qos=1, retained=false); */
```

The MQTT connection, TLS, reconnect loop, subscriptions, Last Will, command
deduplication storage, and opcode allowlist remain platform responsibilities.
See `docs/mqtt-device-protocol.md` for those required behaviors.
