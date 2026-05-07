from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ClientFrame(_message.Message):
    __slots__ = ("hello", "user_message", "end")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    USER_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    hello: Hello
    user_message: UserMessage
    end: EndSession
    def __init__(self, hello: _Optional[_Union[Hello, _Mapping]] = ..., user_message: _Optional[_Union[UserMessage, _Mapping]] = ..., end: _Optional[_Union[EndSession, _Mapping]] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("protocol_version", "client_identity", "auth_token")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    CLIENT_IDENTITY_FIELD_NUMBER: _ClassVar[int]
    AUTH_TOKEN_FIELD_NUMBER: _ClassVar[int]
    protocol_version: int
    client_identity: str
    auth_token: bytes
    def __init__(self, protocol_version: _Optional[int] = ..., client_identity: _Optional[str] = ..., auth_token: _Optional[bytes] = ...) -> None: ...

class UserMessage(_message.Message):
    __slots__ = ("text", "turn_id")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    TURN_ID_FIELD_NUMBER: _ClassVar[int]
    text: str
    turn_id: str
    def __init__(self, text: _Optional[str] = ..., turn_id: _Optional[str] = ...) -> None: ...

class EndSession(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class ServerFrame(_message.Message):
    __slots__ = ("welcome", "token", "turn_end", "error")
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    TURN_END_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    welcome: Welcome
    token: AssistantToken
    turn_end: AssistantTurnEnd
    error: ErrorFrame
    def __init__(self, welcome: _Optional[_Union[Welcome, _Mapping]] = ..., token: _Optional[_Union[AssistantToken, _Mapping]] = ..., turn_end: _Optional[_Union[AssistantTurnEnd, _Mapping]] = ..., error: _Optional[_Union[ErrorFrame, _Mapping]] = ...) -> None: ...

class Welcome(_message.Message):
    __slots__ = ("server_identity", "session_id", "protocol_version")
    SERVER_IDENTITY_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    server_identity: str
    session_id: str
    protocol_version: int
    def __init__(self, server_identity: _Optional[str] = ..., session_id: _Optional[str] = ..., protocol_version: _Optional[int] = ...) -> None: ...

class AssistantToken(_message.Message):
    __slots__ = ("text", "sequence", "turn_id")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    TURN_ID_FIELD_NUMBER: _ClassVar[int]
    text: str
    sequence: int
    turn_id: str
    def __init__(self, text: _Optional[str] = ..., sequence: _Optional[int] = ..., turn_id: _Optional[str] = ...) -> None: ...

class AssistantTurnEnd(_message.Message):
    __slots__ = ("finish_reason", "turn_id")
    class FinishReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        FINISH_REASON_UNSPECIFIED: _ClassVar[AssistantTurnEnd.FinishReason]
        FINISH_REASON_STOP: _ClassVar[AssistantTurnEnd.FinishReason]
        FINISH_REASON_LENGTH: _ClassVar[AssistantTurnEnd.FinishReason]
        FINISH_REASON_USER_CANCELED: _ClassVar[AssistantTurnEnd.FinishReason]
        FINISH_REASON_ERROR: _ClassVar[AssistantTurnEnd.FinishReason]
    FINISH_REASON_UNSPECIFIED: AssistantTurnEnd.FinishReason
    FINISH_REASON_STOP: AssistantTurnEnd.FinishReason
    FINISH_REASON_LENGTH: AssistantTurnEnd.FinishReason
    FINISH_REASON_USER_CANCELED: AssistantTurnEnd.FinishReason
    FINISH_REASON_ERROR: AssistantTurnEnd.FinishReason
    FINISH_REASON_FIELD_NUMBER: _ClassVar[int]
    TURN_ID_FIELD_NUMBER: _ClassVar[int]
    finish_reason: AssistantTurnEnd.FinishReason
    turn_id: str
    def __init__(self, finish_reason: _Optional[_Union[AssistantTurnEnd.FinishReason, str]] = ..., turn_id: _Optional[str] = ...) -> None: ...

class ErrorFrame(_message.Message):
    __slots__ = ("code", "message", "turn_id")
    class Code(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        CODE_UNSPECIFIED: _ClassVar[ErrorFrame.Code]
        CODE_PROTOCOL_VERSION_UNSUPPORTED: _ClassVar[ErrorFrame.Code]
        CODE_MALFORMED_FRAME: _ClassVar[ErrorFrame.Code]
        CODE_INTERNAL: _ClassVar[ErrorFrame.Code]
        CODE_SUBPROCESS_FAILED: _ClassVar[ErrorFrame.Code]
        CODE_RATE_LIMITED: _ClassVar[ErrorFrame.Code]
        CODE_UNAUTHORIZED: _ClassVar[ErrorFrame.Code]
    CODE_UNSPECIFIED: ErrorFrame.Code
    CODE_PROTOCOL_VERSION_UNSUPPORTED: ErrorFrame.Code
    CODE_MALFORMED_FRAME: ErrorFrame.Code
    CODE_INTERNAL: ErrorFrame.Code
    CODE_SUBPROCESS_FAILED: ErrorFrame.Code
    CODE_RATE_LIMITED: ErrorFrame.Code
    CODE_UNAUTHORIZED: ErrorFrame.Code
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TURN_ID_FIELD_NUMBER: _ClassVar[int]
    code: ErrorFrame.Code
    message: str
    turn_id: str
    def __init__(self, code: _Optional[_Union[ErrorFrame.Code, str]] = ..., message: _Optional[str] = ..., turn_id: _Optional[str] = ...) -> None: ...
