#include "smp1.h"

#include <assert.h>
#include <string.h>

int main(void) {
    char payload[257];
    char topic[128];
    smp1_frame_t frame;
    const char *hello[] = {"esp32-s3", "1.2.0", "2", "temp+relay"};
    const char *vehicle[] = {"aircraft-01", "1", "1", "px4", "quadrotor", "2"};
    const char *map[] = {
        "100", "401234567", "-731234567", "123000", "23000", "500",
        "9000", "3", "12", "84", "25"
    };
    int length = smp1_encode(
        payload, sizeof payload, SMP1_HELLO, 42u, 4u, hello
    );

    assert(length > 0);
    assert(strcmp(payload, "SMP1|H|42|esp32-s3|1.2.0|2|temp+relay") == 0);
    assert(smp1_parse(payload, (size_t)length, &frame) == 0);
    assert(frame.type == SMP1_HELLO);
    assert(frame.sequence == 42u);
    assert(frame.field_count == 4u);
    assert(strcmp(frame.fields[0], "esp32-s3") == 0);

    assert(smp1_topic(topic, sizeof topic, "sensor-1", "up", "hello") > 0);
    assert(strcmp(topic, "stratos/v1/devices/sensor-1/up/hello") == 0);
    assert(smp1_topic(topic, sizeof topic, "bad/device", "up", "hello") < 0);
    assert(smp1_topic(topic, sizeof topic, "sensor-1", "sideways", "hello") < 0);

    length = smp1_encode(payload, sizeof payload, SMP1_VEHICLE, 43u, 6u, vehicle);
    assert(length > 0);
    assert(smp1_parse(payload, (size_t)length, &frame) == 0);
    assert(frame.type == SMP1_VEHICLE && frame.field_count == 6u);
    assert(strcmp(frame.fields[0], "aircraft-01") == 0);

    length = smp1_encode(payload, sizeof payload, SMP1_MAP, 44u, 11u, map);
    assert(length > 0);
    assert(smp1_parse(payload, (size_t)length, &frame) == 0);
    assert(frame.type == SMP1_MAP && frame.field_count == 11u);
    assert(strcmp(frame.fields[2], "-731234567") == 0);

    strcpy(payload, "SMP1|H|1|a|b|2|base|extra");
    assert(smp1_parse(payload, strlen(payload), &frame) < 0);
    strcpy(payload, "SMP1|H|1|-bad|b|2|base");
    assert(smp1_parse(payload, strlen(payload), &frame) < 0);
    return 0;
}
