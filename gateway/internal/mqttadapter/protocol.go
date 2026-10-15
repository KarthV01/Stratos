package mqttadapter

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

const TopicPrefix = "stratos/v1/devices"

var deviceID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`)
var token = regexp.MustCompile(`^(?:[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,63}|-[0-9]+(?:\.[0-9]+)?|-)$`)

type Topic struct{ DeviceID, Direction, Channel string }
type Frame struct {
	Type     string
	Sequence uint32
	Fields   []string
}

var channels = map[string]string{"H": "hello", "T": "telemetry", "S": "state", "A": "ack", "W": "welcome", "C": "command", "E": "error", "D": "telemetry", "M": "telemetry", "V": "event", "B": "state", "P": "telemetry"}
var shapes = map[string]int{"H": 4, "T": 3, "S": 2, "A": 3, "W": 2, "C": 3, "E": 2, "D": 11, "M": 3, "V": 3, "B": 6, "P": 12}

func ParseTopic(v string) (Topic, error) {
	p := strings.Split(v, "/")
	if len(p) == 5 && strings.Join(p[:3], "/") == TopicPrefix && p[4] == "availability" && deviceID.MatchString(p[3]) {
		return Topic{DeviceID: p[3], Channel: p[4]}, nil
	}
	if len(p) != 6 || strings.Join(p[:3], "/") != TopicPrefix || !deviceID.MatchString(p[3]) || p[4] != "up" {
		return Topic{}, fmt.Errorf("invalid Stratos MQTT topic")
	}
	valid := map[string]bool{"hello": true, "telemetry": true, "state": true, "event": true, "ack": true}
	if !valid[p[5]] {
		return Topic{}, fmt.Errorf("invalid MQTT channel")
	}
	return Topic{p[3], p[4], p[5]}, nil
}
func DeviceTopic(id, direction, channel string) string {
	return TopicPrefix + "/" + id + "/" + direction + "/" + channel
}
func Decode(payload []byte) (Frame, error) {
	if len(payload) == 0 || len(payload) > 256 {
		return Frame{}, fmt.Errorf("frame must contain 1 to 256 bytes")
	}
	p := strings.Split(string(payload), "|")
	if len(p) < 3 || p[0] != "SMP1" {
		return Frame{}, fmt.Errorf("unsupported MQTT frame")
	}
	if _, ok := shapes[p[1]]; !ok {
		return Frame{}, fmt.Errorf("invalid frame type")
	}
	seq, e := strconv.ParseUint(p[2], 10, 32)
	if e != nil {
		return Frame{}, fmt.Errorf("invalid sequence")
	}
	fields := p[3:]
	if len(fields) != shapes[p[1]] {
		return Frame{}, fmt.Errorf("%s frame requires %d fields", p[1], shapes[p[1]])
	}
	for _, v := range fields {
		if !token.MatchString(v) {
			return Frame{}, fmt.Errorf("invalid field")
		}
	}
	f := Frame{p[1], uint32(seq), fields}
	if e = validate(f); e != nil {
		return Frame{}, e
	}
	return f, nil
}
func validate(f Frame) error {
	if f.Type == "H" && (f.Fields[2] != "2" && f.Fields[2] != "3") {
		return fmt.Errorf("HELLO tier must be 2 or 3")
	}
	if f.Type == "A" && !map[string]bool{"accepted": true, "running": true, "completed": true, "failed": true, "rejected": true}[f.Fields[1]] {
		return fmt.Errorf("invalid ACK status")
	}
	if f.Type == "V" && !map[string]bool{"info": true, "warning": true, "error": true, "critical": true}[f.Fields[1]] {
		return fmt.Errorf("invalid EVENT severity")
	}
	if f.Type == "M" {
		for _, v := range f.Fields[1:] {
			n, e := strconv.Atoi(v)
			if e != nil || n < 0 || n > 100 {
				return fmt.Errorf("invalid percentage")
			}
		}
	}
	return nil
}
func EnsureMatch(t Topic, f Frame) error {
	if channels[f.Type] != t.Channel {
		return fmt.Errorf("frame type does not match MQTT topic")
	}
	return nil
}
func Encode(kind string, seq uint32, fields ...string) ([]byte, error) {
	if len(fields) != shapes[kind] {
		return nil, fmt.Errorf("invalid field count")
	}
	v := fmt.Sprintf("SMP1|%s|%d|%s", kind, seq, strings.Join(fields, "|"))
	if len(v) > 256 {
		return nil, fmt.Errorf("frame exceeds 256 bytes")
	}
	return []byte(v), nil
}
