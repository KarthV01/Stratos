# Survey reconstruction and live sensor mapping

Stratos supports two map-producing device paths without requiring constrained
hardware to handle photographs or JSON.

```text
full agents -- survey JPEG/TIFF batches --> mapping service --> NodeODM
                                                        \--> orthophoto GeoTIFF

embedded agents -- SMP/1 sensor frames --> gateway --> normalized observations
full agents ----- SAP/1 sensor values ----/                  \--> GeoJSON API
```

## Full-agent survey workflow

A full agent advertises `survey.capture` when it can acquire survey images and
`imagery.upload` when it can use the bulk upload API. Photographs never travel
through MAVLink, MQTT, Redis, or the MJPEG camera endpoint.

1. Create a survey with `POST /api/surveys` on the mapping service.
2. Each drone uploads JPEG or TIFF batches to
   `POST /api/surveys/{survey_id}/images`. The multipart form contains
   `agent_id`, `vehicle_id`, optional `sector_id`, comma-separated stable
   `image_ids`, and matching `files` entries.
3. Finalize collection with `POST /api/surveys/{survey_id}/process`.
4. Poll `GET /api/surveys/{survey_id}` or its `artifacts` endpoint.
5. Download the completed GeoTIFF from the returned artifact URL.

All drones contributing to one survey upload into one staged NodeODM task.
Stable image IDs make retried batches idempotent. Once processing starts, the
survey no longer accepts images. Stratos resumes status polling after a mapping
service restart and extracts only NodeODM's known orthophoto member from the
result archive.

Mission Planner, PX4, or ArduPilot remains responsible for flight paths and
camera triggers. Stratos records vehicle and sector membership but does not
generate survey grids in this release.

## Geospatial observations

The normalized representation is `stratos.observation/1`. It contains the
device, optional vehicle, sensor name, numeric value, unit, observation time,
fixed-point coordinates, optional altitude, and how the location was obtained.

Embedded devices can publish an atomic location-bearing sample:

```text
SMP1|G|seq|sensor|value|unit|lat_e7|lon_e7|altitude_mm_or_na
```

Example: `SMP1|G|14|temperature|21.5|C|407128001|-740060002|12500`.

Existing `T` scalar frames remain valid. The gateway locates them from a
provisioned fixed device position or a bound vehicle position no more than five
seconds old. Unlocated readings remain ordinary telemetry and are not invented
as map points. Full-agent sensor dictionaries use the flight position in the
same telemetry snapshot.

Provision stationary coordinates with
`PUT /api/devices/{device_id}/location`. Query mapped readings from
`GET /api/map/observations`; the response is a GeoJSON `FeatureCollection` and
supports `bbox=minLon,minLat,maxLon,maxLat`, `since`, `until`, `sensor`,
`device_id`, and `limit` filters.

## Local deployment

Docker Compose runs Redis, the mapping API, and one token-protected NodeODM
worker. NodeODM stores its working data in `nodeodm-data`; extracted artifacts
use `mapping-data`.

```powershell
docker compose up --build
```

The controller is at `http://localhost:8000`, mapping API at
`http://localhost:8010`, and NodeODM at `http://localhost:3000`. The committed
token is development-only and must be replaced outside an isolated local
environment.

For Minikube, start the laptop-side NodeODM container first:

```powershell
docker compose up -d nodeodm
./infra/minikube/start.ps1
```

The in-cluster mapping service uses `host.minikube.internal:3000` and is exposed
to trusted development agents on NodePort `30810`. NodeODM deliberately stays
outside the existing 2 GiB Minikube nodes; photogrammetry should have roughly
16 GiB available for the initial 100--250-image target. The mapping PVC stores
completed GeoTIFFs, while NodeODM retains its own task data. Plan disk cleanup
and backups before using either store for durable field data.

Only one reconstruction is submitted at a time for this first release. ODM
split/merge and browser raster tiling are later scaling steps.
