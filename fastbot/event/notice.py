import logging
from dataclasses import KW_ONLY, dataclass
from typing import ClassVar, Literal, Self, override

from fastbot.event import Context, Event


@dataclass
class NoticeEvent(Event):
    _: KW_ONLY

    post_type: Literal["notice"] = "notice"

    notice_type: str

    event_type: ClassVar[dict[str, type["NoticeEvent"]]] = {}

    def __init_subclass__(cls, *args, **kwargs) -> None:
        super().__init_subclass__(*args, **kwargs)

        NoticeEvent.event_type[cls.notice_type] = cls

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> "NoticeEvent":
        return (
            event.from_ctx(ctx=ctx)
            if (event := cls.event_type.get(ctx["notice_type"]))
            else cls(
                ctx=ctx,
                time=ctx["time"],
                self_id=ctx["self_id"],
                post_type=ctx["post_type"],
                notice_type=ctx["notice_type"],
            )
        )


@dataclass
class GroupFileUploadNoticeEvent(NoticeEvent):
    @dataclass
    class File:
        _: KW_ONLY

        id: str
        name: str
        size: int
        busid: int

    _: KW_ONLY

    notice_type: Literal["group_upload"] = "group_upload"

    group_id: int
    user_id: int
    file: File

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

        self.file = self.File(
            **{
                k: v
                for k, v in self.ctx["file"].items()
                if k in self.File.__dataclass_fields__
            }
        )

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class GroupAdminChangeNoticeEvent(NoticeEvent):
    _: KW_ONLY

    notice_type: Literal["group_admin"] = "group_admin"

    sub_type: Literal["set", "unset"]

    group_id: int
    user_id: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class GroupMemberDecreaseNoticeEvent(NoticeEvent):
    _: KW_ONLY

    notice_type: Literal["group_decrease"] = "group_decrease"

    sub_type: Literal["leave", "kick", "kick_me"]

    group_id: int
    user_id: int
    operator_id: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class GroupMemberIncreaseNoticeEvent(NoticeEvent):
    _: KW_ONLY

    notice_type: Literal["group_increase"] = "group_increase"

    sub_type: Literal["approve", "invite"]

    group_id: int
    operator_id: int
    user_id: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class GroupBanNoticeEvent(NoticeEvent):
    _: KW_ONLY

    notice_type: Literal["group_ban"] = "group_ban"

    sub_type: Literal["ban", "lift_ban"]

    group_id: int
    operator_id: int
    user_id: int
    duration: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class FriendAddNoticeEvent(NoticeEvent):
    _: KW_ONLY

    notice_type: Literal["friend_add"] = "friend_add"

    user_id: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class GroupMessageRecallNoticeEvent(NoticeEvent):
    _: KW_ONLY

    notice_type: Literal["group_recall"] = "group_recall"

    group_id: int
    user_id: int
    operator_id: int
    message_id: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class FriendMessageRecallNoticeEvent(NoticeEvent):
    _: KW_ONLY

    notice_type: Literal["friend_recall"] = "friend_recall"

    user_id: int
    message_id: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )
