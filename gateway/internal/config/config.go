package config

import (
	"os"
	"strconv"
)

type Config struct {
	RedisURL, MQTTBroker, MQTTUsername, MQTTPassword            string
	TCPAddr, GRPCAddr, AgentToken, TLSCert, TLSKey, TLSClientCA string
	MQTTClientID                                                string
	MQTTTLS                                                     bool
	HeartbeatSeconds, LeaseTimeoutMS                            int
}

func Load() Config {
	return Config{
		RedisURL:     env("REDIS_URL", "redis://localhost:6379/0"),
		MQTTBroker:   "tcp://" + env("MQTT_HOST", "localhost") + ":" + env("MQTT_PORT", "1883"),
		MQTTUsername: os.Getenv("MQTT_USERNAME"), MQTTPassword: os.Getenv("MQTT_PASSWORD"),
		TCPAddr:    env("GATEWAY_TCP_HOST", "0.0.0.0") + ":" + env("GATEWAY_TCP_PORT", "7442"),
		GRPCAddr:   env("GATEWAY_GRPC_HOST", "0.0.0.0") + ":" + env("GATEWAY_GRPC_PORT", "7443"),
		AgentToken: os.Getenv("GATEWAY_AGENT_TOKEN"), TLSCert: os.Getenv("GATEWAY_TLS_CERT"),
		TLSKey: os.Getenv("GATEWAY_TLS_KEY"), TLSClientCA: os.Getenv("GATEWAY_TLS_CLIENT_CA"),
		MQTTClientID:     env("MQTT_BRIDGE_ID", "stratos-go-gateway"),
		MQTTTLS:          envBool("MQTT_TLS", false),
		HeartbeatSeconds: envInt("GATEWAY_HEARTBEAT_SECONDS", 20), LeaseTimeoutMS: envInt("GATEWAY_LEASE_TIMEOUT_MS", 30000),
	}
}
func envBool(key string, fallback bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(v)
	if err != nil {
		return fallback
	}
	return parsed
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
func envInt(key string, fallback int) int {
	v, err := strconv.Atoi(os.Getenv(key))
	if err == nil {
		return v
	}
	return fallback
}
