"""Length-delimited protobuf framing used by the raw TCP transport."""

from __future__ import annotations

import asyncio
import struct
from typing import TypeVar

from google.protobuf.message import Message


MAX_FRAME_BYTES = 1024 * 1024
_HEADER = struct.Struct("!I")
MessageT = TypeVar("MessageT", bound=Message)


class FrameError(ValueError):
    """A TCP frame is empty, oversized, truncated, or invalid protobuf."""


def encode_frame(message: Message) -> bytes:
    payload = message.SerializeToString()
    if not payload:
        raise FrameError("empty protobuf messages are not allowed")
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    return _HEADER.pack(len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader, message_type: type[MessageT]) -> MessageT:
    try:
        header = await reader.readexactly(_HEADER.size)
    except asyncio.IncompleteReadError as exc:
        raise EOFError from exc
    length = _HEADER.unpack(header)[0]
    if length == 0:
        raise FrameError("empty protobuf messages are not allowed")
    if length > MAX_FRAME_BYTES:
        raise FrameError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise FrameError("truncated frame") from exc
    message = message_type()
    try:
        message.ParseFromString(payload)
    except Exception as exc:
        raise FrameError("invalid protobuf payload") from exc
    return message


async def write_frame(writer: asyncio.StreamWriter, message: Message) -> None:
    writer.write(encode_frame(message))
    await writer.drain()
