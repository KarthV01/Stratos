package main

import (
	"context"
	"crypto/tls"
	"errors"
	"log"
	"net"
	"os/signal"
	"syscall"

	gatewayv1 "github.com/KarthV01/Stratos/gateway/gen/stratos/gateway/v1"
	"github.com/KarthV01/Stratos/gateway/internal/config"
	"github.com/KarthV01/Stratos/gateway/internal/fullagent"
	"github.com/KarthV01/Stratos/gateway/internal/mqttadapter"
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
	tlsConfig, err := fullagent.TLSConfig(cfg.TLSCert, cfg.TLSKey, cfg.TLSClientCA)
	if err != nil {
		return err
	}
	tcp, err := net.Listen("tcp", cfg.TCPAddr)
	if err != nil {
		return err
	}
	grpcListener, err := net.Listen("tcp", cfg.GRPCAddr)
	if err != nil {
		return err
	}
	if tlsConfig != nil {
		tcp = tls.NewListener(tcp, tlsConfig.Clone())
	}
	grpcOptions := []grpc.ServerOption{grpc.MaxRecvMsgSize(1024 * 1024), grpc.MaxSendMsgSize(1024 * 1024)}
	if tlsConfig != nil {
		grpcOptions = append(grpcOptions, grpc.Creds(fullagent.GRPCCredentials(tlsConfig)))
	}
	server := grpc.NewServer(grpcOptions...)
	gatewayv1.RegisterFullAgentGatewayServer(server, &fullagent.GRPCServer{Backend: backend})
	mqttBridge := mqttadapter.New(rdb, cfg.MQTTBroker, cfg.MQTTUsername, cfg.MQTTPassword, cfg.MQTTClientID, cfg.HeartbeatSeconds, cfg.MQTTTLS)
	errs := make(chan error, 3)
	go func() { errs <- fullagent.ServeTCP(ctx, tcp, backend) }()
	go func() { errs <- server.Serve(grpcListener) }()
	go func() { errs <- mqttBridge.Run(ctx) }()
	log.Printf("unified gateway: SAGP TCP %s, gRPC %s; SMP/1 MQTT %s", cfg.TCPAddr, cfg.GRPCAddr, cfg.MQTTBroker)
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
