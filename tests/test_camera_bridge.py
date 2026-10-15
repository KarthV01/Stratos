import json
import tempfile
import unittest
from pathlib import Path

from app.camera_bridge.config import CameraSource, load_camera_sources
from app.camera_bridge.ffmpeg import snapshot_command, stream_command


class CameraBridgeTests(unittest.TestCase):
    def test_loads_multiple_sources_and_redacts_rtsp_credentials(self) -> None:
        config = {
            "cameras": [
                {"camera_id": "survey", "input": "v4l2", "uri": "/dev/video0", "vehicle_id": "drone-1", "width": 1920, "height": 1080},
                {"camera_id": "gimbal", "input": "rtsp", "uri": "rtsp://pilot:secret@10.0.0.8:8554/live"},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "cameras.json")
            path.write_text(json.dumps(config), encoding="utf-8")
            sources = load_camera_sources(path)
        self.assertEqual(("survey", "gimbal"), tuple(item.camera_id for item in sources))
        self.assertEqual("rtsp://10.0.0.8:8554/live", sources[1].public_uri())

    def test_rejects_duplicate_ids_and_invalid_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "v4l2"):
            CameraSource.from_dict({"camera_id": "cam", "input": "v4l2", "uri": "video0"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "cameras.json")
            path.write_text(json.dumps({"cameras": [{"camera_id": "cam", "input": "file", "uri": "C:/one.mp4"}, {"camera_id": "cam", "input": "file", "uri": "C:/two.mp4"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                load_camera_sources(path)

    def test_ffmpeg_commands_are_argument_arrays_not_shell_strings(self) -> None:
        source = CameraSource.from_dict({"camera_id": "cam", "input": "rtsp", "uri": "rtsp://camera/live"})
        stream = stream_command(source, "/usr/bin/ffmpeg")
        snapshot = snapshot_command(source)
        self.assertEqual("/usr/bin/ffmpeg", stream[0])
        self.assertIn("-rtsp_transport", stream)
        self.assertEqual("mpjpeg", stream[stream.index("-f") + 1 if stream.index("-f") > stream.index("-i") else -1])
        self.assertIn("image2pipe", snapshot)


if __name__ == "__main__":
    unittest.main()
