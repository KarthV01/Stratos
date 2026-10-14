#include "stratos_mavlink.h"

#include <assert.h>
#include <limits.h>
#include <string.h>

static size_t serialize(mavlink_message_t *message, uint8_t *packet) {
    return (size_t)mavlink_msg_to_send_buffer(packet, message);
}

static size_t heartbeat_packet(uint8_t *packet, uint8_t system_id,
                               uint8_t autopilot, uint8_t mode) {
    mavlink_message_t message;
    mavlink_msg_heartbeat_pack(system_id, 1u, &message, MAV_TYPE_QUADROTOR,
                               autopilot, mode, 7u, MAV_STATE_ACTIVE);
    return serialize(&message, packet);
}

static int feed_packet(stratos_mavlink_t *state, const uint8_t *packet,
                       size_t length, uint32_t monotonic_ms) {
    size_t index;
    int accepted = 0;
    for (index = 0; index < length; ++index)
        accepted += stratos_mavlink_feed(state, packet[index], monotonic_ms);
    return accepted;
}

static void test_snapshot_binding_map_and_source_filter(void) {
    stratos_mavlink_t state;
    mavlink_message_t message;
    uint8_t packet[MAVLINK_MAX_PACKET_LEN];
    char payload[SMP1_MAX_FRAME_BYTES + 1u];
    smp1_frame_t frame;
    size_t length;

    assert(sizeof state <= 512u);
    assert(MAVLINK_COMM_NUM_BUFFERS == 1);
    assert(MAVLINK_MAX_SIGNING_STREAMS == 1);
    stratos_mavlink_init(&state, false);
    length = heartbeat_packet(packet, 17u, MAV_AUTOPILOT_PX4,
                              MAV_MODE_FLAG_SAFETY_ARMED);
    assert(feed_packet(&state, packet, length, 1000u) == 1);
    assert(state.initialized && state.system_id == 17u);
    assert(state.autopilot == MAV_AUTOPILOT_PX4);
    assert(state.mavlink_version == 2u);

    mavlink_msg_global_position_int_pack(
        17u, 1u, &message, 1234u, 401234567, -731234567,
        123450, 23450, 300, 400, 10, 9000u
    );
    length = serialize(&message, packet);
    assert(feed_packet(&state, packet, length, 1100u) == 1);
    assert(state.signed_latitude_e7 == 401234567);
    assert(state.longitude_e7 == -731234567);
    assert(state.ground_speed_cms == 500u);
    assert(state.heading_cdeg == 9000u);

    assert(stratos_mavlink_encode_binding(
        &state, "aircraft-17", 5u, payload, sizeof payload
    ) > 0);
    assert(smp1_parse(payload, strlen(payload), &frame) == 0);
    assert(frame.type == SMP1_VEHICLE && frame.field_count == 6u);
    assert(strcmp(frame.fields[0], "aircraft-17") == 0);
    assert(strcmp(frame.fields[3], "px4") == 0);

    assert(stratos_mavlink_encode_map(
        &state, 6u, 1100u, payload, sizeof payload
    ) > 0);
    assert(smp1_parse(payload, strlen(payload), &frame) == 0);
    assert(frame.type == SMP1_MAP && frame.field_count == 11u);
    assert(strcmp(frame.fields[2], "-731234567") == 0);
    assert(strcmp(frame.fields[5], "500") == 0);
    assert(strcmp(frame.fields[10], "41") == 0);
    assert(stratos_mavlink_encode_map(
        &state, 7u, 6101u, payload, sizeof payload
    ) > 0);
    assert(smp1_parse(payload, strlen(payload), &frame) == 0);
    assert(strcmp(frame.fields[10], "9") == 0);

    length = heartbeat_packet(packet, 18u, MAV_AUTOPILOT_ARDUPILOTMEGA, 0u);
    assert(feed_packet(&state, packet, length, 1200u) == 0);
    assert(state.system_id == 17u);
    assert(state.foreign_messages == 1u);
}

