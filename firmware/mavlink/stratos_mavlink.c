#include "stratos_mavlink.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

#define STRATOS_MAVLINK_CHANNEL MAVLINK_COMM_0

static const char *autopilot_name(uint8_t autopilot) {
    switch (autopilot) {
        case MAV_AUTOPILOT_PX4: return "px4";
        case MAV_AUTOPILOT_ARDUPILOTMEGA: return "ardupilot";
        default: return NULL;
    }
}

static const char *vehicle_type_name(uint8_t type) {
    switch (type) {
        case MAV_TYPE_FIXED_WING: return "fixed-wing";
        case MAV_TYPE_QUADROTOR: return "quadrotor";
        case MAV_TYPE_COAXIAL: return "coaxial";
        case MAV_TYPE_HELICOPTER: return "helicopter";
        case MAV_TYPE_GROUND_ROVER: return "ground-rover";
        case MAV_TYPE_SURFACE_BOAT: return "surface-boat";
        case MAV_TYPE_SUBMARINE: return "submarine";
        case MAV_TYPE_HEXAROTOR: return "hexarotor";
        case MAV_TYPE_OCTOROTOR: return "octorotor";
        case MAV_TYPE_VTOL_TAILSITTER_DUOROTOR:
        case MAV_TYPE_VTOL_TAILSITTER_QUADROTOR:
        case MAV_TYPE_VTOL_TILTROTOR:
        case MAV_TYPE_VTOL_FIXEDROTOR:
        case MAV_TYPE_VTOL_TAILSITTER:
        case MAV_TYPE_VTOL_TILTWING:
        case MAV_TYPE_VTOL_RESERVED5: return "vtol";
        default: return "other";
    }
}

static bool valid_autopilot(uint8_t autopilot) {
    return autopilot == MAV_AUTOPILOT_PX4 ||
           autopilot == MAV_AUTOPILOT_ARDUPILOTMEGA;
}

static bool valid_position(int32_t latitude, int32_t longitude) {
    return latitude >= -900000000 && latitude <= 900000000 &&
           longitude >= -1800000000 && longitude <= 1800000000;
}

static bool supported_message(uint32_t message_id) {
    switch (message_id) {
        case MAVLINK_MSG_ID_HEARTBEAT:
        case MAVLINK_MSG_ID_GLOBAL_POSITION_INT:
        case MAVLINK_MSG_ID_GPS_RAW_INT:
        case MAVLINK_MSG_ID_SYS_STATUS:
        case MAVLINK_MSG_ID_BATTERY_STATUS:
        case MAVLINK_MSG_ID_EXTENDED_SYS_STATE:
        case MAVLINK_MSG_ID_MISSION_CURRENT:
        case MAVLINK_MSG_ID_HOME_POSITION:
        case MAVLINK_MSG_ID_SYSTEM_TIME:
        case MAVLINK_MSG_ID_AUTOPILOT_VERSION:
        case MAVLINK_MSG_ID_ATTITUDE:
        case MAVLINK_MSG_ID_ATTITUDE_QUATERNION:
        case MAVLINK_MSG_ID_LOCAL_POSITION_NED:
        case MAVLINK_MSG_ID_STATUSTEXT:
        case MAVLINK_MSG_ID_HIGH_LATENCY2:
            return true;
        default:
            return false;
    }
}

static uint32_t isqrt_u64(uint64_t value) {
    uint64_t bit = (uint64_t)1u << 62;
    uint64_t result = 0;
    while (bit > value) bit >>= 2;
    while (bit != 0u) {
        if (value >= result + bit) {
            value -= result + bit;
            result = (result >> 1) + bit;
        } else {
            result >>= 1;
        }
        bit >>= 2;
    }
    return (uint32_t)result;
}

