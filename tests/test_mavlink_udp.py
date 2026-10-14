import asyncio
import socket
import unittest

from pymavlink.dialects.v20 import common as mavlink2

from app.mavlink.agent import receive_snapshots


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class MavlinkUdpFleetTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_concurrent_udp_feeds_keep_vehicle_identity_isolated(self) -> None:
        # Two different vehicles intentionally reuse MAVLink system ID 1.
        vehicles = [
            ("px4-aircraft", 1, mavlink2.MAV_AUTOPILOT_PX4),
            ("ardupilot-aircraft", 1, mavlink2.MAV_AUTOPILOT_ARDUPILOTMEGA),
            ("scout-aircraft", 2, mavlink2.MAV_AUTOPILOT_PX4),
        ]
        ports = [free_udp_port() for _ in vehicles]
        socket_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        async def read_vehicle(index: int):
            vehicle_id, _, _ = vehicles[index]
            async for snapshot in receive_snapshots(
                f"udpin:127.0.0.1:{ports[index]}",
                vehicle_id,
                f"companion-{index + 1}",
                rate_hz=5.0,
            ):
                if snapshot.identity.system_id is not None and (
                    snapshot.position.latitude_e7 is not None
                ):
                    return snapshot
            raise AssertionError("MAVLink UDP stream ended unexpectedly")

        tasks = [asyncio.create_task(read_vehicle(index)) for index in range(3)]
        try:
            # Let pymavlink bind each UDP listener before sending the test packets.
            await asyncio.sleep(0.2)
            for index, (_, system_id, autopilot) in enumerate(vehicles):
                sender = mavlink2.MAVLink(
                    None, srcSystem=system_id, srcComponent=1
                )
                messages = (
                    sender.heartbeat_encode(
                        mavlink2.MAV_TYPE_QUADROTOR,
                        autopilot,
                        mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
                        7,
                        mavlink2.MAV_STATE_ACTIVE,
                    ),
                    sender.global_position_int_encode(
                        1234,
                        401000000 + index,
                        -730000000 - index,
                        12000,
                        2000,
                        300,
                        400,
                        10,
                        9000,
                    ),
                )
                for message in messages:
                    socket_tx.sendto(
                        message.pack(sender), ("127.0.0.1", ports[index])
                    )

            snapshots = await asyncio.wait_for(
                asyncio.gather(*tasks), timeout=5.0
            )
        finally:
            socket_tx.close()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        self.assertEqual(
            [item[0] for item in vehicles], [snapshot.vehicle_id for snapshot in snapshots]
        )
        self.assertEqual([1, 1, 2], [s.identity.system_id for s in snapshots])
        self.assertEqual(
            ["px4", "ardupilot", "px4"],
            [s.identity.autopilot for s in snapshots],
        )
        self.assertEqual(
            [401000000, 401000001, 401000002],
            [s.position.latitude_e7 for s in snapshots],
        )
        self.assertTrue(all(s.link.connected for s in snapshots))


if __name__ == "__main__":
    unittest.main()
