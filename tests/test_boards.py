import asyncio
import unittest

from fastapi import HTTPException

from app.boards import (
    AgentTier,
    board_directory,
    classify_capabilities,
    decode_hello,
    encode_hello,
    encode_identity,
    resolve_board,
)
from app.controller import (
    BoardIdentityRequest,
    ReportedBoardCapabilities,
    identify_board,
    list_boards,
)


class BoardDirectoryTests(unittest.TestCase):
    def test_directory_has_broad_board_coverage(self) -> None:
        boards = board_directory()
        vendors = {board.vendor for board in boards}

        self.assertGreaterEqual(len(boards), 40)
        self.assertTrue(
            {"Arduino", "Espressif", "Raspberry Pi", "NVIDIA", "BeagleBoard.org"}
            <= vendors
        )

    def test_aliases_resolve_to_canonical_profiles(self) -> None:
        esp32 = resolve_board("ESP32-S3")
        raspberry_pi = resolve_board("rpi5")

        self.assertIsNotNone(esp32)
        self.assertEqual("espressif/esp32-s3-devkitc-1", esp32.identifier)
        self.assertEqual(AgentTier.EMBEDDED, esp32.agent_tier)
        self.assertEqual("mqtt", esp32.connection_mode)
        self.assertIsNotNone(raspberry_pi)
        self.assertEqual(AgentTier.FULL, raspberry_pi.agent_tier)
        self.assertEqual("agent", raspberry_pi.connection_mode)

    def test_accelerated_linux_board_is_a_full_agent(self) -> None:
        jetson = resolve_board("Jetson Orin Nano")

        self.assertIsNotNone(jetson)
        self.assertEqual(AgentTier.FULL, jetson.agent_tier)
        self.assertEqual("agent", jetson.connection_mode)

    def test_unknown_capabilities_are_classified_conservatively(self) -> None:
        self.assertEqual(
            AgentTier.FULL,
            classify_capabilities(
                linux=True, ram_kb=524_288, accelerator=False, ip_network=True
            ),
        )
        self.assertEqual(
            AgentTier.EMBEDDED,
            classify_capabilities(
                linux=False, ram_kb=520, accelerator=False, ip_network=True
            ),
        )

    def test_every_non_full_board_can_use_mqtt(self) -> None:
        for board in board_directory():
            self.assertTrue(board.ip_network, board.identifier)
            if board.agent_tier != AgentTier.FULL:
                self.assertEqual("mqtt", board.connection_mode, board.identifier)

    def test_non_networked_boards_are_not_in_the_directory(self) -> None:
        for unsupported in ("Arduino Uno R3", "ATtiny85", "Pico", "micro:bit v2"):
            self.assertIsNone(resolve_board(unsupported), unsupported)

    def test_minimal_tier_contains_mqtt_capable_boards(self) -> None:
        for identifier in ("esp8266", "uno r4 wifi", "nano 33 iot"):
            board = resolve_board(identifier)
            self.assertIsNotNone(board)
            self.assertEqual(AgentTier.MINIMAL, board.agent_tier)
            self.assertEqual("mqtt", board.connection_mode)

    def test_board_without_ip_network_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must support an IP network"):
            classify_capabilities(
                linux=False, ram_kb=32, accelerator=False, ip_network=False
            )


class BoardWireProtocolTests(unittest.TestCase):
    def test_hello_round_trip_is_small(self) -> None:
        payload = encode_hello("esp32-s3", "greenhouse-07", "1.2.0")
        hello = decode_hello(payload)

        self.assertLess(len(payload), 64)
        self.assertEqual("esp32-s3", hello.board)
        self.assertEqual("greenhouse-07", hello.device_id)
        self.assertEqual("1.2.0", hello.firmware)

    def test_wire_protocol_rejects_delimiter_injection(self) -> None:
        with self.assertRaises(ValueError):
            encode_hello("esp32|fake", "device-1")

    def test_identity_response_contains_tier_and_connection_mode(self) -> None:
        profile = resolve_board("pico w")

        self.assertIsNotNone(profile)
        self.assertEqual(
            b"SBP1|OK|raspberry-pi/pico-w|2|embedded-agent|mqtt\n",
            encode_identity(profile),
        )


class BoardApiTests(unittest.TestCase):
    def test_known_board_identification_is_authoritative(self) -> None:
        response = asyncio.run(
            identify_board(
                BoardIdentityRequest(board="pi 5", device_id="lab-pi-01")
            )
        )

        self.assertFalse(response["provisional"])
        self.assertEqual(1, response["board"]["tier"])
        self.assertEqual("full-agent", response["board"]["tier_code"])
        self.assertEqual("agent", response["board"]["connection_mode"])

    def test_unknown_board_requires_capabilities(self) -> None:
        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                identify_board(
                    BoardIdentityRequest(board="future-board", device_id="future-01")
                )
            )

        self.assertEqual(404, context.exception.status_code)

    def test_unknown_board_can_receive_provisional_class(self) -> None:
        response = asyncio.run(
            identify_board(
                BoardIdentityRequest(
                    board="Future Linux Board",
                    device_id="future-01",
                    capabilities=ReportedBoardCapabilities(
                        linux=True,
                        ram_kb=4 * 1024 * 1024,
                        accelerator=False,
                        ip_network=True,
                    ),
                )
            )
        )

        self.assertTrue(response["provisional"])
        self.assertEqual("full-agent", response["board"]["tier_code"])

    def test_unknown_non_network_board_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                identify_board(
                    BoardIdentityRequest(
                        board="Offline MCU",
                        device_id="offline-01",
                        capabilities=ReportedBoardCapabilities(
                            linux=False,
                            ram_kb=32,
                            accelerator=False,
                            ip_network=False,
                        ),
                    )
                )
            )

        self.assertEqual(422, context.exception.status_code)

    def test_directory_can_filter_by_tier(self) -> None:
        response = asyncio.run(list_boards(tier=1, vendor=None))

        self.assertGreater(len(response["boards"]), 15)
        self.assertTrue(
            all(board["tier_code"] == "full-agent" for board in response["boards"])
        )


if __name__ == "__main__":
    unittest.main()