static void set_identity(
    stratos_mavlink_t *state, const mavlink_message_t *message,
    uint8_t autopilot, uint8_t vehicle_type
) {
    state->initialized = 1u;
    state->system_id = (uint8_t)message->sysid;
    state->component_id = message->compid;
    state->autopilot = autopilot;
    state->vehicle_type = vehicle_type;
    state->mavlink_version = message->magic == MAVLINK_STX_MAVLINK1 ? 1u : 2u;
}

static bool process_message(
    stratos_mavlink_t *state, const mavlink_message_t *message,
    uint32_t monotonic_ms
) {
    if (message->magic == MAVLINK_STX_MAVLINK1 && !state->allow_mavlink1) {
        state->unsupported_version_messages++;
        return false;
    }
    if (!supported_message(message->msgid)) {
        state->unknown_messages++;
        return false;
    }

    if (message->msgid == MAVLINK_MSG_ID_HEARTBEAT) {
        mavlink_heartbeat_t value;
        mavlink_msg_heartbeat_decode(message, &value);
        if (!valid_autopilot(value.autopilot) || message->sysid == 0u ||
            message->sysid > UINT8_MAX) {
            state->unknown_messages++;
            return false;
        }
        if (state->initialized && message->sysid != state->system_id) {
            state->foreign_messages++;
            return false;
        }
        if (!state->initialized) {
            set_identity(state, message, value.autopilot, value.type);
        }
        if (state->autopilot != value.autopilot) {
            state->foreign_messages++;
            return false;
        }
        state->component_id = message->compid;
        state->vehicle_type = value.type;
        state->mavlink_version = message->magic == MAVLINK_STX_MAVLINK1 ? 1u : 2u;
        state->base_mode = value.base_mode;
        state->custom_mode = value.custom_mode;
        state->system_status = value.system_status;
        state->last_heartbeat_at_ms = monotonic_ms;
        state->has_heartbeat = 1u;
        return true;
    }

    if (!state->initialized) {
        if (message->msgid == MAVLINK_MSG_ID_HIGH_LATENCY2) {
            mavlink_high_latency2_t value;
            mavlink_msg_high_latency2_decode(message, &value);
            if (valid_autopilot(value.autopilot) && message->sysid > 0u &&
                message->sysid <= UINT8_MAX) {
                set_identity(state, message, value.autopilot, value.type);
            }
        }
        if (!state->initialized) {
            state->unbound_messages++;
            return false;
        }
    }
    if (message->sysid != state->system_id) {
        state->foreign_messages++;
        return false;
    }

    switch (message->msgid) {
        case MAVLINK_MSG_ID_GLOBAL_POSITION_INT: {
            mavlink_global_position_int_t value;
            mavlink_msg_global_position_int_decode(message, &value);
            state->boot_ms = value.time_boot_ms;
            if (valid_position(value.lat, value.lon)) {
                state->signed_latitude_e7 = value.lat;
                state->longitude_e7 = value.lon;
                state->altitude_msl_mm = value.alt;
                state->relative_altitude_mm = value.relative_alt;
                state->valid |= STRATOS_MAVLINK_VALID_POSITION;
                state->valid |= STRATOS_MAVLINK_VALID_RELATIVE_ALTITUDE;
            } else {
                state->valid &= (uint16_t)~STRATOS_MAVLINK_VALID_POSITION;
            }
            state->velocity_north_cms = value.vx == INT16_MAX ? 0 : value.vx;
            state->velocity_east_cms = value.vy == INT16_MAX ? 0 : value.vy;
            state->velocity_down_cms = value.vz == INT16_MAX ? 0 : value.vz;
            if (value.vx != INT16_MAX && value.vy != INT16_MAX) {
                int64_t north = value.vx;
                int64_t east = value.vy;
                state->ground_speed_cms = isqrt_u64(
                    (uint64_t)(north * north + east * east)
                );
                state->valid |= STRATOS_MAVLINK_VALID_GROUND_SPEED;
            } else {
                state->valid &= (uint16_t)~STRATOS_MAVLINK_VALID_GROUND_SPEED;
            }
            state->has_heading = value.hdg != UINT16_MAX;
            state->heading_cdeg = value.hdg;
            return true;
        }
        case MAVLINK_MSG_ID_GPS_RAW_INT: {
            mavlink_gps_raw_int_t value;
            mavlink_msg_gps_raw_int_decode(message, &value);
            state->gps_fix_type = value.fix_type;
            state->satellites_visible = value.satellites_visible;
            state->gps_eph_centi = value.eph;
            state->gps_epv_centi = value.epv;
            state->valid |= STRATOS_MAVLINK_VALID_GPS;
            return true;
        }
        case MAVLINK_MSG_ID_SYS_STATUS: {
            mavlink_sys_status_t value;
            mavlink_msg_sys_status_decode(message, &value);
            state->sensor_load_decipct = value.load;
            state->communication_drop_centipct = value.drop_rate_comm;
            if (!state->has_battery_voltage && value.voltage_battery != UINT16_MAX) {
                state->battery_voltage_mv = value.voltage_battery;
                state->has_battery_voltage = 1u;
            }
            if (!state->has_battery_current && value.current_battery != -1) {
                state->battery_current_ca = value.current_battery;
                state->has_battery_current = 1u;
            }
            if (!state->has_battery_remaining && value.battery_remaining >= 0) {
                state->battery_remaining = (uint8_t)value.battery_remaining;
                state->has_battery_remaining = 1u;
            }
            if (state->has_battery_voltage || state->has_battery_current ||
                state->has_battery_remaining)
                state->valid |= STRATOS_MAVLINK_VALID_BATTERY;
            return true;
        }
        case MAVLINK_MSG_ID_BATTERY_STATUS: {
            mavlink_battery_status_t value;
            uint32_t millivolts = 0u;
            uint8_t index;
            mavlink_msg_battery_status_decode(message, &value);
            for (index = 0u; index < 10u; ++index) {
                if (value.voltages[index] != UINT16_MAX &&
                    value.voltages[index] != 0u) millivolts += value.voltages[index];
            }
            for (index = 0u; index < 4u; ++index) {
                millivolts += value.voltages_ext[index];
            }
            state->battery_id = value.id;
            state->has_battery_id = 1u;
            state->has_battery_voltage = millivolts > 0u;
            state->battery_voltage_mv = millivolts;
            state->has_battery_current = value.current_battery != -1;
            state->battery_current_ca = value.current_battery;
            state->has_battery_remaining = value.battery_remaining >= 0;
            if (state->has_battery_remaining)
                state->battery_remaining = (uint8_t)value.battery_remaining;
            state->valid |= STRATOS_MAVLINK_VALID_BATTERY;
            return true;
        }
        case MAVLINK_MSG_ID_EXTENDED_SYS_STATE: {
            mavlink_extended_sys_state_t value;
            mavlink_msg_extended_sys_state_decode(message, &value);
            state->vtol_state = value.vtol_state;
            state->landed_state = value.landed_state;
            return true;
        }
        case MAVLINK_MSG_ID_MISSION_CURRENT: {
            mavlink_mission_current_t value;
            mavlink_msg_mission_current_decode(message, &value);
            state->mission_sequence = value.seq;
            state->mission_total = value.total;
            state->mission_state = value.mission_state;
            state->mission_mode = value.mission_mode;
            state->has_mission_state = value.mission_state != 0u;
            state->valid |= STRATOS_MAVLINK_VALID_MISSION;
            return true;
        }
        case MAVLINK_MSG_ID_HOME_POSITION: {
            mavlink_home_position_t value;
            mavlink_msg_home_position_decode(message, &value);
            if (valid_position(value.latitude, value.longitude)) {
                state->home_latitude_e7 = value.latitude;
                state->home_longitude_e7 = value.longitude;
                state->home_altitude_mm = value.altitude;
                state->valid |= STRATOS_MAVLINK_VALID_HOME;
            }
            return true;
        }
        case MAVLINK_MSG_ID_SYSTEM_TIME: {
            mavlink_system_time_t value;
            mavlink_msg_system_time_decode(message, &value);
            if (value.time_unix_usec != 0u) {
                state->unix_time_us = value.time_unix_usec;
                state->valid |= STRATOS_MAVLINK_VALID_SYSTEM_TIME;
            }
            return true;
        }
        case MAVLINK_MSG_ID_AUTOPILOT_VERSION: {
            mavlink_autopilot_version_t value;
            mavlink_msg_autopilot_version_decode(message, &value);
            state->capabilities = value.capabilities;
            state->flight_sw_version = value.flight_sw_version;
            state->hardware_uid = value.uid;
            return true;
        }
        case MAVLINK_MSG_ID_ATTITUDE: {
            mavlink_attitude_t value;
            mavlink_msg_attitude_decode(message, &value);
            state->boot_ms = value.time_boot_ms;
            state->roll_rad = value.roll;
            state->pitch_rad = value.pitch;
            state->yaw_rad = value.yaw;
            state->roll_speed_rad_s = value.rollspeed;
            state->pitch_speed_rad_s = value.pitchspeed;
            state->yaw_speed_rad_s = value.yawspeed;
            state->valid |= STRATOS_MAVLINK_VALID_ATTITUDE;
            return true;
        }
        case MAVLINK_MSG_ID_ATTITUDE_QUATERNION: {
            mavlink_attitude_quaternion_t value;
            mavlink_msg_attitude_quaternion_decode(message, &value);
            state->boot_ms = value.time_boot_ms;
            state->roll_speed_rad_s = value.rollspeed;
            state->pitch_speed_rad_s = value.pitchspeed;
            state->yaw_speed_rad_s = value.yawspeed;
            return true;
        }
        case MAVLINK_MSG_ID_LOCAL_POSITION_NED: {
            mavlink_local_position_ned_t value;
            mavlink_msg_local_position_ned_decode(message, &value);
            state->boot_ms = value.time_boot_ms;
            state->local_north_m = value.x;
            state->local_east_m = value.y;
            state->local_down_m = value.z;
            state->local_velocity_north_m_s = value.vx;
            state->local_velocity_east_m_s = value.vy;
            state->local_velocity_down_m_s = value.vz;
            state->valid |= STRATOS_MAVLINK_VALID_LOCAL_POSITION;
            return true;
        }
        case MAVLINK_MSG_ID_STATUSTEXT: {
            mavlink_statustext_t value;
            size_t length = sizeof value.text;
            mavlink_msg_statustext_decode(message, &value);
            while (length > 0u && value.text[length - 1u] == '\0') length--;
            if (length > 50u) length = 50u;
            memcpy(state->last_status_text, value.text, length);
            state->last_status_text[length] = '\0';
            state->status_severity = value.severity;
            state->has_status_text = 1u;
            return true;
        }
        case MAVLINK_MSG_ID_HIGH_LATENCY2: {
            mavlink_high_latency2_t value;
            mavlink_msg_high_latency2_decode(message, &value);
            if (valid_autopilot(value.autopilot) &&
                value.autopilot == state->autopilot) {
                state->vehicle_type = value.type;
                state->custom_mode = value.custom_mode;
            }
            state->boot_ms = value.timestamp;
            if (valid_position(value.latitude, value.longitude)) {
                state->signed_latitude_e7 = value.latitude;
                state->longitude_e7 = value.longitude;
                state->altitude_msl_mm = (int32_t)value.altitude * 1000;
                state->relative_altitude_mm = 0;
                state->valid &= (uint16_t)~STRATOS_MAVLINK_VALID_RELATIVE_ALTITUDE;
                state->valid |= STRATOS_MAVLINK_VALID_POSITION;
            }
            state->has_heading = value.heading != UINT8_MAX;
            state->heading_cdeg = (uint16_t)value.heading * 200u;
            state->ground_speed_cms = (uint32_t)value.groundspeed * 20u;
            state->valid |= STRATOS_MAVLINK_VALID_GROUND_SPEED;
            if (value.battery >= 0) {
                state->battery_remaining = (uint8_t)value.battery;
                state->has_battery_remaining = 1u;
                state->valid |= STRATOS_MAVLINK_VALID_BATTERY;
            }
            state->failure_flags = value.failure_flags;
            state->last_heartbeat_at_ms = monotonic_ms;
            state->has_heartbeat = 1u;
            return true;
        }
        default:
            state->unknown_messages++;
            return false;
    }
}

