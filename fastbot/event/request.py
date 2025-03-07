import logging
from dataclasses import KW_ONLY, dataclass
from typing import Any, ClassVar, Literal, Self, override

from fastbot.bot import FastBot
from fastbot.event import Context, Event


@dataclass
class RequestEvent(Event):
    _: KW_ONLY

    post_type: Literal["request"] = "request"

    request_type: Literal["friend", "group"]

    event_type: ClassVar[dict[str, type["RequestEvent"]]] = {}

    def __init_subclass__(cls, *args, **kwargs) -> None:
        super().__init_subclass__(*args, **kwargs)

        RequestEvent.event_type[cls.request_type] = cls

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> "RequestEvent":
        return (
            event.from_ctx(ctx=ctx)
            if (event := cls.event_type.get(ctx["request_type"]))
            else cls(
                ctx=ctx,
                time=ctx["time"],
                self_id=ctx["self_id"],
                post_type=ctx["post_type"],
                request_type=ctx["request_type"],
            )
        )


@dataclass
class FriendRequestEvent(RequestEvent):
    _: KW_ONLY

    request_type: Literal["friend"] = "friend"

    user_id: int
    comment: str
    flag: str

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )

    async def approve(self, *, remark: str | None = None) -> Any:
        return await FastBot.do(
            endpoint="set_friend_add_request",
            self_id=self.self_id,
            approve=True,
            flag=self.flag,
            remark=remark,
        )

    async def reject(self) -> Any:
        return await FastBot.do(
            endpoint="set_friend_add_request", self_id=self.self_id, approve=False
        )


@dataclass
class GroupRequestEvent(RequestEvent):
    _: KW_ONLY

    request_type: Literal["group"] = "group"

    sub_type: Literal["add", "invite"]

    group_id: int
    user_id: int
    comment: str
    flag: str

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )

    async def approve(self) -> Any:
        return await FastBot.do(
            endpoint="set_group_add_request",
            self_id=self.self_id,
            approve=True,
            sub_type=self.sub_type,
        )

    async def reject(self, *, reason: str | None = None) -> Any:
        return await FastBot.do(
            endpoint="set_group_add_request",
            self_id=self.self_id,
            approve=False,
            reason=reason,
        )
