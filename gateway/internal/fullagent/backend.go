package fullagent

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"time"

	gatewayv1 "github.com/KarthV01/Stratos/gateway/gen/stratos/gateway/v1"
	"github.com/KarthV01/Stratos/gateway/internal/boards"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

const protocolVersion = "sagp/1"

var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`)
var capPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,63}$`)

type Violation struct{ Code, Detail string }

func (v *Violation) Error() string { return v.Detail }

type Session struct{ AgentID, ID, Transport string }
type Backend struct {
	Redis                            *redis.Client
	Token                            string
	HeartbeatSeconds, LeaseTimeoutMS int
}

func (b *Backend) Register(ctx context.Context, h *gatewayv1.AgentHello, transport string) (*Session, error) {
	if h.GetProtocolVersion() != protocolVersion {
		return nil, &Violation{"unsupported-version", "expected sagp/1"}
	}
	if !idPattern.MatchString(h.GetAgentId()) {
		return nil, &Violation{"invalid-agent-id", "agent_id is invalid"}
	}
	if h.GetAgentVersion() == "" || len(h.GetAgentVersion()) > 64 {
		return nil, &Violation{"invalid-agent-version", "agent_version is required"}
	}
	if len(h.GetCapabilities()) > 64 {
		return nil, &Violation{"invalid-capabilities", "capabilities are invalid"}
	}
	for _, c := range h.GetCapabilities() {
		if !capPattern.MatchString(c) {
			return nil, &Violation{"invalid-capabilities", "capabilities are invalid"}
		}
	}
	if h.GetMaxConcurrentTasks() > 64 {
		return nil, &Violation{"invalid-capacity", "max_concurrent_tasks must be at most 64"}
	}
	board, ok := boards.Resolve(h.GetBoard())
	if !ok {
		return nil, &Violation{"unknown-board", "board is not in the directory"}
	}
	if board.Tier != 1 {
		return nil, &Violation{"wrong-agent-tier", "board does not support a Full Agent"}
	}
	if b.Token != "" && (len(h.GetToken()) != len(b.Token) || subtle.ConstantTimeCompare([]byte(h.GetToken()), []byte(b.Token)) != 1) {
		return nil, &Violation{"unauthenticated", "agent token was rejected"}
	}
	s := &Session{h.GetAgentId(), uuid.NewString(), transport}
	stream := "agent-tasks:" + s.AgentID
	if err := b.Redis.XGroupCreateMkStream(ctx, stream, "full-agent-gateways", "0").Err(); err != nil && !stringsContains(err.Error(), "BUSYGROUP") {
		return nil, err
	}
	now := time.Now()
	caps, _ := json.Marshal(h.GetCapabilities())
	p := b.Redis.TxPipeline()
	p.HSet(ctx, "agent:"+s.AgentID, map[string]any{"id": s.AgentID, "session_id": s.ID, "board": board.ID, "agent_version": h.GetAgentVersion(), "capabilities": string(caps), "transport": transport, "availability": "online", "connected_at": floatSeconds(now), "last_seen": floatSeconds(now)})
	p.ZAdd(ctx, "agent-index", redis.Z{Score: float64(now.UnixNano()) / 1e9, Member: s.AgentID})
	p.XAdd(ctx, &redis.XAddArgs{Stream: "agent-events", MaxLen: 10000, Approx: true, Values: map[string]any{"agent_id": s.AgentID, "kind": "session", "value": "connected", "transport": transport, "recorded_at": floatSeconds(now)}})
	_, err := p.Exec(ctx)
	return s, err
}

func (b *Backend) check(ctx context.Context, s *Session) error {
	v, err := b.Redis.HGet(ctx, "agent:"+s.AgentID, "session_id").Result()
	if err != nil {
		return err
	}
	if v != s.ID {
		return &Violation{"session-replaced", "a newer session is active"}
	}
	return nil
}
func (b *Backend) Heartbeat(ctx context.Context, s *Session) error {
	if err := b.check(ctx, s); err != nil {
		return err
	}
	now := time.Now()
	p := b.Redis.Pipeline()
	p.HSet(ctx, "agent:"+s.AgentID, "last_seen", floatSeconds(now))
	p.ZAdd(ctx, "agent-index", redis.Z{Score: float64(now.UnixNano()) / 1e9, Member: s.AgentID})
	_, err := p.Exec(ctx)
	return err
}