void stratos_mavlink_init(stratos_mavlink_t *state, bool mavlink1_compatibility) {
    mavlink_status_t *channel_status;
    if (state == NULL) return;
    memset(state, 0, sizeof *state);
    state->allow_mavlink1 = mavlink1_compatibility ? 1u : 0u;
    state->landed_state = MAV_LANDED_STATE_UNDEFINED;
    state->vtol_state = MAV_VTOL_STATE_UNDEFINED;
    state->satellites_visible = UINT8_MAX;
    state->battery_remaining = UINT8_MAX;
    state->gps_eph_centi = UINT16_MAX;
    state->gps_epv_centi = UINT16_MAX;
    mavlink_reset_channel_status(STRATOS_MAVLINK_CHANNEL);
    channel_status = mavlink_get_channel_status(STRATOS_MAVLINK_CHANNEL);
    channel_status->signing = NULL;
    channel_status->signing_streams = NULL;
}

void stratos_mavlink_set_signing_key(
    stratos_mavlink_t *state, const uint8_t key[32], uint64_t timestamp
) {
    mavlink_status_t *channel_status;
    if (state == NULL || key == NULL) return;
    memset(&state->signing, 0, sizeof state->signing);
    memset(&state->signing_streams, 0, sizeof state->signing_streams);
    memcpy(state->signing.secret_key, key, sizeof state->signing.secret_key);
    state->signing.timestamp = timestamp;
    state->signing_enabled = 1u;
    channel_status = mavlink_get_channel_status(STRATOS_MAVLINK_CHANNEL);
    channel_status->signing = &state->signing;
    channel_status->signing_streams = &state->signing_streams;
}

