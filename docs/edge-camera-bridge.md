# Companion-computer camera bridge

Stratos terminates a drone's image stream on its Linux companion computer. The
flight controller and MAVLink link remain responsible for telemetry and camera
commands; ordinary image data does not travel through the telemetry parser or
the Kubernetes control plane.

```text
camera -- USB/CSI/RTSP/RTP --> Raspberry Pi, Jetson, or x86 companion
                                  |-- local HTTP snapshots/MJPEG
flight controller -- MAVLink ----|-- existing Stratos telemetry agent
```

`app.camera_bridge.server` is the local camera boundary. It accepts Linux V4L2
devices and network streams that FFmpeg can read, and exposes:

- `GET /health`
- `GET /v1/cameras`
- `GET /v1/cameras/{camera_id}/snapshot.jpg`
- `GET /v1/cameras/{camera_id}/stream.mjpeg`

It binds to loopback by default. Keep that default until authentication and TLS
are added. If another machine needs the feed now, use an authenticated VPN or
SSH port forward instead of exposing the service to an untrusted network.
Camera credentials are accepted in an RTSP URI but are redacted from the list
endpoint. FFmpeg is invoked with an argument array and never through a shell.

## Run on the secondary computer

Install FFmpeg and make the camera available to the operating system. Copy
`docs/examples/cameras.json`, then run from a Stratos checkout:

```sh
python -m app.camera_bridge.server --config docs/examples/cameras.json
curl http://127.0.0.1:8090/v1/cameras
curl -o frame.jpg http://127.0.0.1:8090/v1/cameras/mapping/snapshot.jpg
```

For a USB UVC camera or CSI camera exposed through V4L2, use `input: "v4l2"`
and an absolute `/dev/video*` path. On current Raspberry Pi OS, a CSI camera can
also be encoded by `rpicam-vid` into a local UDP stream; configure the bridge's
input as `network` with that `udp://` listener. For a gimbal or IP camera, use
`input: "rtsp"`. Network inputs also support HTTP(S), RTP, RTMP, SRT, TCP, and
UDP. A `file` input exists for repeatable development tests and must be an
absolute path.

Each HTTP viewer currently gets a dedicated FFmpeg process. `--max-streams`
bounds concurrent encoders (default 2); this is deliberately a small first
slice, not yet a fleet media server. A later adapter can forward the encoded
feed to a central service without changing how cameras are acquired locally.

## What the target platforms expose

| Target | Can Stratos access a feed? | Integration boundary |
| --- | --- | --- |
| PX4 multirotor, fixed-wing, or VTOL with companion | Yes, when the camera is connected to or network-reachable from the companion. PX4 states that video cannot be streamed directly from a camera connected to PX4. | V4L2/CSI or a network stream into this bridge; MAVLink remains a separate telemetry/control link. |
| ArduPilot multirotor, fixed-wing, or VTOL with companion | Yes, subject to the selected camera's native USB/network interface. ArduPilot documents the companion as the place to consume MAVLink data and preprocess advanced sensors; camera vendors use different feed mechanisms. | V4L2 or camera-native RTSP/RTP into this bridge. ArduPilot camera settings control capture but do not create a generic video transport. |
| MAVLink Camera Protocol v2 camera | Yes if the camera advertises `CAMERA_CAP_FLAGS_HAS_VIDEO_STREAM`; `VIDEO_STREAM_INFORMATION` supplies the transport, encoding, dimensions, rate, and URI. | Configure the advertised URI as a network source. Discovery/control is future work and should not be confused with carrying frames over MAVLink. |
| Custom Raspberry Pi camera | Yes. USB UVC commonly appears as V4L2; CSI uses the Raspberry Pi camera stack and can be adapted to V4L2 or local UDP. | Direct local camera access, which is the primary supported path in this slice. |
| DJI Mavic 3 Enterprise family | Possible, but not as a generic Linux webcam. DJI lists Mavic 3E/3T SDK support and its SDK stream listeners expose encoded stream data; Payload SDK Liveview can provide H.264 or decoded RGB on supported product/connection combinations. | A licensed, model/firmware-specific DJI SDK adapter must emit a standard local stream (for example UDP or RTSP) before this bridge can ingest it. Do not assume direct access from the aircraft camera on arbitrary hardware. |
| WingtraOne | No public, general-purpose live camera API was identified in the reviewed official material. | Treat it as an offline photo/export integration unless Wingtra supplies a supported SDK or network-stream contract for the exact model. |

Airframe shape does not affect this boundary. A quadcopter, fixed-wing, and VTOL
using the same flight controller, companion, and camera can use the same adapter.

## MAVLink integration rule

Camera Protocol v2 discovers and controls cameras and describes streams. It is
not the normal media plane. MAVLink's legacy image-transmission protocol exists,
but the MAVLink project explicitly recommends Camera Protocol and file/media
transports instead; it was designed for small images over low-bandwidth links.
Stratos therefore keeps its existing passive telemetry reader receive-only and
does not overload it with video chunks.

## Official references

- [MAVLink Camera Protocol v2](https://mavlink.io/en/services/camera.html)
- [MAVLink image transmission warning and scope](https://mavlink.io/en/services/image_transmission.html)
- [PX4 companion-computer video streaming](https://docs.px4.io/main/en/companion_computer/video_streaming)
- [PX4 MAVLink camera integration](https://docs.px4.io/main/en/camera/mavlink_v2_camera)
- [ArduPilot companion computers](https://ardupilot.org/dev/docs/companion-computers.html)
- [ArduPilot camera controls](https://ardupilot.org/copter/docs/common-camera-controls.html)
- [DJI Payload SDK Liveview](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/advanced-function/liveview.html)
- [DJI SDK release/product support](https://developer.dji.com/doc/payload-sdk-tutorial/en/)
