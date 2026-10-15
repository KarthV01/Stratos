package mqttadapter

import "testing"

func TestDecodeAndTopicMatch(t *testing.T) {
	topic, e := ParseTopic("stratos/v1/devices/garden-1/up/telemetry")
	if e != nil {
		t.Fatal(e)
	}
	f, e := Decode([]byte("SMP1|T|7|soil-moisture|43.2|pct"))
	if e != nil {
		t.Fatal(e)
	}
	if f.Sequence != 7 || f.Fields[1] != "43.2" {
		t.Fatalf("unexpected frame: %#v", f)
	}
	if e = EnsureMatch(topic, f); e != nil {
		t.Fatal(e)
	}
}
func TestRejectsOversizedAndMismatchedFrames(t *testing.T) {
	if _, e := Decode(make([]byte, 257)); e == nil {
		t.Fatal("accepted oversized frame")
	}
	topic, _ := ParseTopic("stratos/v1/devices/a/up/state")
	f, _ := Decode([]byte("SMP1|T|1|temp|20|C"))
	if EnsureMatch(topic, f) == nil {
		t.Fatal("accepted mismatched channel")
	}
}

func TestDecodeGeospatialSensor(t *testing.T) {
	f, err := Decode([]byte("SMP1|G|14|temperature|21.5|C|407128001|-740060002|12500"))
	if err != nil || f.Type != "G" || f.Fields[3] != "407128001" {
		t.Fatalf("unexpected geospatial frame: %#v %v", f, err)
	}
	if _, err = Decode([]byte("SMP1|G|15|temperature|21.5|C|900000001|0|na")); err == nil {
		t.Fatal("expected latitude validation error")
	}
}
