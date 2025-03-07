import logging
from dataclasses import KW_ONLY, dataclass
from typing import ClassVar, Literal, Self, override

from fastbot.event import Context, Event


@dataclass
class MetaEvent(Event):
    _: KW_ONLY

    post_type: Literal["meta_event"] = "meta_event"

    meta_event_type: Literal["heartbeat", "lifecycle"]

    event_type: ClassVar[dict[str, type["MetaEvent"]]] = {}

    def __init_subclass__(cls, *args, **kwargs) -> None:
        super().__init_subclass__(*args, **kwargs)

        MetaEvent.event_type[cls.meta_event_type] = cls

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> "MetaEvent":
        return (
            event.from_ctx(ctx=ctx)
            if (event := cls.event_type.get(ctx["meta_event_type"]))
            else cls(
                ctx=ctx,
                time=ctx["time"],
                self_id=ctx["self_id"],
                post_type=ctx["post_type"],
                meta_event_type=ctx["meta_event_type"],
            )
        )


@dataclass
class LifecycleMetaEvent(MetaEvent):
    _: KW_ONLY

    meta_event_type: Literal["lifecycle"] = "lifecycle"

    sub_type: Literal["enable", "disable", "connect"]

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )


@dataclass
class HeartbeatMetaEvent(MetaEvent):
    _: KW_ONLY

    meta_event_type: Literal["heartbeat"] = "heartbeat"

    status: dict
    interval: int

    def __post_init__(self) -> None:
        logging.debug(self.__repr__())

    @classmethod
    @override
    def from_ctx(cls, *, ctx: Context) -> Self:
        return cls(
            ctx=ctx, **{k: v for k, v in ctx.items() if k in cls.__dataclass_fields__}
        )