int stratos_mavlink_feed(
    stratos_mavlink_t *state, uint8_t byte, uint32_t monotonic_ms
) {
    mavlink_message_t message;
    mavlink_status_t parse_status;
    mavlink_status_t *channel_status;
    uint8_t errors_before;
    uint8_t framing;
    bool processed;
    if (state == NULL) return 0;
    memset(&parse_status, 0, sizeof parse_status);
    channel_status = mavlink_get_channel_status(STRATOS_MAVLINK_CHANNEL);
    errors_before = channel_status->parse_error;
    framing = mavlink_parse_char(
        STRATOS_MAVLINK_CHANNEL, byte, &message, &parse_status
    );
    if (channel_status->parse_error != 0u) {
        state->parse_errors += channel_status->parse_error;
        channel_status->parse_error = 0u;
    } else if (parse_status.packet_rx_drop_count > errors_before) {
        state->parse_errors += parse_status.packet_rx_drop_count - errors_before;
    }
    if (framing != MAVLINK_FRAMING_OK) return 0;
    state->received_messages++;
    if (state->initialized && message.sysid == state->system_id) {
        if (state->has_last_packet_sequence) {
            uint8_t gap = (uint8_t)(message.seq - state->last_packet_sequence - 1u);
            if (gap <= 127u) state->dropped_messages += gap;
        }
        state->last_packet_sequence = message.seq;
        state->has_last_packet_sequence = 1u;
    }
    processed = process_message(state, &message, monotonic_ms);
    return processed ? 1 : 0;
}

