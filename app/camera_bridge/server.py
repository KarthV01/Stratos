"""Local HTTP camera service intended to run on a drone companion computer."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.camera_bridge.config import CameraSource, load_camera_sources
from app.camera_bridge.ffmpeg import capture_jpeg, stream_mjpeg


def create_app(
    sources: tuple[CameraSource, ...], *, ffmpeg: str = "ffmpeg", max_streams: int = 2
) -> FastAPI:
    cameras = {source.camera_id: source for source in sources}
    slots = asyncio.Semaphore(max_streams)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ffmpeg_available = shutil.which(ffmpeg) is not None
        yield

    app = FastAPI(title="Stratos Edge Camera Bridge", version="1", lifespan=lifespan)

    def camera(camera_id: str) -> CameraSource:
        source = cameras.get(camera_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        return source

    def require_ffmpeg() -> None:
        if not app.state.ffmpeg_available:
            raise HTTPException(status_code=503, detail="FFmpeg is not installed")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok" if app.state.ffmpeg_available else "degraded",
            "ffmpeg": app.state.ffmpeg_available,
            "cameras": len(cameras),
        }

    @app.get("/v1/cameras")
    async def list_cameras() -> dict[str, object]:
        return {"schema": "stratos.camera/1", "cameras": [item.as_public_dict() for item in cameras.values()]}

    @app.get("/v1/cameras/{camera_id}/snapshot.jpg")
    async def snapshot(camera_id: str) -> Response:
        require_ffmpeg()
        try:
            image = await capture_jpeg(camera(camera_id), ffmpeg)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/v1/cameras/{camera_id}/stream.mjpeg")
    async def stream(camera_id: str) -> StreamingResponse:
        require_ffmpeg()
        source = camera(camera_id)

        async def limited_stream():
            async with slots:
                async for chunk in stream_mjpeg(source, ffmpeg):
                    yield chunk

        return StreamingResponse(
            limited_stream(),
            media_type="multipart/x-mixed-replace;boundary=ffmpeg",
            headers={"Cache-Control": "no-store"},
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Stratos companion camera bridge")
    parser.add_argument("--config", default=os.getenv("STRATOS_CAMERA_CONFIG"), required=os.getenv("STRATOS_CAMERA_CONFIG") is None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--ffmpeg", default=os.getenv("STRATOS_FFMPEG", "ffmpeg"))
    parser.add_argument("--max-streams", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.max_streams <= 32:
        parser.error("--max-streams must be between 1 and 32")
    uvicorn.run(create_app(load_camera_sources(args.config), ffmpeg=args.ffmpeg, max_streams=args.max_streams), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
