"""Dedicated Stratos survey and orthomosaic service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.broker import redis_client
from app.mapping.nodeodm import NodeODMClient, extract_orthophoto
from app.mqtt_protocol import validate_device_id


SURVEY_INDEX = "survey-index"
ACTIVE_STATUSES = {"queued", "running"}
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff"}


def survey_key(survey_id: str) -> str:
    return f"survey:{survey_id}"


def image_key(survey_id: str, image_id: str) -> str:
    return f"survey-image:{survey_id}:{image_id}"


def decode_survey(record: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = dict(record)
    for field in ("created_at", "updated_at"):
        if field in result:
            result[field] = float(result[field])
    result["image_count"] = int(result.get("image_count", "0"))
    result["vehicles"] = json.loads(result.get("vehicles", "[]"))
    result["sectors"] = json.loads(result.get("sectors", "[]"))
    return result


class SurveyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class MappingRuntime:
    def __init__(self, redis: Redis, odm: NodeODMClient, artifact_root: Path, poll_seconds: float) -> None:
        self.redis = redis
        self.odm = odm
        self.artifact_root = artifact_root
        self.poll_seconds = poll_seconds
        self.stop = asyncio.Event()

    async def run(self) -> None:
        while not self.stop.is_set():
            try:
                await self.poll_active()
            except Exception as exc:
                print(f"mapping poll failed: {exc}", flush=True)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def poll_active(self) -> None:
        ids = await self.redis.zrange(SURVEY_INDEX, 0, -1)
        for survey_id in ids:
            record = await self.redis.hgetall(survey_key(survey_id))
            if record.get("status") not in ACTIVE_STATUSES:
                continue
            try:
                info = await self.odm.info(record["nodeodm_task_id"])
                code = int(info.get("status", {}).get("code", 0))  # type: ignore[union-attr]
                if code in {10, 20}:
                    status = "queued" if code == 10 else "running"
                    await self._update(survey_id, status=status)
                elif code == 40:
                    await self._complete(survey_id, record["nodeodm_task_id"])
                elif code in {30, 50}:
                    await self._update(
                        survey_id,
                        status="failed",
                        error=str(info.get("lastError") or f"NodeODM status {code}"),
                    )
            except Exception as exc:
                await self._update(survey_id, error=str(exc))

    async def _complete(self, survey_id: str, task_id: str) -> None:
        directory = self.artifact_root / survey_id
        archive = directory / "all.zip"
        orthophoto = directory / "orthophoto.tif"
        await self.odm.download_all(task_id, archive)
        await asyncio.to_thread(extract_orthophoto, archive, orthophoto)
        await self._update(
            survey_id,
            status="completed",
            artifact_path=str(orthophoto),
            artifact_size=str(orthophoto.stat().st_size),
            error="",
        )

    async def _update(self, survey_id: str, **values: str) -> None:
        values["updated_at"] = str(time.time())
        await self.redis.hset(survey_key(survey_id), mapping=values)


def create_app(
    *,
    odm: NodeODMClient | None = None,
    artifact_root: str | Path | None = None,
    poll_seconds: float | None = None,
) -> FastAPI:
    odm = odm or NodeODMClient(
        os.getenv("NODEODM_URL", "http://localhost:3000"), os.getenv("NODEODM_TOKEN")
    )
    root = Path(artifact_root or os.getenv("MAPPING_ARTIFACT_DIR", "mapping-data"))
    interval = poll_seconds or float(os.getenv("MAPPING_POLL_SECONDS", "5"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        root.mkdir(parents=True, exist_ok=True)
        redis = redis_client()
        runtime = MappingRuntime(redis, odm, root, interval)
        task = asyncio.create_task(runtime.run())
        app.state.redis = redis
        app.state.runtime = runtime
        try:
            yield
        finally:
            runtime.stop.set()
            await task
            await redis.aclose()

    app = FastAPI(title="Stratos Mapping API", version="1", lifespan=lifespan)

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        await request.app.state.redis.ping()
        return {"status": "ok"}

    @app.post("/api/surveys", status_code=201)
    async def create_survey(body: SurveyRequest, request: Request) -> dict[str, Any]:
        survey_id = f"survey-{uuid4().hex}"
        task_id = str(uuid4())
        try:
            node_task_id = await odm.initialize(body.name, task_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        now = time.time()
        record = {
            "id": survey_id,
            "name": body.name,
            "status": "collecting",
            "nodeodm_task_id": node_task_id,
            "image_count": "0",
            "vehicles": "[]",
            "sectors": "[]",
            "created_at": str(now),
            "updated_at": str(now),
            "error": "",
        }
        redis: Redis = request.app.state.redis
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(survey_key(survey_id), mapping=record)
            pipe.zadd(SURVEY_INDEX, {survey_id: now})
            await pipe.execute()
        return decode_survey(record)

    @app.get("/api/surveys")
    async def list_surveys(
        request: Request, limit: int = Query(default=100, ge=1, le=500)
    ) -> list[dict[str, Any]]:
        redis: Redis = request.app.state.redis
        ids = await redis.zrevrange(SURVEY_INDEX, 0, limit - 1)
        async with redis.pipeline(transaction=False) as pipe:
            for survey_id in ids:
                pipe.hgetall(survey_key(survey_id))
            rows = await pipe.execute()
        return [decode_survey(row) for row in rows if row]

    @app.get("/api/surveys/{survey_id}")
    async def get_survey(survey_id: str, request: Request) -> dict[str, Any]:
        record = await request.app.state.redis.hgetall(survey_key(survey_id))
        if not record:
            raise HTTPException(status_code=404, detail="Survey not found")
        return decode_survey(record)

    @app.post("/api/surveys/{survey_id}/images", status_code=202)
    async def upload_images(
        survey_id: str,
        request: Request,
        agent_id: Annotated[str, Form()],
        vehicle_id: Annotated[str, Form()],
        image_ids: Annotated[str, Form(description="Comma-separated stable IDs")],
        files: Annotated[list[UploadFile], File()],
        sector_id: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        try:
            validate_device_id(agent_id)
            validate_device_id(vehicle_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        redis: Redis = request.app.state.redis
        survey = await redis.hgetall(survey_key(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        if survey["status"] != "collecting":
            raise HTTPException(status_code=409, detail="Survey is no longer accepting images")
        agent = await redis.hgetall(f"device:{agent_id}")
        if agent.get("tier") != "1":
            raise HTTPException(status_code=403, detail="Imagery uploads require a full agent")
        try:
            capabilities = set(json.loads(agent.get("capabilities", "[]")))
        except json.JSONDecodeError:
            capabilities = set()
        if "imagery.upload" not in capabilities:
            raise HTTPException(status_code=403, detail="Agent lacks imagery.upload capability")
        ids = [item.strip() for item in image_ids.split(",") if item.strip()]
        if not 1 <= len(files) <= 100:
            raise HTTPException(status_code=422, detail="Upload between 1 and 100 images per batch")
        if len(ids) != len(files) or len(set(ids)) != len(ids):
            raise HTTPException(status_code=422, detail="Provide one unique image ID per file")
        for image_id, upload in zip(ids, files):
            if not 1 <= len(image_id) <= 64 or not image_id.replace("-", "").replace("_", "").isalnum():
                raise HTTPException(status_code=422, detail="Invalid image ID")
            if Path(upload.filename or "").suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
                raise HTTPException(status_code=415, detail="Only JPEG and TIFF images are supported")
        selected: list[tuple[str, UploadFile]] = []
        for image_id, upload in zip(ids, files):
            if await redis.hsetnx(image_key(survey_id, image_id), "reserved", "1"):
                selected.append((image_id, upload))
        if selected:
            try:
                await odm.upload(
                    survey["nodeodm_task_id"],
                    [
                        (upload.filename or image_id, upload.file, upload.content_type or "application/octet-stream")
                        for image_id, upload in selected
                    ],
                )
            except Exception as exc:
                await redis.delete(
                    *(image_key(survey_id, image_id) for image_id, _ in selected)
                )
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            vehicles = set(json.loads(survey.get("vehicles", "[]")))
            sectors = set(json.loads(survey.get("sectors", "[]")))
            vehicles.add(vehicle_id)
            if sector_id:
                sectors.add(sector_id)
            async with redis.pipeline(transaction=True) as pipe:
                for image_id, upload in selected:
                    pipe.hset(
                        image_key(survey_id, image_id),
                        mapping={
                            "reserved": "0",
                            "image_id": image_id,
                            "filename": upload.filename or image_id,
                            "agent_id": agent_id,
                            "vehicle_id": vehicle_id,
                            "sector_id": sector_id or "",
                        },
                    )
                pipe.hincrby(survey_key(survey_id), "image_count", len(selected))
                pipe.hset(
                    survey_key(survey_id),
                    mapping={
                        "vehicles": json.dumps(sorted(vehicles)),
                        "sectors": json.dumps(sorted(sectors)),
                        "updated_at": str(time.time()),
                    },
                )
                await pipe.execute()
        return {"survey_id": survey_id, "accepted": len(selected), "duplicates": len(files) - len(selected)}

    @app.post("/api/surveys/{survey_id}/process", status_code=202)
    async def process_survey(survey_id: str, request: Request) -> dict[str, Any]:
        redis: Redis = request.app.state.redis
        survey = await redis.hgetall(survey_key(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        if survey["status"] != "collecting":
            raise HTTPException(status_code=409, detail="Survey has already been finalized")
        if int(survey.get("image_count", "0")) == 0:
            raise HTTPException(status_code=409, detail="Survey has no images")
        try:
            await odm.commit(survey["nodeodm_task_id"])
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await redis.hset(
            survey_key(survey_id),
            mapping={"status": "queued", "updated_at": str(time.time()), "error": ""},
        )
        return {"survey_id": survey_id, "status": "queued"}

    @app.get("/api/surveys/{survey_id}/artifacts")
    async def survey_artifacts(survey_id: str, request: Request) -> dict[str, Any]:
        survey = await request.app.state.redis.hgetall(survey_key(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        if survey.get("status") != "completed":
            return {"survey_id": survey_id, "status": survey.get("status"), "artifacts": []}
        return {
            "survey_id": survey_id,
            "status": "completed",
            "artifacts": [
                {
                    "type": "orthophoto",
                    "format": "image/tiff; application=geotiff",
                    "size": int(survey["artifact_size"]),
                    "url": f"/api/surveys/{survey_id}/orthophoto.tif",
                }
            ],
        }

    @app.get("/api/surveys/{survey_id}/orthophoto.tif")
    async def download_orthophoto(survey_id: str, request: Request) -> FileResponse:
        survey = await request.app.state.redis.hgetall(survey_key(survey_id))
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        path = Path(survey.get("artifact_path", ""))
        if survey.get("status") != "completed" or not path.is_file():
            raise HTTPException(status_code=409, detail="Orthophoto is not ready")
        return FileResponse(path, media_type="image/tiff", filename=f"{survey_id}-orthophoto.tif")

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stratos mapping service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run("app.mapping.server:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
