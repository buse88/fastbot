import asyncio
import logging
from dataclasses import KW_ONLY, asdict, dataclass
from functools import cached_property
from typing import Any, ClassVar, Iterable, Literal, Self, override

from fastbot.bot import FastBot
from fastbot.event import Context, Event
from fastbot.message import Message, MessageSegment


@dataclass
class MessageEvent(Event):
    _: KW_ONLY

    post_type: Literal["message"] = "message"

    message_type: Literal["group", "private"]

    event_type: ClassVar[dict[str, type["MessageEvent"]]] = {}

    def __init_subclass__(cls, *args, **kwargs) -> None:
        super().__init_subclass__(*args, **kwargs)

        MessageEvent.event_type[cls.message_type] = cls

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> "MessageEvent":
        return (
            event.from_ctx(ctx=ctx)
            if (event := cls.event_type.get(ctx["message_type"]))
            else cls(
                ctx=ctx,
                time=ctx["time"],
                self_id=ctx["self_id"],
                post_type=ctx["post_type"],
                message_type=ctx["message_type"],
            )
        )


@dataclass
class PrivateMessageEvent(MessageEvent):
    @dataclass
    class Sender:
        _: KW_ONLY

        user_id: int | None = None
        nickname: str | None = None
        sex: str | None = None
        age: int | None = None

    _: KW_ONLY

    message_type: Literal["private"] = "private"

    sub_type: Literal["friend", "group", "other"]

    message_id: int
    user_id: int
    message: Message
    raw_message: str
    font: int
    sender: Sender

    futures: ClassVar[dict[int, asyncio.Future]] = {}

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

        self.message = Message(
            MessageSegment(type=msg["type"], data=msg["data"]) for msg in self.message
        )

        self.sender = self.Sender(
            **{
                k: v
                for k, v in self.ctx["sender"].items()
                if k in self.Sender.__dataclass_fields__
            }
        )

        if future := self.__class__.futures.get(self.user_id):
            future.set_result(self)

    def __hash__(self) -> int:
        return hash((self.user_id, self.time, self.self_id, self.raw_message))

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )

    async def send(
        self,
        message: str
        | Message
        | MessageSegment
        | Iterable[str | Message | MessageSegment],
    ) -> Any:
        return await FastBot.do(
            endpoint="send_private_msg",
            message=[asdict(msg) for msg in Message(message)],
            self_id=self.self_id,
            user_id=self.user_id,
        )

    async def defer(
        self,
        message: str
        | Message
        | MessageSegment
        | Iterable[str | Message | MessageSegment],
    ) -> Self:
        future = asyncio.Future()
        self.__class__.futures[self.user_id] = future

        await self.send(message=message)

        try:
            return await future
        finally:
            del self.__class__.futures[self.user_id]

    @cached_property
    def plaintext(self) -> str:
        return "".join(
            segment.data["text"] for segment in self.message if segment.type == "text"
        )


@dataclass
class GroupMessageEvent(MessageEvent):
    @dataclass
    class Sender:
        _: KW_ONLY

        user_id: int | None = None
        nickname: str | None = None
        card: str | None = None
        role: str | None = None
        sex: str | None = None
        age: int | None = None
        area: str | None = None
        level: str | None = None
        title: str | None = None

    _: KW_ONLY

    message_type: Literal["group"] = "group"

    sub_type: Literal["normal", "anonymous", "notice"]

    message_id: int
    group_id: int
    user_id: int
    message: Message
    raw_message: str
    font: int
    sender: Sender

    futures: ClassVar[dict[tuple[int, int], asyncio.Future]] = {}

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

        self.message = Message(
            MessageSegment(type=msg["type"], data=msg["data"]) for msg in self.message
        )

        self.sender = self.Sender(
            **{
                k: v
                for k, v in self.ctx["sender"].items()
                if k in self.Sender.__dataclass_fields__
            }
        )

        if future := self.__class__.futures.get((self.group_id, self.user_id)):
            future.set_result(self)

    def __hash__(self) -> int:
        return hash((self.user_id, self.time, self.self_id, self.raw_message))

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )

    async def send(
        self,
        message: str
        | Message
        | MessageSegment
        | Iterable[str | Message | MessageSegment],
    ) -> Any:
        return await FastBot.do(
            endpoint="send_group_msg",
            message=[asdict(msg) for msg in Message(message)],
            self_id=self.self_id,
            group_id=self.group_id,
        )

    async def defer(
        self,
        message: str
        | Message
        | MessageSegment
        | Iterable[str | Message | MessageSegment],
    ) -> Self:
        future = asyncio.Future()
        self.__class__.futures[(self.group_id, self.user_id)] = future

        await self.send(message=message)

        try:
            return await future
        finally:
            del self.__class__.futures[(self.group_id, self.user_id)]

    @cached_property
    def plaintext(self) -> str:
        return "".join(
            segment.data["text"] for segment in self.message if segment.type == "text"
        )
