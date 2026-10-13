#include "smp1.h"

#include <assert.h>
#include <string.h>

int main(void) {
    char payload[257];
    smp1_frame_t frame;
    const char *snapshot[] = {
        "88000", "407128001", "-740060002", "42500", "120", "-30",
        "5", "9120", "22400", "76", "3"
    };
    int length = smp1_encode(
        payload, sizeof payload, SMP1_DRONE_SNAPSHOT, 44u, 11u, snapshot
    );

    assert(length > 0);
    assert(length < 128);
    assert(smp1_parse(payload, (size_t)length, &frame) == 0);
    assert(frame.type == SMP1_DRONE_SNAPSHOT);
    assert(frame.field_count == 11u);
    assert(strcmp(frame.fields[2], "-740060002") == 0);
    return 0;
}
