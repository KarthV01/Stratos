# MQTT device integration plan

## Architecture

```text
Tier 2 / Tier 3 device
        |  Wi-Fi + MQTT 3.1.1 (SMP/1)
        v
Mosquitto Service (persistent session and QoS state)
        |
        v
Stratos MQTT bridge <-> Redis Streams <-> Controller API
```

Devices never receive Kubernetes credentials and never connect to Redis. The
bridge is the trust boundary and protocol adapter.

## Delivery milestones

- [x] Define compact topics, frames, limits, lifecycle, and error behavior.
- [x] Build Python codecs and an allocation-free C99 firmware codec.
- [x] Validate device identity and declared tier against the board directory.
- [x] Store inventory, availability, state, bounded telemetry, and events.
- [x] Add durable, idempotent downlink commands and correlated ACK handling.
- [x] Deploy Mosquitto and the bridge in Compose and local Kubernetes.
- [ ] Replace the development listener with TLS and per-device broker ACLs.
- [ ] Add enrollment, credential rotation, revocation, and audit APIs.
- [ ] Add stale-session claiming and dead-letter handling for bridge commands.
- [ ] Add firmware examples for each selected Wi-Fi/MQTT library and board.
- [ ] Load-test reconnect storms, retained LWT traffic, and telemetry limits.

## Rollout

1. Start with one ESP32 (Tier 2) and one constrained Wi-Fi Arduino or ESP8266
   (Tier 3) in a development namespace.
2. Verify hello/welcome, LWT, telemetry, command deduplication, and reconnects.
3. Add TLS and unique credentials before allowing traffic outside the lab LAN.
4. Canary new firmware protocol changes. SMP major versions use a new topic
   prefix and run beside the old bridge during migration.
5. Set per-fleet telemetry quotas, alert on rejects/stale devices, then expand.

## Minikube connection

Build and apply the existing Stratos image and manifests, then find the node IP:

```powershell
minikube image load stratos:dev
kubectl apply -k k8s
minikube ip
```

Development devices connect to the returned IP on port `31883`. Docker Compose
devices connect to the host on port `1883`. The committed listeners are
anonymous for local development and must not be exposed to an untrusted network.
