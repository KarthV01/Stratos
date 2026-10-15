package fullagent

import (
	"bytes"
	"encoding/binary"
	"testing"

	gatewayv1 "github.com/KarthV01/Stratos/gateway/gen/stratos/gateway/v1"
	"google.golang.org/protobuf/proto"
)

func TestTCPFrameMatchesSAGPFraming(t *testing.T) {
	want := &gatewayv1.GatewayMessage{Payload: &gatewayv1.GatewayMessage_Welcome{Welcome: &gatewayv1.Welcome{SessionId: "session-1"}}}
	var wire bytes.Buffer
	if err := writeGateway(&wire, want); err != nil {
		t.Fatal(err)
	}
	data := wire.Bytes()
	if got := binary.BigEndian.Uint32(data[:4]); got != uint32(len(data)-4) {
		t.Fatalf("length=%d payload=%d", got, len(data)-4)
	}
	got := new(gatewayv1.GatewayMessage)
	if err := proto.Unmarshal(data[4:], got); err != nil {
		t.Fatal(err)
	}
	if got.GetWelcome().GetSessionId() != "session-1" {
		t.Fatalf("unexpected message: %v", got)
	}
}

func TestReadAgentRejectsOversizedFrame(t *testing.T) {
	var wire bytes.Buffer
	_ = binary.Write(&wire, binary.BigEndian, uint32(maxFrame+1))
	if _, err := readAgent(&wire); err == nil {
		t.Fatal("oversized frame accepted")
	}
}

func TestErrorMessageDoesNotExposeInternalErrors(t *testing.T) {
	message := ErrorMessage(bytes.ErrTooLarge)
	if message.GetError().GetCode() != "internal" || message.GetError().GetDetail() != "gateway backend error" {
		t.Fatalf("unexpected error: %v", message)
	}
}
