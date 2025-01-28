# FastBot

A lightweight bot framework base on `FastAPI` and `OneBot v11` protocol.

## Quick Start
### Installation
#### Install from Github
```sh
pip install --no-cache --upgrade git+https://github.com/OrganRemoved/fastbot.git
```

or

```sh
pip install --no-cache --upgrade https://github.com/OrganRemoved/fastbot/archive/refs/heads/main.zip
```

#### Install from PYPI
```sh
pip install --no-cache --upgrade fastbot-onebot
```

### Example
The directory structure is as follows:
```sh
bot_example
|   __init__.py
|   bot.py
|
\---plugins
        __init__.py
        plugin_example.py
```

#### bot.py
```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastbot.bot import FastBot
from fastbot.plugin import PluginManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Register a websocket adapter to `FastAPI`
    app.add_api_websocket_route("/onebot/v11/ws", FastBot.ws_adapter)

    await asyncio.gather(
        *(
            init() if asyncio.iscoroutinefunction(init) else asyncio.to_thread(init)
            for plugin in PluginManager.plugins.values()
            if (init := plugin.init)
        ),
    )

    yield


if __name__ == "__main__":
    (
        FastBot
        # `plugins` parameter will pass to `fastbot.plugin.PluginManager.import_from(...)`
        # the rest parameter will pass to `FastAPI(...)`
        .build(plugins=["plugins"], lifespan=lifespan)
        # Parameter will pass to `uvicorn.run(...)`
        .run(host="0.0.0.0", port=80)
    )
```

#### plugin_example.py

```python
from typing import AsyncGenerator

from fastbot.event import Context
from fastbot.event.message import GroupMessageEvent, PrivateMessageEvent
from fastbot.matcher import Matcher
from fastbot.plugin import Dependency, PluginManager, middleware, on
from redis.asyncio.client import Redis, Pipeline


# Passing rules to the matcher
IsNotGroupAdmin = Matcher(rule=lambda event: event.sender.role != "admin")


# Refactoring the Matcher
class IsInGroupBlacklist(Matcher):
    def __init__(self, *blacklist):
        self.blacklist = blacklist

    def __call__(self, event: GroupMessageEvent) -> bool:
        return event.group_id in self.blacklist


async def init() -> None:
    # Do some initial work here
    ...


# Dependency injection
async def get_redis(*args, **kwargs) -> AsyncGenerator[Redis, None]:
    if "url" in kwargs:
        redis = Redis.from_url(decode_responses=True, *args, **kwargs)

    elif "connection_pool" in kwargs:
        redis = Redis.from_pool(*args, **kwargs)

    else:
        redis = Redis(
            host=kwargs.pop("host", environ["REDIS_HOST"]),
            port=kwargs.pop("port", int(environ["REDIS_PORT"])),
            db=kwargs.pop("db", environ["REDIS_DB"]),
            password=kwargs.pop("password", environ["REDIS_PASSWORD"]),
            decode_responses=kwargs.pop("decode_responses", True),
            **kwargs,
        )

    async with redis as r:
        yield r


# Chaining dependency injection
async def get_pipeline(
    redis: Redis = Dependency.provide(dependency=redis), *args, **kwargs
) -> AsyncGenerator[Pipeline, None]:
    async with redis.pipeline(*args, **kwargs) as pipeline:
        yield pipeline

        await pipeline.execute()


# All middlewares will be executed in sequence
@middleware(priority=0)
async def preprocessing(ctx: Context):
    if (group_id := ctx.get("group_id")) == ...:
        # Temporarily disable the plugin
        PluginManager.plugins["plugins.plugin_example"].state.set(False)
    elif group_id is None:
        # When the `Context` is clear, the middleware will discard
        # the event and terminate processing
        ctx.clear()


# Combining multiple rules via `&(and)`, `|(or)`,`~(not)`
@on(matcher=IsNotGroupAdmin & ~IsInGroupBlacklist(...))
# For the best performance, you can use `callable function`
# E.g. `lambda event: event.get("group_id") in (...)`
async def func(
    # The event type to be handled must be specified via type hints
    # You can use `|`  or `typing.Union` types
    event: GroupMessageEvent | PrivateMessageEvent,
    *,
    redis: Redis = Dependency.provide(dependency=get_redis),
    pipeline: Pipeline = Dependency.provide(dependency=get_pipeline),
) -> None:
    if event.text == "guess":
        await event.send("Start guessing the number game now: [0-10]!")

        while new_event := await event.defer("Enter a number: "):
            if new_event.text != "10":
                await new_event.send("Guess wrong!")
                continue

            await new_event.send("Guess right!")
            return
```
