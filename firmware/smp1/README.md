# SMP/1 firmware codec

`smp1.c` is a portable C99, allocation-free codec for Tier 2 and Tier 3
firmware. It has no RTOS, network-stack, or MQTT-library dependency. Add both
files to the firmware project and pass encoded payloads to the board's MQTT
client.

The default build is the Tier 3 profile: six field pointers and 128-byte
frames. Tier 2 firmware enables the twelve-field map snapshot and 256-byte
ceiling without changing source:

```sh
-DSMP1_MAX_FIELDS=12 -DSMP1_MAX_FRAME_BYTES=256
```

Compile `tests/c/test_smp1.c` with the defaults to verify the Tier 3 profile.
Compile `tests/c/test_smp1_tier2.c` with the two definitions above to verify the
extended Tier 2 snapshot.

The application owns a `SMP1_MAX_FRAME_BYTES + 1` receive buffer: reserve the last byte for NUL,
then call `smp1_parse`. The parser modifies that buffer and returns field
pointers into it, so handle the frame before reusing the receive buffer.

The default profile supports the geospatial sensor frame
`SMP1|G|seq|sensor|value|unit|lat_e7|lon_e7|altitude_mm_or_na`. Use it when a
reading has its own GPS or provisioned location. Values stay compact ASCII;
the Stratos gateway produces the normalized JSON and GeoJSON views.

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
