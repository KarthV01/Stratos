import asyncio
import unittest

from app.handler import handle_task


class HandlerTests(unittest.TestCase):
    def test_handler_returns_worker_and_reply(self) -> None:
        result = asyncio.run(
            handle_task({"message": "hello", "delay_seconds": 0}, "worker-test")
        )

        self.assertEqual("worker-test", result["worker"])
        self.assertEqual("worker-test processed: hello", result["reply"])


if __name__ == "__main__":
    unittest.main()
