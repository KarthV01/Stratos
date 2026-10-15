package fullagent

import (
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"sync"
	"time"

	gatewayv1 "github.com/KarthV01/Stratos/gateway/gen/stratos/gateway/v1"
	"google.golang.org/protobuf/proto"
)

const maxFrame = 1024 * 1024

func ServeTCP(ctx context.Context, l net.Listener, b *Backend) error {
	go func() { <-ctx.Done(); _ = l.Close() }()
	for {
		c, err := l.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		go handleTCP(ctx, c, b)
	}
}
func handleTCP(parent context.Context, c net.Conn, b *Backend) {
	defer c.Close()
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	_ = c.SetReadDeadline(time.Now().Add(10 * time.Second))
	first, err := readAgent(c)
	if err != nil {
		return
	}
	_ = c.SetReadDeadline(time.Time{})
	s := &GatewaySession{Backend: b, Transport: "tcp"}
	defer s.Close(context.Background())
	welcome, err := s.Open(ctx, first)
	if err != nil {
		_ = writeGateway(c, ErrorMessage(err))
		return
	}
	if writeGateway(c, welcome) != nil {
		return
	}
	out := make(chan *gatewayv1.GatewayMessage, 100)
	var once sync.Once
	stop := func() { once.Do(cancel) }
	go func() {
		defer stop()
		for {
			m, e := readAgent(c)
			if e != nil {
				return
			}
			reply, e := s.Receive(ctx, m)
			if e != nil {
				out <- ErrorMessage(e)
				return
			}
			if reply != nil {
				out <- reply
			}
		}
	}()
	go func() {
		defer stop()
		for {
			m, e := s.Next(ctx)
			if e != nil {
				return
			}
			if m != nil {
				out <- m
			}
		}
	}()
	for {
		select {
		case m := <-out:
			if writeGateway(c, m) != nil {
				return
			}
			if m.GetError() != nil {
				return
			}
		case <-ctx.Done():
			return
		}
	}
}
func readAgent(r io.Reader) (*gatewayv1.AgentMessage, error) {
	var h [4]byte
	if _, e := io.ReadFull(r, h[:]); e != nil {
		return nil, e
	}
	n := binary.BigEndian.Uint32(h[:])
	if n == 0 || n > maxFrame {
		return nil, fmt.Errorf("invalid frame length")
	}
	p := make([]byte, n)
	if _, e := io.ReadFull(r, p); e != nil {
		return nil, e
	}
	m := new(gatewayv1.AgentMessage)
	return m, proto.Unmarshal(p, m)
}
func writeGateway(w io.Writer, m *gatewayv1.GatewayMessage) error {
	p, e := proto.Marshal(m)
	if e != nil {
		return e
	}
	var h [4]byte
	binary.BigEndian.PutUint32(h[:], uint32(len(p)))
	if _, e = w.Write(h[:]); e != nil {
		return e
	}
	_, e = w.Write(p)
	return e
}
