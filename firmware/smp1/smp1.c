#include "smp1.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int valid_token(const char *value) {
    size_t length = 0;
    int decimal_points = 0;
    if (value == NULL || value[0] == '\0') return 0;
    if (value[0] == '-') {
        if (value[1] == '\0') return 1;
        if (value[1] < '0' || value[1] > '9') return 0;
        length = 1;
        while (value[length] != '\0') {
            char c = value[length];
            if (c == '.') {
                if (++decimal_points > 1 || value[length + 1] == '\0') return 0;
            } else if (c < '0' || c > '9') {
                return 0;
            }
            if (++length > 64u) return 0;
        }
        return 1;
    }
    if (!((value[0] >= 'A' && value[0] <= 'Z') ||
          (value[0] >= 'a' && value[0] <= 'z') ||
          (value[0] >= '0' && value[0] <= '9'))) return 0;
    while (value[length] != '\0') {
        char c = value[length];
        int valid = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                    (c >= '0' && c <= '9') || strchr("._:/+-", c) != NULL;
        if (!valid || ++length > 64u) return 0;
    }
    return 1;
}

static int expected_fields(smp1_type_t type) {
    switch (type) {
        case SMP1_HELLO: return 4;
        case SMP1_TELEMETRY: return 3;
        case SMP1_STATE: return 2;
        case SMP1_ACK: return 3;
        case SMP1_WELCOME: return 2;
        case SMP1_COMMAND: return 3;
        case SMP1_ERROR: return 2;
        case SMP1_DRONE_SNAPSHOT: return 11;
        case SMP1_MINIMAL_STATUS: return 3;
        case SMP1_EVENT: return 3;
        case SMP1_VEHICLE: return 6;
        case SMP1_MAP: return 12;
        default: return -1;
    }
}

int smp1_parse(char *payload, size_t len, smp1_frame_t *frame) {
    char *cursor;
    char *end;
    unsigned long sequence;
    uint8_t count = 0;
    if (payload == NULL || frame == NULL || len < 8u ||
        len > SMP1_MAX_FRAME_BYTES || payload[len] != '\0') return -1;
    if (strncmp(payload, "SMP1|", 5u) != 0 || payload[5] == '\0' ||
        payload[6] != '|') return -2;
    frame->type = (smp1_type_t)payload[5];
    if (expected_fields(frame->type) < 0) return -2;
    cursor = payload + 7;
    sequence = strtoul(cursor, &end, 10);
    if (end == cursor || *end != '|' || sequence > UINT32_MAX) return -3;
    frame->sequence = (uint32_t)sequence;
    cursor = end + 1;
    while (count < SMP1_MAX_FIELDS) {
        end = strchr(cursor, '|');
        if (end != NULL) *end = '\0';
        if (!valid_token(cursor)) return -4;
        frame->fields[count++] = cursor;
        if (end == NULL) break;
        cursor = end + 1;
    }
    if (end != NULL || count != (uint8_t)expected_fields(frame->type))
        return -5;
    frame->field_count = count;
    return 0;
}

int smp1_encode(char *output, size_t capacity, smp1_type_t type,
                uint32_t sequence, uint8_t field_count,
                const char *const fields[]) {
    int written;
    size_t used;
    uint8_t index;
    if (output == NULL || fields == NULL || capacity == 0u ||
        field_count != (uint8_t)expected_fields(type)) return -1;
    written = snprintf(output, capacity, "SMP1|%c|%lu", (char)type,
                       (unsigned long)sequence);
    if (written < 0 || (size_t)written >= capacity) return -2;
    used = (size_t)written;
    for (index = 0; index < field_count; ++index) {
        if (!valid_token(fields[index])) return -3;
        written = snprintf(output + used, capacity - used, "|%s", fields[index]);
        if (written < 0 || (size_t)written >= capacity - used) return -2;
        used += (size_t)written;
    }
    if (used > SMP1_MAX_FRAME_BYTES) return -2;
    return (int)used;
}

int smp1_topic(char *output, size_t capacity, const char *device_id,
               const char *direction, const char *channel) {
    int written;
    if (!valid_token(device_id) || strchr(device_id, '/') != NULL ||
        (strcmp(direction, "up") != 0 && strcmp(direction, "down") != 0) ||
        !valid_token(channel) || strchr(channel, '/') != NULL) return -1;
    written = snprintf(output, capacity, "stratos/v1/devices/%s/%s/%s",
                       device_id, direction, channel);
    if (written < 0 || (size_t)written >= capacity) return -2;
    return written;
}