func (b *Backend) Next(ctx context.Context, s *Session) (*gatewayv1.GatewayMessage, error) {
	if err := b.check(ctx, s); err != nil {
		return nil, err
	}
	stream := "agent-tasks:" + s.AgentID
	claimed, _, err := b.Redis.XAutoClaim(ctx, &redis.XAutoClaimArgs{Stream: stream, Group: "full-agent-gateways", Consumer: s.ID, MinIdle: time.Duration(b.LeaseTimeoutMS) * time.Millisecond, Start: "0-0", Count: 1}).Result()
	if err != nil && err != redis.Nil {
		return nil, err
	}
	var msg redis.XMessage
	if len(claimed) > 0 {
		msg = claimed[0]
	} else {
		rows, e := b.Redis.XReadGroup(ctx, &redis.XReadGroupArgs{Group: "full-agent-gateways", Consumer: s.ID, Streams: []string{stream, ">"}, Count: 1, Block: time.Second}).Result()
		if e == redis.Nil {
			return nil, nil
		}
		if e != nil {
			return nil, e
		}
		msg = rows[0].Messages[0]
	}
	taskID := fmt.Sprint(msg.Values["task_id"])
	payload := fmt.Sprint(msg.Values["payload"])
	now := time.Now()
	p := b.Redis.Pipeline()
	p.HSet(ctx, "task:"+taskID, map[string]any{"status": "assigned", "worker": s.AgentID, "lease_id": msg.ID, "updated_at": floatSeconds(now)})
	p.XAdd(ctx, &redis.XAddArgs{Stream: "task-events", MaxLen: 10000, Approx: true, Values: map[string]any{"task_id": taskID, "status": "assigned", "worker": s.AgentID}})
	if _, err = p.Exec(ctx); err != nil {
		return nil, err
	}
	return &gatewayv1.GatewayMessage{Payload: &gatewayv1.GatewayMessage_Task{Task: &gatewayv1.TaskAssignment{TaskId: taskID, LeaseId: msg.ID, PayloadJson: payload}}}, nil
}

func (b *Backend) Complete(ctx context.Context, s *Session, r *gatewayv1.TaskResult) (*gatewayv1.GatewayMessage, error) {
	if err := b.check(ctx, s); err != nil {
		return nil, err
	}
	if r.GetTaskId() == "" || r.GetLeaseId() == "" {
		return nil, &Violation{"invalid-result", "task_id and lease_id are required"}
	}
	rec, err := b.Redis.HGetAll(ctx, "task:"+r.GetTaskId()).Result()
	if err != nil {
		return nil, err
	}
	ack := func(ok bool, d string) *gatewayv1.GatewayMessage {
		return &gatewayv1.GatewayMessage{Payload: &gatewayv1.GatewayMessage_ResultAck{ResultAck: &gatewayv1.ResultAck{TaskId: r.GetTaskId(), Accepted: ok, Detail: d}}}
	}
	if rec["worker"] != s.AgentID || rec["lease_id"] != r.GetLeaseId() {
		return ack(false, "lease does not belong to this session"), nil
	}
	if rec["status"] == "completed" || rec["status"] == "failed" {
		return ack(true, "already recorded"), nil
	}
	status := ""
	if r.GetStatus() == gatewayv1.TaskStatus_TASK_STATUS_SUCCEEDED {
		status = "completed"
	} else if r.GetStatus() == gatewayv1.TaskStatus_TASK_STATUS_FAILED {
		status = "failed"
	} else {
		return nil, &Violation{"invalid-result", "terminal task status is required"}
	}
	if r.GetResultJson() != "" && !json.Valid([]byte(r.GetResultJson())) {
		return nil, &Violation{"invalid-result", "result_json is not valid JSON"}
	}
	updates := map[string]any{"status": status, "updated_at": floatSeconds(time.Now())}
	if r.GetResultJson() != "" {
		updates["result"] = r.GetResultJson()
	}
	if r.GetError() != "" {
		e := r.GetError()
		if len(e) > 2000 {
			e = e[:2000]
		}
		updates["error"] = e
	}
	p := b.Redis.TxPipeline()
	p.HSet(ctx, "task:"+r.GetTaskId(), updates)
	p.XAdd(ctx, &redis.XAddArgs{Stream: "task-events", MaxLen: 10000, Approx: true, Values: map[string]any{"task_id": r.GetTaskId(), "status": status, "worker": s.AgentID}})
	p.XAck(ctx, "agent-tasks:"+s.AgentID, "full-agent-gateways", r.GetLeaseId())
	_, err = p.Exec(ctx)
	return ack(true, "recorded"), err
}
func (b *Backend) Disconnect(ctx context.Context, s *Session) {
	if b.check(ctx, s) != nil {
		return
	}
	now := time.Now()
	p := b.Redis.Pipeline()
	p.HSet(ctx, "agent:"+s.AgentID, map[string]any{"availability": "offline", "last_seen": floatSeconds(now)})
	p.XAdd(ctx, &redis.XAddArgs{Stream: "agent-events", MaxLen: 10000, Approx: true, Values: map[string]any{"agent_id": s.AgentID, "kind": "session", "value": "disconnected", "transport": s.Transport, "recorded_at": floatSeconds(now)}})
	_, _ = p.Exec(ctx)
}
func floatSeconds(t time.Time) string {
	return strconv.FormatFloat(float64(t.UnixNano())/1e9, 'f', -1, 64)
}
func stringsContains(s, sub string) bool {
	return regexp.MustCompile(regexp.QuoteMeta(sub)).FindStringIndex(s) != nil
}
