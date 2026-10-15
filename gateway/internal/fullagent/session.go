package fullagent

import (
	"context"
	"time"

	gatewayv1 "github.com/KarthV01/Stratos/gateway/gen/stratos/gateway/v1"
)

type GatewaySession struct {
	Backend        *Backend
	Transport      string
	Session        *Session
	Max, Available uint32
}

func (s *GatewaySession) Open(ctx context.Context, m *gatewayv1.AgentMessage) (*gatewayv1.GatewayMessage, error) {
	h := m.GetHello()
	if h == nil {
		return nil, &Violation{"hello-required", "the first message must be hello"}
	}
	session, err := s.Backend.Register(ctx, h, s.Transport)
	if err != nil {
		return nil, err
	}
	s.Session = session
	s.Max = h.GetMaxConcurrentTasks()
	if s.Max < 1 {
		s.Max = 1
	}
	s.Available = s.Max
	return &gatewayv1.GatewayMessage{Payload: &gatewayv1.GatewayMessage_Welcome{Welcome: &gatewayv1.Welcome{SessionId: session.ID, HeartbeatIntervalSeconds: int32(s.Backend.HeartbeatSeconds), ServerTimeUnixMs: time.Now().UnixMilli()}}}, nil
}
func (s *GatewaySession) Receive(ctx context.Context, m *gatewayv1.AgentMessage) (*gatewayv1.GatewayMessage, error) {
	if s.Session == nil {
		return nil, &Violation{"hello-required", "session is not open"}
	}
	if h := m.GetHeartbeat(); h != nil {
		if err := s.Backend.Heartbeat(ctx, s.Session); err != nil {
			return nil, err
		}
		s.Available = min(h.GetAvailableTaskSlots(), s.Max)
		return nil, nil
	}
	if r := m.GetTaskResult(); r != nil {
		reply, err := s.Backend.Complete(ctx, s.Session, r)
		s.Available = min(s.Available+1, s.Max)
		return reply, err
	}
	return nil, &Violation{"unexpected-message", "hello is only valid once"}
}
func (s *GatewaySession) Next(ctx context.Context) (*gatewayv1.GatewayMessage, error) {
	if s.Available == 0 {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(100 * time.Millisecond):
			return nil, nil
		}
	}
	m, err := s.Backend.Next(ctx, s.Session)
	if m != nil {
		s.Available--
	}
	return m, err
}
func (s *GatewaySession) Close(ctx context.Context) {
	if s.Session != nil {
		s.Backend.Disconnect(ctx, s.Session)
	}
}
func ErrorMessage(err error) *gatewayv1.GatewayMessage {
	v, ok := err.(*Violation)
	if !ok {
		v = &Violation{"internal", "gateway backend error"}
	}
	return &gatewayv1.GatewayMessage{Payload: &gatewayv1.GatewayMessage_Error{Error: &gatewayv1.ProtocolError{Code: v.Code, Detail: v.Detail, Fatal: true}}}
}
