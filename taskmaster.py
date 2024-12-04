import asyncio
from typing import (Any, AsyncIterator, Awaitable, Callable, Iterable,
                    Optional, Sequence, Tuple, Type, TypeVar, cast)

from contextlib import AbstractAsyncContextManager

T = TypeVar("T")

class amap(AsyncIterator[T], Awaitable[Sequence[T]]):
    """Singular map for asyncio tasks that carry an unknown amount of objects with it
    with a set level of conccurrency so that iterables provided can be infinate."""
    def __init__(self, func: Callable[..., Awaitable[T]],  iterables: Sequence[Iterable[Any]] , concurrency:int = 16) -> None:
        self._func = func 
        self._queue = asyncio.Queue()
        self._concurrency = concurrency
        self._iters = zip(iterables)
        self._running = True
        self._task = asyncio.ensure_future(self.__loop())
        self.__mapped = self.__results()

    async def __loop(self):
        pending: set[asyncio.Future[T]] = set()

        def on_done(fut:asyncio.Future[T]):
            nonlocal pending
            pending.remove(fut)
            exception = fut.exception()
            if not exception:
                self._queue.put_nowait((fut.result(), None))
            else:
                self._queue.put_nowait((None, exception))


        while self._running or pending:
            while self._running and (len(pending) < self._concurrency):
                try:
                    item = next(self._iters)
                except StopIteration:
                    self._running = False
                    break

                fut = asyncio.ensure_future(self._func(*item))
                fut.add_done_callback(on_done)
                pending.add(fut)

            await asyncio.sleep(0.005)
    
    async def __results(self):
        while not self._task.done() or not self._queue.empty():
            try:
                item, exc = cast(Tuple[T, Optional[BaseException]], self._queue.get_nowait())
                if exc:
                    raise exc
                yield item
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.005)
        

    async def __collect(self) -> Sequence[T]:
        return [i async for i in self.__mapped]

    def __await__(self) -> Sequence[T]:
        return self.__collect().__await__()

    async def __aiter__(self) -> AsyncIterator[T]:
        async for i in self.__mapped:
            yield i 
    
    async def __anext__(self) -> Awaitable[T]:
        return self.__mapped.__anext__()

# Ripped from the cotextlib libarary and made async...
class suppress(AbstractAsyncContextManager):
    """Async Context manager to suppress specified exceptions

    After the exception is suppressed, execution proceeds with the next
    statement following the with statement.

::

        async with suppress(FileNotFoundError):
             os.remove(somefile)
         # Execution still resumes here if the file was already removed
    """

    def __init__(self, *exceptions):
        self._exceptions = exceptions

    async def __aenter__(self):
        pass

    async def __aexit__(self, exctype, excinst, exctb):
        # Unlike isinstance and issubclass, CPython exception handling
        # currently only looks at the concrete type hierarchy (ignoring
        # the instance and subclass checking hooks). While Guido considers
        # that a bug rather than a feature, it's a fairly hard one to fix
        # due to various internal implementation details. suppress provides
        # the simpler issubclass based semantics, rather than trying to
        # exactly reproduce the limitations of the CPython interpreter.
        #
        # See http://bugs.python.org/issue12029 for more details
        return exctype is not None and issubclass(exctype, self._exceptions)
