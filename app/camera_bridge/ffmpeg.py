"""Safe, shell-free FFmpeg processes for local camera access."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.camera_bridge.config import CameraSource


def input_arguments(source: CameraSource) -> list[str]:
    args: list[str] = []
    if source.input == "v4l2":
        args.extend(["-f", "v4l2"])
        if source.frame_rate is not None:
            args.extend(["-framerate", str(source.frame_rate)])
        if source.width is not None and source.height is not None:
            args.extend(["-video_size", f"{source.width}x{source.height}"])
    elif source.input == "rtsp":
        args.extend(["-rtsp_transport", "tcp"])
    elif source.input == "file":
        args.append("-re")
    args.extend(["-i", source.uri])
    return args


def stream_command(source: CameraSource, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        *input_arguments(source),
        "-an", "-c:v", "mjpeg", "-q:v", "5", "-f", "mpjpeg", "pipe:1",
    ]


def snapshot_command(source: CameraSource, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        *input_arguments(source),
        "-an", "-frames:v", "1", "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1",
    ]


async def stream_mjpeg(source: CameraSource, ffmpeg: str) -> AsyncIterator[bytes]:
    process = await asyncio.create_subprocess_exec(
        *stream_command(source, ffmpeg),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        while chunk := await process.stdout.read(64 * 1024):
            yield chunk
        code = await process.wait()
        if code:
            assert process.stderr is not None
            detail = (await process.stderr.read()).decode("utf-8", "replace")[-500:]
            raise RuntimeError(f"camera process exited with {code}: {detail}")
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()


async def capture_jpeg(source: CameraSource, ffmpeg: str, timeout: float = 10) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *snapshot_command(source, ffmpeg),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("camera snapshot timed out") from None
    if process.returncode:
        detail = stderr.decode("utf-8", "replace")[-500:]
        raise RuntimeError(f"camera process exited with {process.returncode}: {detail}")
    if not stdout.startswith(b"\xff\xd8") or not stdout.endswith(b"\xff\xd9"):
        raise RuntimeError("camera returned an invalid JPEG image")
    return stdout
