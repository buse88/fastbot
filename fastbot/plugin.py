import asyncio
import logging
from bisect import insort
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import KW_ONLY, dataclass, field
from functools import wraps
from heapq import merge
from importlib.util import module_from_spec, spec_from_file_location
from inspect import (
    Parameter,
    isasyncgenfunction,
    isclass,
    iscoroutinefunction,
    isgeneratorfunction,
    signature,
)
from operator import attrgetter
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Callable, ClassVar, Union, get_args, get_origin

from fastbot.event import Context, Event
from fastbot.matcher import Matcher


@dataclass(slots=True)
class Plugin:
    @dataclass(order=True, slots=True)
    class Middleware:
        _: KW_ONLY

        priority: int = 0
        executor: Callable[..., Any]

    _: KW_ONLY

    state: ContextVar[bool] = ContextVar("state", default=True)

    init: Callable[..., Any] | None = None

    middlewares: list[Middleware] = field(default_factory=list)
    executors: list[Callable[..., Any]] = field(default_factory=list)

    async def run(self, event: Event) -> None:
        await asyncio.gather(*(executor(event) for executor in self.executors))


@dataclass(slots=True)
class Dependency:
    _: KW_ONLY

    dependency: Callable[..., Any]

    @classmethod
    def provide(cls, dependency: Callable[..., Any]) -> Any:
        return cls(dependency=dependency)


@dataclass(slots=True)
class PluginManager:
    plugins: ClassVar[dict[str, Plugin]] = {}

    @classmethod
    def import_from(cls, path_to_import: str) -> None:
        def load(module_name: str, module_path: Path) -> None:
            module_name = module_name.removesuffix(".py")

            cls.plugins[module_name] = plugin = Plugin()

            try:
                spec = spec_from_file_location(module_name, module_path)
                module = module_from_spec(spec)  # type: ignore

                spec.loader.exec_module(module)  # type: ignore

                plugin.init = getattr(module, "init", None)

                logging.info(f"Loaded plugin [{module_name}] from [{module_path}]")

            except Exception as e:
                logging.exception(e)

            finally:
                if not (plugin.init or plugin.middlewares or plugin.executors):
                    del cls.plugins[module_name]

        if (path := Path(path_to_import)).is_dir():
            for file in path.rglob("*.py"):
                if file.is_file() and not file.name.startswith("_"):
                    load(".".join(file.relative_to(path.parent).parts), file)

        elif (
            path.is_file()
            and path.name.endswith(".py")
            and not path.name.startswith("_")
        ):
            load(".".join(path.parts).removesuffix(".py"), path)

    @classmethod
    async def run(cls, *, ctx: Context) -> None:
        try:
            for middleware in merge(
                middleware
                for plugin in cls.plugins.values()
                for middleware in plugin.middlewares
            ):
                _ = asyncio.Task(
                    middleware.executor(ctx),
                    loop=asyncio.get_running_loop(),
                    eager_start=True,
                )

                if not ctx:
                    logging.warning("The context is empty, discarding")

                    return

            event = Event.from_ctx(ctx=ctx)

            await asyncio.gather(
                *(
                    plugin.run(event=event)
                    for plugin in cls.plugins.values()
                    if plugin.state.get()
                )
            )

        except Exception as e:
            logging.exception(e)


def middleware(*, priority: int = 0) -> Callable[..., Any]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        insort(
            PluginManager.plugins[func.__module__].middlewares,
            Plugin.Middleware(priority=priority, executor=func),
            key=attrgetter("priority"),
        )

        return func

    return decorator


