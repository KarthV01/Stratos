from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TaskStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TASK_STATUS_UNSPECIFIED: _ClassVar[TaskStatus]
    TASK_STATUS_SUCCEEDED: _ClassVar[TaskStatus]
    TASK_STATUS_FAILED: _ClassVar[TaskStatus]
TASK_STATUS_UNSPECIFIED: TaskStatus
TASK_STATUS_SUCCEEDED: TaskStatus
TASK_STATUS_FAILED: TaskStatus

class AgentHello(_message.Message):
    __slots__ = ("protocol_version", "agent_id", "board", "agent_version", "capabilities", "token")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    BOARD_FIELD_NUMBER: _ClassVar[int]
    AGENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    protocol_version: str
    agent_id: str
    board: str
    agent_version: str
    capabilities: _containers.RepeatedScalarFieldContainer[str]
    token: str
    def __init__(self, protocol_version: _Optional[str] = ..., agent_id: _Optional[str] = ..., board: _Optional[str] = ..., agent_version: _Optional[str] = ..., capabilities: _Optional[_Iterable[str]] = ..., token: _Optional[str] = ...) -> None: ...

class Heartbeat(_message.Message):
    __slots__ = ("sent_at_unix_ms", "running_task_ids")
    SENT_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    RUNNING_TASK_IDS_FIELD_NUMBER: _ClassVar[int]
    sent_at_unix_ms: int
    running_task_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, sent_at_unix_ms: _Optional[int] = ..., running_task_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class TaskResult(_message.Message):
    __slots__ = ("task_id", "lease_id", "status", "result_json", "error")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    lease_id: str
    status: TaskStatus
    result_json: str
    error: str
    def __init__(self, task_id: _Optional[str] = ..., lease_id: _Optional[str] = ..., status: _Optional[_Union[TaskStatus, str]] = ..., result_json: _Optional[str] = ..., error: _Optional[str] = ...) -> None: ...

class AgentMessage(_message.Message):
    __slots__ = ("hello", "heartbeat", "task_result")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    TASK_RESULT_FIELD_NUMBER: _ClassVar[int]
    hello: AgentHello
    heartbeat: Heartbeat
    task_result: TaskResult
    def __init__(self, hello: _Optional[_Union[AgentHello, _Mapping]] = ..., heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., task_result: _Optional[_Union[TaskResult, _Mapping]] = ...) -> None: ...

class Welcome(_message.Message):
    __slots__ = ("session_id", "heartbeat_interval_seconds", "server_time_unix_ms")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_INTERVAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    SERVER_TIME_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    heartbeat_interval_seconds: int
    server_time_unix_ms: int
    def __init__(self, session_id: _Optional[str] = ..., heartbeat_interval_seconds: _Optional[int] = ..., server_time_unix_ms: _Optional[int] = ...) -> None: ...

class TaskAssignment(_message.Message):
    __slots__ = ("task_id", "lease_id", "payload_json")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    lease_id: str
    payload_json: str
    def __init__(self, task_id: _Optional[str] = ..., lease_id: _Optional[str] = ..., payload_json: _Optional[str] = ...) -> None: ...

class ResultAck(_message.Message):
    __slots__ = ("task_id", "accepted", "detail")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    accepted: bool
    detail: str
    def __init__(self, task_id: _Optional[str] = ..., accepted: bool = ..., detail: _Optional[str] = ...) -> None: ...

class ProtocolError(_message.Message):
    __slots__ = ("code", "detail", "fatal")
    CODE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    FATAL_FIELD_NUMBER: _ClassVar[int]
    code: str
    detail: str
    fatal: bool
    def __init__(self, code: _Optional[str] = ..., detail: _Optional[str] = ..., fatal: bool = ...) -> None: ...

class GatewayMessage(_message.Message):
    __slots__ = ("welcome", "task", "result_ack", "error")
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    RESULT_ACK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    welcome: Welcome
    task: TaskAssignment
    result_ack: ResultAck
    error: ProtocolError
    def __init__(self, welcome: _Optional[_Union[Welcome, _Mapping]] = ..., task: _Optional[_Union[TaskAssignment, _Mapping]] = ..., result_ack: _Optional[_Union[ResultAck, _Mapping]] = ..., error: _Optional[_Union[ProtocolError, _Mapping]] = ...) -> None: ...
