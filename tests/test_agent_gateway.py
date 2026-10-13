import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent_gateway import (
    ingest_full_telemetry,
    poll_full_commands,
    register_full_agent,
)
from app.controller import DeviceCommandRequest, create_device_command
from app.tier_payloads import (
    AgentResources,
    ComputeState,
    FlightState,
    FullAgentHello,
    FullTelemetry,
    LinkState,
    PowerState,
)


def request_with(redis):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))


def telemetry(sequence: int = 2) -> FullTelemetry:
    return FullTelemetry(
        agent_id="drone-full-1",
        sequence=sequence,
        sent_at_ms=1_700_000_001_000,
        uptime_ms=40_000,
        flight=FlightState(armed=True, mode="AUTO", system_status="active"),
        power=PowerState(voltage_mv=22_400, remaining_pct=76),
        link=LinkState(quality_pct=90),
        compute=ComputeState(cpu_pct=20, memory_used_mb=500, disk_used_mb=1000),
    )


class FullAgentGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = AsyncMock()
        self.request = request_with(self.redis)

    def test_registers_known_full_agent_in_shared_inventory(self) -> None:
        hello = FullAgentHello(
            agent_id="drone-full-1",
            sequence=1,
            sent_at_ms=1_700_000_000_000,
            board="jetson orin nano",
            firmware="2.4.1",
            boot_id="boot-1",
            resources=AgentResources(cpu_cores=6, memory_mb=8192, storage_mb=65536),
        )

        with patch("app.agent_gateway.time.time", return_value=1000.0):
            response = asyncio.run(register_full_agent(hello, self.request))

        mapping = self.redis.hset.await_args.kwargs["mapping"]
        self.assertEqual("1", mapping["tier"])
        self.assertEqual("https", mapping["transport"])
        self.assertTrue(response["accepted"])

    def test_telemetry_reaches_shared_stream_and_latest_view(self) -> None:
        self.redis.hgetall.return_value = {
            "id": "drone-full-1",
            "tier": "1",
            "transport": "https",
            "boot_id": "boot-1",
        }
        self.redis.set.return_value = True

        response = asyncio.run(
            ingest_full_telemetry("drone-full-1", telemetry(), self.request)
        )

        self.assertFalse(response["duplicate"])
        latest = self.redis.hset.await_args_list[0]
        self.assertEqual("device-telemetry-latest:drone-full-1", latest.args[0])
        self.assertEqual("flight", latest.kwargs["mapping"]["kind"])
        self.assertEqual("device-telemetry", self.redis.xadd.await_args.args[0])

    def test_command_poll_returns_structured_payload(self) -> None:
        self.redis.hgetall.return_value = {
            "id": "drone-full-1",
            "tier": "1",
            "transport": "https",
            "boot_id": "boot-1",
        }
        self.redis.xread.return_value = [
            (
                "agent-commands:drone-full-1",
                [("100-0", {"payload": json.dumps({"type": "task.assign", "opcode": "mission.start"})})],
            )
        ]

        response = asyncio.run(
            poll_full_commands("drone-full-1", self.request, after="0-0", wait_seconds=0)
        )

        self.assertEqual("100-0", response["cursor"])
        self.assertEqual("mission.start", response["commands"][0]["opcode"])


class FakePipeline:
    def __init__(self) -> None:
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return record

    async def execute(self):
        return []


class FakeCommandRedis:
    def __init__(self) -> None:
        self.pipe = FakePipeline()

    async def hgetall(self, key):
        return {"id": "drone-full-1", "tier": "1", "transport": "https"}

    async def incr(self, key):
        return 7

    def pipeline(self, transaction=True):
        return self.pipe


class TierAwareCommandTests(unittest.TestCase):
    def test_full_agent_command_uses_per_agent_stream(self) -> None:
        redis = FakeCommandRedis()
        request = request_with(redis)

        response = asyncio.run(
            create_device_command(
                "drone-full-1",
                DeviceCommandRequest(
                    opcode="mission.start", arguments={"mission_id": "survey-12"}
                ),
                request,
            )
        )

        xadd = next(call for call in redis.pipe.calls if call[0] == "xadd")
        self.assertEqual("agent-commands:drone-full-1", xadd[1][0])
        self.assertIn('"mission_id":"survey-12"', xadd[1][1]["payload"])
        self.assertEqual("https", response["transport"])


if __name__ == "__main__":
    unittest.main()