def on(matcher: Matcher | Callable[..., bool] | None = None) -> Callable[..., Any]:
    def annotation_event_type(annotation: Any) -> tuple[type[Event], ...]:
        if get_origin(annotation) in (Annotated, Union, UnionType):
            return tuple(
                arg
                for arg in get_args(annotation)
                if isclass(arg) and issubclass(arg, Event)
            )

        elif isclass(annotation) and issubclass(annotation, Event):
            return (annotation,)

        else:
            return ()

    async def resolve_dependency(
        event: Event, dependency: Dependency, stack: AsyncExitStack
    ) -> Any:
        func = dependency.dependency

        kwargs: dict[str, Any] = {}
        tasks: dict[str, asyncio.Task] = {}

        async with asyncio.TaskGroup() as tg:
            for param_name, param in signature(func).parameters.items():
                if isinstance(param.default, Dependency):
                    tasks[param_name] = tg.create_task(
                        resolve_dependency(
                            event=event, dependency=param.default, stack=stack
                        )
                    )

                elif isinstance(event, annotation_event_type(param.annotation)):
                    kwargs[param_name] = event

                elif param.default is not Parameter.empty:
                    kwargs[param_name] = param.default

                elif param.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD):
                    pass

                else:
                    raise ValueError(
                        f"Cannot resolve dependency for parameter '{param_name}' "
                        f"in function '{func.__name__}'. "
                        f"Parameter must have either a default value, be an Event, or be a Dependency"
                    )

        kwargs.update({k: v.result() for k, v in tasks.items()})

        if isasyncgenfunction(func):
            return await stack.enter_async_context(asynccontextmanager(func)(**kwargs))

        elif isgeneratorfunction(func):
            return stack.enter_context(contextmanager(func)(**kwargs))

        elif iscoroutinefunction(func):
            return await func(**kwargs)

        else:
            return func(**kwargs)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        sign = signature(func)

        event_type = ()

        for param in sign.parameters.values():
            event_type += annotation_event_type(param.annotation)

        if matcher:
            if any(
                param
                for param in sign.parameters.values()
                if isinstance(param.default, Dependency)
            ):

                @wraps(func)
                async def wrapper(event: Event, **kwargs) -> Any:
                    if isinstance(event, event_type) and matcher(event):
                        func_kwargs: dict[str, Any] = {}
                        tasks: dict[str, asyncio.Task] = {}

                        async with AsyncExitStack() as stack:
                            async with asyncio.TaskGroup() as tg:
                                for param_name, param in sign.parameters.items():
                                    if isinstance(param.default, Dependency):
                                        tasks[param_name] = tg.create_task(
                                            resolve_dependency(
                                                event=event,
                                                dependency=param.default,
                                                stack=stack,
                                            )
                                        )

                                    elif isinstance(
                                        event, annotation_event_type(param.annotation)
                                    ):
                                        func_kwargs[param_name] = event

                                    elif param.default is not Parameter.empty:
                                        func_kwargs[param_name] = param.default

                                    elif param.kind in (
                                        Parameter.VAR_POSITIONAL,
                                        Parameter.VAR_KEYWORD,
                                    ):
                                        pass

                                    else:
                                        raise ValueError(
                                            f"Cannot resolve dependency for parameter '{param_name}' "
                                            f"in function '{func.__name__}'. "
                                            f"Parameter must have either a default value, be an Event, or be a Dependency."
                                        )

                            return await func(
                                **{k: v.result() for k, v in tasks.items()},
                                **func_kwargs,
                                **kwargs,
                            )

            else:

                @wraps(func)
                async def wrapper(event: Event, **kwargs) -> Any:
                    if isinstance(event, event_type) and matcher(event):
                        return await func(event, **kwargs)

        else:
            if any(
                param
                for param in sign.parameters.values()
                if isinstance(param.default, Dependency)
            ):

                @wraps(func)
                async def wrapper(event: Event, **kwargs) -> Any:
                    if isinstance(event, event_type):
                        func_kwargs: dict[str, Any] = {}
                        tasks: dict[str, asyncio.Task] = {}

                        async with AsyncExitStack() as stack:
                            async with asyncio.TaskGroup() as tg:
                                for param_name, param in sign.parameters.items():
                                    if isinstance(param.default, Dependency):
                                        tasks[param_name] = tg.create_task(
                                            resolve_dependency(
                                                event=event,
                                                dependency=param.default,
                                                stack=stack,
                                            )
                                        )

                                    elif isinstance(
                                        event, annotation_event_type(param.annotation)
                                    ):
                                        func_kwargs[param_name] = event

                                    elif param.default is not Parameter.empty:
                                        func_kwargs[param_name] = param.default

                                    elif param.kind in (
                                        Parameter.VAR_POSITIONAL,
                                        Parameter.VAR_KEYWORD,
                                    ):
                                        pass

                                    else:
                                        raise ValueError(
                                            f"Cannot resolve dependency for parameter '{param_name}' "
                                            f"in function '{func.__name__}'. "
                                            f"Parameter must have either a default value, be an Event, or be a Dependency."
                                        )

                            return await func(
                                **{k: v.result() for k, v in tasks.items()},
                                **func_kwargs,
                                **kwargs,
                            )

            else:

                @wraps(func)
                async def wrapper(event: Event, **kwargs) -> Any:
                    if isinstance(event, event_type):
                        return await func(event, **kwargs)

        PluginManager.plugins[func.__module__].executors.append(wrapper)

        return wrapper

    return decorator
