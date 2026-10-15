#ifndef STRATOS_SMP1_H
#define STRATOS_SMP1_H

#include <stddef.h>
#include <stdint.h>

#ifndef SMP1_MAX_FRAME_BYTES
#define SMP1_MAX_FRAME_BYTES 128u
#endif

#ifndef SMP1_MAX_FIELDS
#define SMP1_MAX_FIELDS 6u
#endif
#define SMP1_MAX_DEVICE_ID 64u

typedef enum {
    SMP1_HELLO = 'H',
    SMP1_TELEMETRY = 'T',
    SMP1_STATE = 'S',
    SMP1_ACK = 'A',
    SMP1_WELCOME = 'W',
    SMP1_COMMAND = 'C',
    SMP1_ERROR = 'E',
    SMP1_DRONE_SNAPSHOT = 'D',
    SMP1_MINIMAL_STATUS = 'M',
    SMP1_EVENT = 'V',
    SMP1_VEHICLE = 'B',
    SMP1_MAP = 'P',
    SMP1_GEO_SENSOR = 'G'
} smp1_type_t;

typedef struct {
    smp1_type_t type;
    uint32_t sequence;
    uint8_t field_count;
    char *fields[SMP1_MAX_FIELDS];
} smp1_frame_t;

/* Parses in place and replaces separators with NUL. payload[len] must be NUL. */
int smp1_parse(char *payload, size_t len, smp1_frame_t *frame);

int smp1_encode(
    char *output,
    size_t capacity,
    smp1_type_t type,
    uint32_t sequence,
    uint8_t field_count,
    const char *const fields[]
);

int smp1_topic(
    char *output,
    size_t capacity,
    const char *device_id,
    const char *direction,
    const char *channel
);

#endif
