import asyncio
import json
import unittest

from app.full_agent import FullAgent, FullAgentConfig
from app.stratos_gateway.generated import agent_pb2


class FullAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        async def handler(payload):
            return {"reply": payload["message"]}

        self.agent = FullAgent(
            FullAgentConfig(
                agent_id="linux-01",
                board="raspberry-pi/5",
                agent_version="1.0.0",
                host="gateway",
                port=7443,
            ),
            handler,
        )

    def test_hello_declares_protocol_and_capacity(self) -> None:
        hello = self.agent.hello().hello

        self.assertEqual("sagp/1", hello.protocol_version)
        self.assertEqual(1, hello.max_concurrent_tasks)

    def test_successful_task_returns_same_lease(self) -> None:
        assignment = agent_pb2.TaskAssignment(
            task_id="task-1",
            lease_id="12-0",
            payload_json=json.dumps({"message": "hello"}),
        )

        result = asyncio.run(self.agent._execute(assignment)).task_result

        self.assertEqual(agent_pb2.TASK_STATUS_SUCCEEDED, result.status)
        self.assertEqual("12-0", result.lease_id)
        self.assertEqual({"reply": "hello"}, json.loads(result.result_json))

    def test_handler_failure_returns_failed_result(self) -> None:
        async def failing_handler(payload):
            raise RuntimeError("boom")

        self.agent.handler = failing_handler
        assignment = agent_pb2.TaskAssignment(
            task_id="task-1", lease_id="12-0", payload_json="{}"
        )

        result = asyncio.run(self.agent._execute(assignment)).task_result

        self.assertEqual(agent_pb2.TASK_STATUS_FAILED, result.status)
        self.assertEqual("boom", result.error)


if __name__ == "__main__":
    unittest.main()
