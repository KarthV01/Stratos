package mqttadapter

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"log"
	"strconv"
	"time"

	"github.com/KarthV01/Stratos/gateway/internal/boards"
	mqtt "github.com/eclipse/paho.mqtt.golang"
	"github.com/redis/go-redis/v9"
)

type inbound struct {
	topic   string
	payload []byte
}
type Bridge struct {
	Redis            *redis.Client
	Client           mqtt.Client
	ClientID         string
	HeartbeatSeconds int
	messages         chan inbound
}

func New(r *redis.Client, broker, user, password, clientID string, heartbeat int, useTLS bool) *Bridge {
	b := &Bridge{Redis: r, ClientID: clientID, HeartbeatSeconds: heartbeat, messages: make(chan inbound, 1000)}
	o := mqtt.NewClientOptions().AddBroker(broker).SetClientID(clientID).SetProtocolVersion(4).SetAutoReconnect(true)
	if user != "" {
		o.SetUsername(user).SetPassword(password)
	}
	if useTLS {
		o.SetTLSConfig(&tls.Config{MinVersion: tls.VersionTLS12})
	}
	o.SetOnConnectHandler(func(c mqtt.Client) {
		if t := c.Subscribe(TopicPrefix+"/+/up/+", 1, b.onMessage); t.Wait() && t.Error() != nil {
			log.Printf("MQTT subscribe: %v", t.Error())
		}
		if t := c.Subscribe(TopicPrefix+"/+/availability", 1, b.onMessage); t.Wait() && t.Error() != nil {
			log.Printf("MQTT subscribe: %v", t.Error())
		}
	})
	b.Client = mqtt.NewClient(o)
	return b
}
func (b *Bridge) onMessage(_ mqtt.Client, m mqtt.Message) {
	item := inbound{m.Topic(), append([]byte(nil), m.Payload()...)}
	select {
	case b.messages <- item:
	default:
		log.Printf("MQTT inbound queue full; dropping %s", m.Topic())
	}
}
func (b *Bridge) Run(ctx context.Context) error {
	if t := b.Client.Connect(); t.Wait() && t.Error() != nil {
		return t.Error()
	}
	defer b.Client.Disconnect(250)
	if err := b.ensureGroup(ctx); err != nil {
		return err
	}
	errs := make(chan error, 2)
	go func() { errs <- b.consume(ctx) }()
	go func() { errs <- b.dispatch(ctx) }()
	select {
	case <-ctx.Done():
		return nil
	case err := <-errs:
		return err
	}
}
func (b *Bridge) ensureGroup(ctx context.Context) error {
	err := b.Redis.XGroupCreateMkStream(ctx, "device-commands", "mqtt-bridges", "0").Err()
	if err != nil && !contains(err.Error(), "BUSYGROUP") {
		return err
	}
	return nil
}
func (b *Bridge) consume(ctx context.Context) error {
	for {
		select {
		case <-ctx.Done():
			return nil
		case m := <-b.messages:
			if err := b.Process(ctx, m.topic, m.payload); err != nil {
				log.Printf("MQTT rejected %s: %v", m.topic, err)
			}
		}
	}
}
func (b *Bridge) publish(id, channel string, payload []byte) {
	b.Client.Publish(DeviceTopic(id, "down", channel), 1, false, payload)
}
func (b *Bridge) error(id string, seq uint32, code, detail string) {
	p, _ := Encode("E", seq, code, detail)
	b.publish(id, "error", p)
}
func (b *Bridge) Process(ctx context.Context, topicValue string, payload []byte) error {
	topic, err := ParseTopic(topicValue)
	if err != nil {
		return err
	}
	now := time.Now()
	if topic.Channel == "availability" {
		v := string(payload)
		if v != "online" && v != "offline" {
			return fmt.Errorf("availability must be online or offline")
		}
		if b.Redis.Exists(ctx, "device:"+topic.DeviceID).Val() > 0 {
			b.Redis.HSet(ctx, "device:"+topic.DeviceID, map[string]any{"availability": v, "last_seen": seconds(now)})
		}
		return b.event(ctx, topic.DeviceID, "availability", v, "mqtt", "-", now)
	}
	f, err := Decode(payload)
	if err != nil {
		return err
	}
	if err = EnsureMatch(topic, f); err != nil {
		return err
	}
	if f.Type == "H" {
		return b.hello(ctx, topic.DeviceID, f, len(payload), now)
	}
	key := "device:" + topic.DeviceID
	device, err := b.Redis.HGetAll(ctx, key).Result()
	if err != nil {
		return err
	}
	if len(device) == 0 {
		b.error(topic.DeviceID, f.Sequence, "not-registered", "send-hello")
		return nil
	}
	tier, _ := strconv.Atoi(device["tier"])
	if tier == 3 && len(payload) > 128 {
		b.error(topic.DeviceID, f.Sequence, "frame-too-large", "tier-3-limit")
		return nil
	}
	allowed := map[int]map[string]bool{2: {"T": true, "G": true, "S": true, "A": true, "D": true, "V": true, "B": true, "P": true}, 3: {"T": true, "G": true, "S": true, "A": true, "M": true, "B": true, "P": true}}
	if !allowed[tier][f.Type] {
		b.error(topic.DeviceID, f.Sequence, "tier-frame", "not-allowed")
		return nil
	}
	if f.Type != "T" && f.Type != "P" {
		ok, e := b.Redis.SetNX(ctx, fmt.Sprintf("device-frame:%s:%s:%d", topic.DeviceID, f.Type, f.Sequence), "1", 24*time.Hour).Result()
		if e != nil {
			return e
		}
		if !ok {
			return nil
		}
	}
	b.Redis.HSet(ctx, key, map[string]any{"last_seen": seconds(now), "last_sequence": f.Sequence})
	b.Redis.ZAdd(ctx, "device-index", redis.Z{Score: float64(now.UnixNano()) / 1e9, Member: topic.DeviceID})
	switch f.Type {
	case "T":
		if err = b.telemetry(ctx, topic.DeviceID, tier, "sensor", map[string]any{"sensor": f.Fields[0], "value": f.Fields[1], "unit": f.Fields[2]}, f.Sequence, now); err != nil {
			return err
		}
		return b.locateScalar(ctx, topic.DeviceID, device, f, now)
	case "G":
		altitude := any(nil)
		if f.Fields[5] != "na" {
			altitude = atoi(f.Fields[5])
		}
		if err = b.telemetry(ctx, topic.DeviceID, tier, "sensor", map[string]any{"sensor": f.Fields[0], "value": f.Fields[1], "unit": f.Fields[2], "latitude_e7": atoi(f.Fields[3]), "longitude_e7": atoi(f.Fields[4]), "altitude_mm": altitude}, f.Sequence, now); err != nil {
			return err
		}
		return b.observation(ctx, topic.DeviceID, device["vehicle_id"], f.Fields[0], f.Fields[1], f.Fields[2], atoi(f.Fields[3]), atoi(f.Fields[4]), altitude, "explicit", f.Sequence, now)
	case "D":
		names := []string{"uptime_ms", "latitude_e7", "longitude_e7", "relative_altitude_mm", "velocity_north_cms", "velocity_east_cms", "velocity_down_cms", "heading_cdeg", "voltage_mv", "remaining_pct", "flags"}
		v := map[string]any{}
		for i, n := range names {
			v[n], _ = strconv.Atoi(f.Fields[i])
		}
		return b.telemetry(ctx, topic.DeviceID, tier, "flight", v, f.Sequence, now)
	case "M":
		return b.telemetry(ctx, topic.DeviceID, tier, "essential", map[string]any{"flags": atoi(f.Fields[0]), "battery_pct": atoi(f.Fields[1]), "link_pct": atoi(f.Fields[2])}, f.Sequence, now)
	case "S":
		if err = b.Redis.HSet(ctx, "device-state:"+topic.DeviceID, f.Fields[0], f.Fields[1]).Err(); err != nil {
			return err
		}
		return b.event(ctx, topic.DeviceID, "state", f.Fields[0], f.Fields[1], "-", now)
	case "A":
		command := "device-command:" + f.Fields[0]
		if b.Redis.HGet(ctx, command, "device_id").Val() == topic.DeviceID {
			b.Redis.HSet(ctx, command, map[string]any{"status": f.Fields[1], "code": f.Fields[2], "updated_at": seconds(now)})
			return b.event(ctx, topic.DeviceID, "command", f.Fields[0], f.Fields[1], "-", now)
		}
	case "V":
		return b.event(ctx, topic.DeviceID, "agent", f.Fields[0], f.Fields[1], f.Fields[2], now)
	case "B":
		return b.Redis.HSet(ctx, key, map[string]any{"vehicle_id": f.Fields[0], "mav_system_id": f.Fields[1], "mav_component_id": f.Fields[2], "mav_autopilot": f.Fields[3], "mav_vehicle_type": f.Fields[4], "mavlink_version": f.Fields[5], "vehicle_bound_at": seconds(now)}).Err()
	case "P":
		return b.mapSnapshot(ctx, topic.DeviceID, device, f, now)
	}
	return nil
}
func (b *Bridge) hello(ctx context.Context, id string, f Frame, size int, now time.Time) error {
	board, ok := boards.Resolve(f.Fields[0])
	if !ok {
		b.error(id, f.Sequence, "unknown-board", "not-registered")
		return nil
	}
	tier, _ := strconv.Atoi(f.Fields[2])
	if board.Tier == 1 || board.Tier != tier {
		b.error(id, f.Sequence, "tier-mismatch", "not-registered")
		return nil
	}
	if tier == 3 && size > 128 {
		b.error(id, f.Sequence, "frame-too-large", "tier-3-limit")
		return nil
	}
	tierCode := "embedded-agent"
	if tier == 3 {
		tierCode = "minimal-agent"
	}
	p := b.Redis.Pipeline()
	p.HSet(ctx, "device:"+id, map[string]any{"id": id, "board": board.ID, "firmware": f.Fields[1], "tier": tier, "tier_code": tierCode, "transport": "mqtt", "capabilities": f.Fields[3], "availability": "online", "last_seen": seconds(now), "last_sequence": f.Sequence})
	p.ZAdd(ctx, "device-index", redis.Z{Score: float64(now.UnixNano()) / 1e9, Member: id})
	p.XAdd(ctx, &redis.XAddArgs{Stream: "device-events", MaxLen: 10000, Approx: true, Values: map[string]any{"device_id": id, "kind": "session", "name": "hello", "value": "accepted", "detail": "-", "recorded_at": seconds(now)}})
	if _, err := p.Exec(ctx); err != nil {
		return err
	}
	welcome, _ := Encode("W", f.Sequence, strconv.FormatInt(now.Unix(), 10), strconv.Itoa(b.HeartbeatSeconds))
	b.publish(id, "welcome", welcome)
	return nil
}
func (b *Bridge) telemetry(ctx context.Context, id string, tier int, kind string, values map[string]any, seq uint32, now time.Time) error {
	payload, _ := json.Marshal(values)
	record := map[string]any{"device_id": id, "tier": tier, "kind": kind, "payload": string(payload), "recorded_at": seconds(now), "sequence": seq}
	p := b.Redis.Pipeline()
	p.HSet(ctx, "device-telemetry-latest:"+id, record)
	p.XAdd(ctx, &redis.XAddArgs{Stream: "device-telemetry", MaxLen: 100000, Approx: true, Values: record})
	_, err := p.Exec(ctx)
	return err
}
func (b *Bridge) locateScalar(ctx context.Context, id string, device map[string]string, f Frame, now time.Time) error {
	if lat := device["fixed_latitude_e7"]; lat != "" && device["fixed_longitude_e7"] != "" {
		altitude := any(nil)
		if device["fixed_altitude_mm"] != "" {
			altitude = atoi(device["fixed_altitude_mm"])
		}
		return b.observation(ctx, id, device["vehicle_id"], f.Fields[0], f.Fields[1], f.Fields[2], atoi(lat), atoi(device["fixed_longitude_e7"]), altitude, "fixed", f.Sequence, now)
	}
	vehicle := device["vehicle_id"]
	if vehicle == "" {
		return nil
	}
	v, err := b.Redis.HGetAll(ctx, "vehicle:"+vehicle).Result()
	if err != nil || v["latitude_e7"] == "" || v["longitude_e7"] == "" {
		return err
	}
	received, _ := strconv.ParseFloat(v["received_at"], 64)
	if float64(now.UnixNano())/1e9-received > 5 {
		return nil
	}
	altitude := any(nil)
	if v["altitude_mm"] != "" && v["altitude_mm"] != "null" {
		altitude = atoi(v["altitude_mm"])
	}
	return b.observation(ctx, id, vehicle, f.Fields[0], f.Fields[1], f.Fields[2], atoi(v["latitude_e7"]), atoi(v["longitude_e7"]), altitude, "vehicle", f.Sequence, now)
}
func (b *Bridge) observation(ctx context.Context, id, vehicle, sensor, value, unit string, lat, lon int, altitude any, source string, seq uint32, now time.Time) error {
	observationID := fmt.Sprintf("%s:%d:%d", id, seq, now.UnixNano())
	var vehicleValue any
	if vehicle != "" {
		vehicleValue = vehicle
	}
	record := map[string]any{"schema": "stratos.observation/1", "id": observationID, "device_id": id, "vehicle_id": vehicleValue, "sensor": sensor, "value": parseFloat(value), "unit": unit, "observed_at": float64(now.UnixNano()) / 1e9, "latitude_e7": lat, "longitude_e7": lon, "altitude_mm": altitude, "location_source": source}
	payload, _ := json.Marshal(record)
	p := b.Redis.TxPipeline()
	p.HSet(ctx, "observation:"+observationID, "payload", string(payload))
	p.ZAdd(ctx, "observation-index", redis.Z{Score: float64(now.UnixNano()) / 1e9, Member: observationID})
	p.XAdd(ctx, &redis.XAddArgs{Stream: "observations", MaxLen: 100000, Approx: true, Values: map[string]any{"observation_id": observationID, "payload": string(payload)}})
	_, err := p.Exec(ctx)
	return err
}
func (b *Bridge) event(ctx context.Context, id, kind, name, value, detail string, now time.Time) error {
	return b.Redis.XAdd(ctx, &redis.XAddArgs{Stream: "device-events", MaxLen: 10000, Approx: true, Values: map[string]any{"device_id": id, "kind": kind, "name": name, "value": value, "detail": detail, "recorded_at": seconds(now)}}).Err()
}
func (b *Bridge) dispatch(ctx context.Context) error {
	for {
		rows, err := b.Redis.XReadGroup(ctx, &redis.XReadGroupArgs{Group: "mqtt-bridges", Consumer: b.ClientID, Streams: []string{"device-commands", ">"}, Count: 10, Block: 5 * time.Second}).Result()
		if err == redis.Nil {
			continue
		}
		if err != nil {
			return err
		}
		for _, stream := range rows {
			for _, m := range stream.Messages {
				seq, _ := strconv.ParseUint(fmt.Sprint(m.Values["sequence"]), 10, 32)
				id := fmt.Sprint(m.Values["device_id"])
				commandID := fmt.Sprint(m.Values["command_id"])
				payload, e := Encode("C", uint32(seq), commandID, fmt.Sprint(m.Values["opcode"]), fmt.Sprint(m.Values["argument"]))
				if e != nil {
					continue
				}
				t := b.Client.Publish(DeviceTopic(id, "down", "command"), 1, false, payload)
				if t.WaitTimeout(10*time.Second) && t.Error() == nil {
					b.Redis.HSet(ctx, "device-command:"+commandID, map[string]any{"status": "sent", "updated_at": seconds(time.Now())})
					b.Redis.XAck(ctx, "device-commands", "mqtt-bridges", m.ID)
				}
			}
		}
	}
}
func (b *Bridge) mapSnapshot(ctx context.Context, id string, d map[string]string, f Frame, now time.Time) error {
	vehicle := d["vehicle_id"]
	if vehicle == "" {
		b.error(id, f.Sequence, "vehicle-required", "send-vehicle")
		return nil
	}
	tier := atoi(d["tier"])
	last, _ := strconv.ParseFloat(d["last_map_at"], 64)
	minimum := .5
	if tier == 3 {
		minimum = 1
	}
	if float64(now.UnixNano())/1e9-last < minimum {
		return nil
	}
	nullable := func(v string) any {
		if v == "na" {
			return nil
		}
		return atoi(v)
	}
	flags := atoi(f.Fields[10])
	snapshot := map[string]any{"vehicle_id": vehicle, "device_id": id, "received_at": float64(now.UnixNano()) / 1e9, "identity": map[string]any{"system_id": atoi(d["mav_system_id"]), "component_id": atoi(d["mav_component_id"]), "autopilot": d["mav_autopilot"], "vehicle_type": d["mav_vehicle_type"], "mavlink_version": atoi(d["mavlink_version"])}, "position": map[string]any{"boot_ms": nullable(f.Fields[0]), "latitude_e7": nullable(f.Fields[1]), "longitude_e7": nullable(f.Fields[2]), "altitude_msl_mm": nullable(f.Fields[3]), "relative_altitude_mm": nullable(f.Fields[4]), "ground_speed_cms": nullable(f.Fields[5]), "heading_cdeg": nullable(f.Fields[6])}, "gps": map[string]any{"fix_type": nullable(f.Fields[7]), "satellites_visible": nullable(f.Fields[8])}, "battery": map[string]any{"remaining_percent": nullable(f.Fields[9])}, "state": map[string]any{"armed": flags&1 != 0, "landed_state": map[bool]int{true: 1, false: 2}[flags&2 != 0], "failsafe": flags&4 != 0}, "link": map[string]any{"connected": flags&32 != 0, "last_heartbeat_at": map[bool]any{true: float64(now.UnixNano()) / 1e9, false: nil}[flags&32 != 0], "session_id": atoi(f.Fields[11])}, "attitude": map[string]any{}, "local_position": map[string]any{}, "schema": "flight/1"}
	encoded, _ := json.Marshal(snapshot)
	lat, lon := nullable(f.Fields[1]), nullable(f.Fields[2])
	p := b.Redis.TxPipeline()
	p.HSet(ctx, "vehicle:"+vehicle, map[string]any{"vehicle_id": vehicle, "device_id": id, "schema": "flight/1", "received_at": seconds(now), "connected": map[bool]string{true: "1", false: "0"}[flags&32 != 0], "session_id": f.Fields[11], "system_id": d["mav_system_id"], "component_id": d["mav_component_id"], "autopilot": d["mav_autopilot"], "vehicle_type": d["mav_vehicle_type"], "latitude_e7": nullString(f.Fields[1]), "longitude_e7": nullString(f.Fields[2]), "altitude_mm": nullString(f.Fields[4]), "snapshot": string(encoded)})
	p.ZAdd(ctx, "vehicle-index", redis.Z{Score: float64(now.UnixNano()) / 1e9, Member: vehicle})
	if lat != nil && lon != nil {
		p.XAdd(ctx, &redis.XAddArgs{Stream: "vehicle-track:" + vehicle, MaxLen: 10000, Approx: true, Values: map[string]any{"received_at": seconds(now), "boot_ms": nullString(f.Fields[0]), "latitude_e7": f.Fields[1], "longitude_e7": f.Fields[2], "altitude_msl_mm": nullString(f.Fields[3]), "relative_altitude_mm": nullString(f.Fields[4]), "ground_speed_cms": nullString(f.Fields[5]), "heading_cdeg": nullString(f.Fields[6])}})
	}
	p.HSet(ctx, "device:"+id, "last_map_at", seconds(now))
	_, err := p.Exec(ctx)
	return err
}
func atoi(v string) int           { n, _ := strconv.Atoi(v); return n }
func parseFloat(v string) float64 { n, _ := strconv.ParseFloat(v, 64); return n }
func seconds(t time.Time) string  { return strconv.FormatFloat(float64(t.UnixNano())/1e9, 'f', -1, 64) }
func contains(s, sub string) bool {
	return len(sub) == 0 || len(s) >= len(sub) && func() bool {
		for i := 0; i+len(sub) <= len(s); i++ {
			if s[i:i+len(sub)] == sub {
				return true
			}
		}
		return false
	}()
}
func nullString(v string) string {
	if v == "na" {
		return "null"
	}
	return v
}