int stratos_mavlink_encode_binding(
    const stratos_mavlink_t *state, const char *vehicle_id,
    uint32_t sequence, char *output, size_t capacity
) {
    char system_id[4];
    char component_id[4];
    char version[4];
    const char *autopilot;
    const char *fields[6];
    if (state == NULL || !state->initialized || vehicle_id == NULL) return -1;
    autopilot = autopilot_name(state->autopilot);
    if (autopilot == NULL) return -1;
    (void)snprintf(system_id, sizeof system_id, "%u", state->system_id);
    (void)snprintf(component_id, sizeof component_id, "%u", state->component_id);
    (void)snprintf(version, sizeof version, "%u", state->mavlink_version);
    fields[0] = vehicle_id;
    fields[1] = system_id;
    fields[2] = component_id;
    fields[3] = autopilot;
    fields[4] = vehicle_type_name(state->vehicle_type);
    fields[5] = version;
    return smp1_encode(output, capacity, SMP1_VEHICLE, sequence, 6u, fields);
}

static const char *integer_or_na(char *buffer, size_t size, int64_t value,
                                 bool valid) {
    if (!valid) return "na";
    (void)snprintf(buffer, size, "%lld", (long long)value);
    return buffer;
}

int stratos_mavlink_encode_map(
    const stratos_mavlink_t *state, uint32_t sequence, uint32_t monotonic_ms,
    char *output, size_t capacity
) {
    char values[11][16];
    const char *fields[11];
    uint8_t flags = 0u;
    bool position_valid;
    if (state == NULL || !state->initialized) return -1;
    position_valid = (state->valid & STRATOS_MAVLINK_VALID_POSITION) != 0u;
    fields[0] = integer_or_na(values[0], sizeof values[0], state->boot_ms,
                              state->boot_ms != 0u);
    fields[1] = integer_or_na(values[1], sizeof values[1], state->signed_latitude_e7,
                              position_valid);
    fields[2] = integer_or_na(values[2], sizeof values[2], state->longitude_e7,
                              position_valid);
    fields[3] = integer_or_na(values[3], sizeof values[3], state->altitude_msl_mm,
                              position_valid);
    fields[4] = integer_or_na(values[4], sizeof values[4], state->relative_altitude_mm,
                              position_valid && (state->valid &
                              STRATOS_MAVLINK_VALID_RELATIVE_ALTITUDE) != 0u);
    fields[5] = integer_or_na(values[5], sizeof values[5], state->ground_speed_cms,
                              position_valid && (state->valid &
                              STRATOS_MAVLINK_VALID_GROUND_SPEED) != 0u);
    fields[6] = integer_or_na(values[6], sizeof values[6], state->heading_cdeg,
                              position_valid && state->has_heading);
    fields[7] = integer_or_na(values[7], sizeof values[7], state->gps_fix_type,
                              (state->valid & STRATOS_MAVLINK_VALID_GPS) != 0u);
    fields[8] = integer_or_na(values[8], sizeof values[8], state->satellites_visible,
                              (state->valid & STRATOS_MAVLINK_VALID_GPS) != 0u &&
                              state->satellites_visible != UINT8_MAX);
    fields[9] = integer_or_na(values[9], sizeof values[9], state->battery_remaining,
                              state->has_battery_remaining != 0u);
    if ((state->base_mode & MAV_MODE_FLAG_SAFETY_ARMED) != 0u) flags |= 1u;
    if (state->landed_state == MAV_LANDED_STATE_ON_GROUND) flags |= 2u;
    if (position_valid) flags |= 8u;
    if ((state->valid & STRATOS_MAVLINK_VALID_BATTERY) != 0u ||
        state->has_battery_remaining) flags |= 16u;
    if (state->has_heartbeat &&
        (uint32_t)(monotonic_ms - state->last_heartbeat_at_ms) <= 5000u)
        flags |= 32u;
    fields[10] = integer_or_na(values[10], sizeof values[10], flags, true);
    return smp1_encode(output, capacity, SMP1_MAP, sequence, 11u, fields);
}
