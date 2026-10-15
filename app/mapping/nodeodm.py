"""Small asynchronous client for the NodeODM staged-task API."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import BinaryIO

import httpx


class NodeODMError(RuntimeError):
    pass


class NodeODMClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _params(self) -> dict[str, str]:
        return {"token": self.token} if self.token else {}

    async def initialize(self, name: str, task_id: str) -> str:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post(
                "/task/new/init",
                params=self._params(),
                headers={"set-uuid": task_id},
                data={"name": name},
            )
        self._raise(response)
        return str(response.json()["uuid"])

    async def upload(self, task_id: str, files: list[tuple[str, BinaryIO, str]]) -> None:
        multipart = [("images", (name, stream, content_type)) for name, stream, content_type in files]
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post(
                f"/task/new/upload/{task_id}", params=self._params(), files=multipart
            )
        self._raise(response)

    async def commit(self, task_id: str) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post(
                f"/task/new/commit/{task_id}", params=self._params()
            )
        self._raise(response)

    async def info(self, task_id: str) -> dict[str, object]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.get(f"/task/{task_id}/info", params=self._params())
        self._raise(response)
        return response.json()

    async def download_all(self, task_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            async with client.stream(
                "GET", f"/task/{task_id}/download/all.zip", params=self._params()
            ) as response:
                self._raise(response)
                with temporary.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
        os.replace(temporary, destination)

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        detail = response.text[-500:]
        raise NodeODMError(f"NodeODM returned HTTP {response.status_code}: {detail}")


def extract_orthophoto(archive: Path, destination: Path) -> None:
    """Extract only ODM's known orthophoto member without trusting ZIP paths."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with zipfile.ZipFile(archive) as source:
        matches = [
            item
            for item in source.infolist()
            if item.filename.replace("\\", "/").endswith(
                "odm_orthophoto/odm_orthophoto.tif"
            )
            and not item.is_dir()
        ]
        if len(matches) != 1:
            raise NodeODMError("NodeODM result does not contain one orthophoto GeoTIFF")
        member = matches[0]
        if member.file_size > 50 * 1024 * 1024 * 1024:
            raise NodeODMError("Orthophoto exceeds the 50 GiB extraction limit")
        with source.open(member) as input_file, temporary.open("wb") as output:
            while chunk := input_file.read(1024 * 1024):
                output.write(chunk)
    os.replace(temporary, destination)
