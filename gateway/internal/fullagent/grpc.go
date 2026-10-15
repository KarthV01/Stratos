package fullagent

import (
	"context"
	"io"

	gatewayv1 "github.com/KarthV01/Stratos/gateway/gen/stratos/gateway/v1"
	"google.golang.org/grpc"
)

type GRPCServer struct {
	gatewayv1.UnimplementedFullAgentGatewayServer
	Backend *Backend
}

func (g *GRPCServer) Connect(stream grpc.BidiStreamingServer[gatewayv1.AgentMessage, gatewayv1.GatewayMessage]) error {
	ctx, cancel := context.WithCancel(stream.Context())
	defer cancel()
	s := &GatewaySession{Backend: g.Backend, Transport: "grpc"}
	defer s.Close(context.Background())
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	welcome, err := s.Open(ctx, first)
	if err != nil {
		return stream.Send(ErrorMessage(err))
	}
	if err = stream.Send(welcome); err != nil {
		return err
	}
	out := make(chan *gatewayv1.GatewayMessage, 100)
	errs := make(chan error, 2)
	go func() {
		for {
			m, e := stream.Recv()
			if e != nil {
				errs <- e
				return
			}
			reply, e := s.Receive(ctx, m)
			if e != nil {
				out <- ErrorMessage(e)
				errs <- e
				return
			}
			if reply != nil {
				out <- reply
			}
		}
	}()
	go func() {
		for {
			m, e := s.Next(ctx)
			if e != nil {
				errs <- e
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
			if e := stream.Send(m); e != nil {
				return e
			}
			if m.GetError() != nil {
				return nil
			}
		case e := <-errs:
			if e == io.EOF {
				return nil
			}
			return e
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}
