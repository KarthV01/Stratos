import asyncio
import json
import unittest

from app.controller import decode_task, service_info, task_key


class ControllerTests(unittest.TestCase):
    def test_task_key_namespaces_the_id(self) -> None:
        self.assertEqual("task:abc-123", task_key("abc-123"))

    def test_decode_task_restores_structured_fields(self) -> None:
        decoded = decode_task(
            {
                "id": "abc-123",
                "payload": json.dumps({"message": "hello"}),
                "result": json.dumps({"ok": True}),
                "created_at": "10.5",
                "updated_at": "11.25",
            }
        )

        self.assertEqual({"message": "hello"}, decoded["payload"])
        self.assertEqual({"ok": True}, decoded["result"])
        self.assertEqual(10.5, decoded["created_at"])
        self.assertEqual(11.25, decoded["updated_at"])

    def test_root_describes_backend_endpoints(self) -> None:
        info = asyncio.run(service_info())

        self.assertEqual("stratos", info["service"])
        self.assertEqual("/api/tasks", info["tasks"])
        self.assertEqual("/docs", info["docs"])


if __name__ == "__main__":
    unittest.main()
