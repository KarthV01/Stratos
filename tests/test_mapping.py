import asyncio
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

from app.mapping.nodeodm import NodeODMError, extract_orthophoto
from app.mapping.server import MappingRuntime, decode_survey


class OrthophotoExtractionTests(unittest.TestCase):
    def test_extracts_only_known_orthophoto_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "all.zip"
            destination = root / "artifacts" / "orthophoto.tif"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("project/odm_orthophoto/odm_orthophoto.tif", b"TIFF")
                output.writestr("../../outside.txt", b"unsafe")
            extract_orthophoto(archive, destination)
            self.assertEqual(b"TIFF", destination.read_bytes())
            self.assertFalse((root.parent / "outside.txt").exists())

    def test_missing_orthophoto_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "all.zip")
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("report.json", "{}")
            with self.assertRaises(NodeODMError):
                extract_orthophoto(archive, Path(directory, "orthophoto.tif"))


class MappingRuntimeTests(unittest.TestCase):
    def test_completed_task_downloads_and_records_artifact(self) -> None:
        async def exercise(root: Path) -> None:
            redis = AsyncMock()
            redis.zrange.return_value = ["survey-1"]
            redis.hgetall.return_value = {
                "status": "running",
                "nodeodm_task_id": "task-1",
            }
            odm = AsyncMock()
            odm.info.return_value = {"status": {"code": 40}}

            async def download(_task_id: str, destination: Path) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(destination, "w") as output:
                    output.writestr("odm_orthophoto/odm_orthophoto.tif", b"TIFF")

            odm.download_all.side_effect = download
            runtime = MappingRuntime(redis, odm, root, 1)
            await runtime.poll_active()
            mapping = redis.hset.await_args.kwargs["mapping"]
            self.assertEqual("completed", mapping["status"])
            self.assertTrue(Path(mapping["artifact_path"]).is_file())

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(exercise(Path(directory)))

    def test_survey_decoder_restores_types(self) -> None:
        decoded = decode_survey(
            {
                "id": "survey-1",
                "image_count": "2",
                "vehicles": '["drone-1"]',
                "sectors": '["north"]',
                "created_at": "1.5",
                "updated_at": "2.5",
            }
        )
        self.assertEqual(2, decoded["image_count"])
        self.assertEqual(["drone-1"], decoded["vehicles"])
        self.assertEqual(1.5, decoded["created_at"])


if __name__ == "__main__":
    unittest.main()
