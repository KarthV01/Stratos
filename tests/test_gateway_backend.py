import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.stratos_gateway.backend import (
    AgentSession,
    GatewayBackend,
    ProtocolViolation,
    validate_hello,
)
from app.stratos_gateway.generated import agent_pb2


def full_agent_hello(**overrides):
    values = {
        "protocol_version": "sagp/1",
        "agent_id": "linux-01",
        "board": "raspberry-pi/5",
        "agent_version": "1.0.0",
        "capabilities": ["python", "shell"],
    }
    values.update(overrides)
    return agent_pb2.AgentHello(**values)


class GatewayValidationTests(unittest.TestCase):
    def test_known_full_agent_is_accepted(self) -> None:
        validate_hello(full_agent_hello(), None)

    def test_embedded_board_is_rejected(self) -> None:
        with self.assertRaisesRegex(ProtocolViolation, "Full Agent"):
            validate_hello(full_agent_hello(board="esp32-s3"), None)

    def test_configured_token_is_required(self) -> None:
        with self.assertRaises(ProtocolViolation) as caught:
            validate_hello(full_agent_hello(token="wrong"), "secret")
        self.assertEqual("unauthenticated", caught.exception.code)


class GatewayBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = MagicMock()
        for method in ("hget", "hgetall", "hset", "xadd", "xgroup_create", "zadd"):
            setattr(self.redis, method, AsyncMock())
        self.pipeline = MagicMock()
        self.pipeline.execute = AsyncMock()
        pipeline_context = MagicMock()
        pipeline_context.__aenter__ = AsyncMock(return_value=self.pipeline)
        pipeline_context.__aexit__ = AsyncMock(return_value=None)
        self.redis.pipeline.return_value = pipeline_context
        self.backend = GatewayBackend(self.redis, agent_token=None)

    def test_register_persists_transport_without_token(self) -> None:
        with patch("app.stratos_gateway.backend.time.time", return_value=100.5):
            session = asyncio.run(self.backend.register(full_agent_hello(token="secret"), "tcp"))

        mapping = self.pipeline.hset.call_args.kwargs["mapping"]
        self.assertEqual("linux-01", session.agent_id)
        self.assertEqual("tcp", mapping["transport"])
        self.assertNotIn("token", mapping)

    def test_result_requires_matching_agent_and_lease(self) -> None:
        self.redis.hgetall.return_value = {
            "worker": "another-agent",
            "lease_id": "1-0",
        }
        self.redis.hget.return_value = "session-1"
        session = AgentSession("linux-01", "session-1", "tcp")
        result = agent_pb2.TaskResult(
            task_id="task-1",
            lease_id="1-0",
            status=agent_pb2.TASK_STATUS_SUCCEEDED,
            result_json="{}",
        )

        response = asyncio.run(self.backend.complete(session, result))

        self.assertFalse(response.result_ack.accepted)
        self.pipeline.execute.assert_not_awaited()

    def test_duplicate_terminal_result_is_idempotent(self) -> None:
        self.redis.hget.return_value = "session-1"
        self.redis.hgetall.return_value = {
            "worker": "linux-01",
            "lease_id": "1-0",
            "status": "completed",
        }
        session = AgentSession("linux-01", "session-1", "grpc")
        result = agent_pb2.TaskResult(
            task_id="task-1",
            lease_id="1-0",
            status=agent_pb2.TASK_STATUS_FAILED,
            error="late duplicate",
        )

        response = asyncio.run(self.backend.complete(session, result))

        self.assertTrue(response.result_ack.accepted)
        self.assertEqual("already recorded", response.result_ack.detail)
        self.pipeline.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