static void test_bad_crc_truncation_and_recovery(void) {
    stratos_mavlink_t state;
    uint8_t packet[MAVLINK_MAX_PACKET_LEN];
    size_t length;

    stratos_mavlink_init(&state, false);
    length = heartbeat_packet(packet, 23u, MAV_AUTOPILOT_ARDUPILOTMEGA, 0u);
    packet[length - 1u] ^= 0x80u;
    assert(feed_packet(&state, packet, length, 1000u) == 0);
    assert(!state.initialized);
    assert(state.parse_errors > 0u);

    length = heartbeat_packet(packet, 23u, MAV_AUTOPILOT_ARDUPILOTMEGA, 0u);
    assert(feed_packet(&state, packet, length, 1100u) == 1);
    assert(state.initialized && state.system_id == 23u);

    stratos_mavlink_init(&state, false);
    assert(feed_packet(&state, packet, length - 2u, 1200u) == 0);
    assert(!state.initialized);
    /* A UART flush/session reset discards an incomplete trailing frame. */
    stratos_mavlink_init(&state, false);
    assert(feed_packet(&state, packet, length, 1300u) == 1);
    assert(state.initialized);
}

static void test_unknown_and_mavlink1_policy(void) {
    stratos_mavlink_t state;
    mavlink_message_t message;
    uint8_t packet[MAVLINK_MAX_PACKET_LEN];
    size_t length;

    stratos_mavlink_init(&state, false);
    (void)heartbeat_packet(packet, 33u, MAV_AUTOPILOT_PX4, 0u);
    mavlink_msg_named_value_float_pack(33u, 1u, &message, 10u, "test", 1.0f);
    length = serialize(&message, packet);
    assert(feed_packet(&state, packet, length, 1000u) == 0);
    assert(state.unknown_messages == 1u);

    mavlink_get_channel_status(MAVLINK_COMM_0)->flags |=
        MAVLINK_STATUS_FLAG_OUT_MAVLINK1;
    length = heartbeat_packet(packet, 34u, MAV_AUTOPILOT_PX4, 0u);
    assert(packet[0] == MAVLINK_STX_MAVLINK1);
    mavlink_get_channel_status(MAVLINK_COMM_0)->flags &=
        (uint8_t)~MAVLINK_STATUS_FLAG_OUT_MAVLINK1;

    stratos_mavlink_init(&state, false);
    assert(feed_packet(&state, packet, length, 1000u) == 0);
    assert(state.unsupported_version_messages == 1u);
    assert(!state.initialized);

    stratos_mavlink_init(&state, true);
    assert(feed_packet(&state, packet, length, 1000u) == 1);
    assert(state.initialized && state.mavlink_version == 1u);
}

static void test_signing_key_requires_verified_signed_packets(void) {
    stratos_mavlink_t receiver;
    mavlink_message_t message;
    mavlink_signing_t sender_signing;
    uint8_t key[32];
    uint8_t packet[MAVLINK_MAX_PACKET_LEN];
    size_t length;
    size_t index;

    memset(key, 0x5au, sizeof key);
    memset(&sender_signing, 0, sizeof sender_signing);
    memcpy(sender_signing.secret_key, key, sizeof key);
    sender_signing.flags = MAVLINK_SIGNING_FLAG_SIGN_OUTGOING;
    sender_signing.link_id = 1u;
    sender_signing.timestamp = 1000000u;
    mavlink_get_channel_status(MAVLINK_COMM_0)->signing = &sender_signing;
    mavlink_msg_heartbeat_pack(44u, 1u, &message, MAV_TYPE_QUADROTOR,
                               MAV_AUTOPILOT_PX4, 0u, 1u, MAV_STATE_ACTIVE);
    length = serialize(&message, packet);
    mavlink_get_channel_status(MAVLINK_COMM_0)->signing = NULL;
    assert((message.incompat_flags & MAVLINK_IFLAG_SIGNED) != 0u);

    stratos_mavlink_init(&receiver, false);
    stratos_mavlink_set_signing_key(&receiver, key, 1000000u);
    assert(feed_packet(&receiver, packet, length, 2000u) == 1);
    assert(receiver.initialized && receiver.system_id == 44u);

    length = heartbeat_packet(packet, 44u, MAV_AUTOPILOT_PX4, 0u);
    for (index = 0u; index < length; ++index)
        (void)stratos_mavlink_feed(&receiver, packet[index], 2000u);
    assert(receiver.received_messages == 1u);
    assert(receiver.parse_errors > 0u);
}

int main(void) {
    test_snapshot_binding_map_and_source_filter();
    test_bad_crc_truncation_and_recovery();
    test_unknown_and_mavlink1_policy();
    test_signing_key_requires_verified_signed_packets();
    return 0;
}
