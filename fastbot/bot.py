import asyncio
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from typing import Any, ClassVar, Iterable, Self
from weakref import WeakValueDictionary

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketException, status

from fastbot.plugin import PluginManager

try:
    import ujson as json

    json.dumps = partial(json.dumps, ensure_ascii=False, sort_keys=False)

except ImportError:
    import json

    json.dumps = partial(
        json.dumps, ensure_ascii=False, separators=(",", ":"), sort_keys=False
    )


@dataclass(slots=True)
class FastBot:
    app: ClassVar[FastAPI]

    connectors: ClassVar[WeakValueDictionary[int, WebSocket]] = WeakValueDictionary()
    futures: ClassVar[dict[int, asyncio.Future]] = {}

    self_id: ClassVar[ContextVar[int | None]] = ContextVar("self_id", default=None)

    def __init__(self, *, app: FastAPI = FastAPI()) -> None:
        self.__class__.app = app

    @classmethod
    async def ws_adapter(cls, websocket: WebSocket) -> None:
        if authorization := os.getenv("FASTBOT_AUTHORIZATION"):
            if not (access_token := websocket.headers.get("authorization")):
                raise WebSocketException(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Missing `authorization` header",
                )

            match access_token.split():
                case [header, token] if header.title() in ("Bearer", "Token"):
                    if token != authorization:
                        raise WebSocketException(
                            code=status.HTTP_403_FORBIDDEN,
                            reason="Invalid `authorization` header",
                        )

                case [token]:
                    if token != authorization:
                        raise WebSocketException(
                            code=status.HTTP_403_FORBIDDEN,
                            reason="Invalid `authorization` header",
                        )

                case _:
                    raise WebSocketException(
                        code=status.HTTP_403_FORBIDDEN,
                        reason="Invalid `authorization` header",
                    )

        if not (self_id := websocket.headers.get("x-self-id")):
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Missing `x-self-id` header",
            )

        if not (self_id.isdigit() and (self_id := int(self_id))):
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Invalid `x-self-id` header",
            )

        if self_id in cls.connectors:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Duplicate `x-self-id` header",
            )

        await websocket.accept()

        logging.info(f"Websocket connected {self_id=}")

        cls.connectors[self_id] = websocket

        await cls.event_handler(websocket=websocket)

    @classmethod
    async def event_handler(cls, websocket: WebSocket) -> None:
        async with asyncio.TaskGroup() as tg:
            while True:
                match message := await websocket.receive():
                    case {"bytes": data} | {"text": data}:
                        if "post_type" in (ctx := json.loads(data)):
                            cls.self_id.set(ctx.get("self_id"))

                            tg.create_task(PluginManager.run(ctx=ctx))

                        elif ctx["status"] == "ok":
                            cls.futures[ctx["echo"]].set_result(ctx.get("data"))

                        else:
                            cls.futures[ctx["echo"]].set_exception(RuntimeError(ctx))

                    case _:
                        logging.warning(f"Unknow websocket message received {message=}")

    @classmethod
    async def do(cls, *, endpoint: str, self_id: int | None = None, **kwargs) -> Any:
        if not (
            self_id := (
                self_id
                or cls.self_id.get()
                or (next(iter(cls.connectors)) if len(cls.connectors) == 1 else None)
            )
        ):
            raise RuntimeError("Parameter `self_id` must be specified")

        logging.debug(f"{endpoint=} {self_id=} {kwargs=}")

        future = asyncio.Future()
        future_id = id(future)

        cls.futures[future_id] = future

        try:
            await cls.connectors[self_id].send_bytes(
                json.dumps(
                    {"action": endpoint, "params": kwargs, "echo": future_id}
                ).encode(encoding="utf-8")
            )

            return await future

        finally:
            del cls.futures[future_id]

    @classmethod
    def build(
        cls, app: FastAPI = FastAPI(), plugins: str | Iterable[str] | None = None
    ) -> Self:
        if isinstance(plugins, str):
            PluginManager.import_from(plugins)

        elif isinstance(plugins, Iterable):
            for plugin in plugins:
                PluginManager.import_from(plugin)

        return cls(app=app)

    @classmethod
    def run(cls, **kwargs) -> None:
        uvicorn.run(app=cls.app, **kwargs)
