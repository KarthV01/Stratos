package main

import (
	"context"
	"errors"
	"log"
	"net"
	"os/signal"
	"syscall"

	gatewayv1 "github.com/KarthV01/Stratos/gateway/gen/stratos/gateway/v1"
	"github.com/KarthV01/Stratos/gateway/internal/config"
	"github.com/KarthV01/Stratos/gateway/internal/fullagent"
	"github.com/redis/go-redis/v9"
	"google.golang.org/grpc"
)

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
func run() error {
	cfg := config.Load()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	opts, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		return err
	}
	rdb := redis.NewClient(opts)
	defer rdb.Close()
	if err = rdb.Ping(ctx).Err(); err != nil {
		return err
	}
	backend := &fullagent.Backend{Redis: rdb, Token: cfg.AgentToken, HeartbeatSeconds: cfg.HeartbeatSeconds, LeaseTimeoutMS: cfg.LeaseTimeoutMS}
	tcp, err := net.Listen("tcp", cfg.TCPAddr)
	if err != nil {
		return err
	}
	grpcListener, err := net.Listen("tcp", cfg.GRPCAddr)
	if err != nil {
		return err
	}
	server := grpc.NewServer(grpc.MaxRecvMsgSize(1024*1024), grpc.MaxSendMsgSize(1024*1024))
	gatewayv1.RegisterFullAgentGatewayServer(server, &fullagent.GRPCServer{Backend: backend})
	errs := make(chan error, 2)
	go func() { errs <- fullagent.ServeTCP(ctx, tcp, backend) }()
	go func() { errs <- server.Serve(grpcListener) }()
	log.Printf("unified gateway: SAGP TCP %s, gRPC %s", cfg.TCPAddr, cfg.GRPCAddr)
	select {
	case <-ctx.Done():
		server.GracefulStop()
		return nil
	case err := <-errs:
		if errors.Is(err, net.ErrClosed) {
			return nil
		}
		return err
	}
}
