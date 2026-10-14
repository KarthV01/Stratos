#ifndef STRATOS_MAVLINK_H
#define STRATOS_MAVLINK_H

/* A single receive channel keeps the generated parser suitable for small MCUs. */
#ifndef MAVLINK_COMM_NUM_BUFFERS
#define MAVLINK_COMM_NUM_BUFFERS 1
#endif
#if MAVLINK_COMM_NUM_BUFFERS != 1
#error "The Stratos embedded adapter uses one MAVLink UART/channel"
#endif
#ifndef MAVLINK_MAX_SIGNING_STREAMS
#define MAVLINK_MAX_SIGNING_STREAMS 1
#endif
#ifndef MAVLINK_HELPER
#define MAVLINK_HELPER static inline
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "common/mavlink.h"

#include "../smp1/smp1.h"

#define STRATOS_MAVLINK_VALID_POSITION 0x0001u
#define STRATOS_MAVLINK_VALID_GPS 0x0002u
#define STRATOS_MAVLINK_VALID_BATTERY 0x0004u
#define STRATOS_MAVLINK_VALID_ATTITUDE 0x0008u
#define STRATOS_MAVLINK_VALID_LOCAL_POSITION 0x0010u
#define STRATOS_MAVLINK_VALID_SYSTEM_TIME 0x0020u
#define STRATOS_MAVLINK_VALID_HOME 0x0040u
#define STRATOS_MAVLINK_VALID_MISSION 0x0080u
#define STRATOS_MAVLINK_VALID_GROUND_SPEED 0x0100u
#define STRATOS_MAVLINK_VALID_RELATIVE_ALTITUDE 0x0200u

typedef struct {
    uint8_t initialized;
    uint8_t allow_mavlink1;
    uint8_t signing_enabled;
    uint8_t system_id;
    uint8_t component_id;
    uint8_t autopilot;
    uint8_t vehicle_type;
    uint8_t mavlink_version;
    uint8_t base_mode;
    uint8_t system_status;
    uint8_t landed_state;
    uint8_t vtol_state;
    uint8_t gps_fix_type;
    uint8_t satellites_visible;
    uint8_t battery_remaining;
    uint8_t battery_id;
    uint8_t has_battery_id;
    uint8_t has_battery_current;
    uint8_t has_battery_voltage;
    uint8_t has_battery_remaining;
    uint8_t mission_state;
    uint8_t mission_mode;
    uint8_t has_mission_state;
    uint8_t status_severity;
    uint8_t has_status_text;
    uint8_t has_last_packet_sequence;
    uint8_t last_packet_sequence;
    uint16_t valid;
    uint16_t gps_eph_centi;
    uint16_t gps_epv_centi;
    uint16_t mission_sequence;
    uint16_t mission_total;
    uint16_t sensor_load_decipct;
    uint16_t communication_drop_centipct;
    uint32_t battery_voltage_mv;
    int16_t battery_current_ca;
    uint32_t custom_mode;
    uint32_t boot_ms;
    int32_t signed_latitude_e7;
    int32_t longitude_e7;
    int32_t altitude_msl_mm;
    int32_t relative_altitude_mm;
    int32_t velocity_north_cms;
    int32_t velocity_east_cms;
    int32_t velocity_down_cms;
    uint32_t ground_speed_cms;
    uint16_t heading_cdeg;
    uint8_t has_heading;
    uint8_t reserved2;
    uint64_t unix_time_us;
    int32_t home_latitude_e7;
    int32_t home_longitude_e7;
    int32_t home_altitude_mm;
    uint64_t capabilities;
    uint64_t hardware_uid;
    uint32_t flight_sw_version;
    float roll_rad;
    float pitch_rad;
    float yaw_rad;
    float roll_speed_rad_s;
    float pitch_speed_rad_s;
    float yaw_speed_rad_s;
    float local_north_m;
    float local_east_m;
    float local_down_m;
    float local_velocity_north_m_s;
    float local_velocity_east_m_s;
    float local_velocity_down_m_s;
    uint32_t received_messages;
    uint32_t dropped_messages;
    uint32_t parse_errors;
    uint32_t unknown_messages;
    uint32_t foreign_messages;
    uint32_t unbound_messages;
    uint32_t unsupported_version_messages;
    uint16_t failure_flags;
    char last_status_text[51];
    mavlink_signing_t signing;
    mavlink_signing_streams_t signing_streams;
} stratos_mavlink_t;

/* Initialize one receive-only parser. MAVLink 1 is disabled unless requested. */
void stratos_mavlink_init(stratos_mavlink_t *state, bool mavlink1_compatibility);

/* Provisioning a key makes signed MAVLink 2 mandatory on this parser channel. */
void stratos_mavlink_set_signing_key(
    stratos_mavlink_t *state, const uint8_t key[32], uint64_t timestamp
);

/* Feed exactly one UART byte. Returns 1 for a normalized supported message. */
int stratos_mavlink_feed(stratos_mavlink_t *state, uint8_t byte);

/* Encode the already-provisioned identity for SMP/1 up/state. */
int stratos_mavlink_encode_binding(
    const stratos_mavlink_t *state, const char *vehicle_id,
    uint32_t sequence, char *output, size_t capacity
);

/* Encode a compact SMP/1 fleet-map sample for SMP/1 up/telemetry. */
int stratos_mavlink_encode_map(
    const stratos_mavlink_t *state, uint32_t sequence,
    char *output, size_t capacity
);

#endif
