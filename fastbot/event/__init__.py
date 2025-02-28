from dataclasses import KW_ONLY, dataclass, field
from typing import Any, ClassVar, Literal, TypeAlias

Context: TypeAlias = dict[str, Any]


@dataclass
class Event:
    _: KW_ONLY

    ctx: Context = field(repr=False)

    post_type: Literal["message", "notice", "request", "meta_event"]

    time: int
    self_id: int

    event_type: ClassVar[dict[str, type["Event"]]] = {}

    def __init_subclass__(cls, *args, **kwargs) -> None:
        super().__init_subclass__(*args, **kwargs)

        Event.event_type[cls.post_type] = cls

    @classmethod
    def from_ctx(cls, *, ctx: Context) -> "Event":
        return (
            event.from_ctx(ctx=ctx)
            if (event := cls.event_type.get(ctx["post_type"]))
            else cls(
                ctx=ctx,
                time=ctx["time"],
                self_id=ctx["self_id"],
                post_type=ctx["post_type"],
            )
        )
