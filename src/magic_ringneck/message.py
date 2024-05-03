import abc
import asyncio
import logging

from abc import abstractmethod
from enum import IntFlag, auto
from typing import Iterable, AsyncIterator

BUFFER_SIZE = (1 << 16) - 1

logger = logging.getLogger()


def abbreviate(bs: bytes | memoryview) -> bytes:
    bs = memoryview(bs)
    n = 100
    if len(bs) < n:
        return bytes(bs)
    return bytes(bs[: (n // 2)]) + b"..." + bytes(bs[(-n // 2) :])


class Prefix(IntFlag):
    STDOUT = auto()
    STDERR = auto()
    EXIT = auto()
    KEEP_ALIVE = auto()
    STDIN = auto()

    def bytes(self) -> bytes:
        return bytes([self.value])

    def byte(self) -> int:
        return self.value


def encode_data(prefix: Prefix, data: bytes | memoryview) -> bytes:
    return prefix.bytes() + bytes([len(data) >> 8, len(data) & 255]) + data


class Protocol(abc.ABC):

    @abstractmethod
    async def keep_alive(self) -> None: ...

    @abstractmethod
    async def send(self, prefix: Prefix, data: bytes | memoryview) -> None: ...

    @abstractmethod
    async def exit(self, returncode: int) -> None: ...

    @abstractmethod
    async def terminate(self) -> None: ...

    @abstractmethod
    def is_terminated(self) -> bool: ...

    @abstractmethod
    def collected(self) -> list[tuple[Prefix, bytes | memoryview]]: ...


class CompositeProtocol(Protocol):
    def __init__(self, protocols: Iterable[Protocol]):
        self.protocols = protocols

    async def keep_alive(self) -> None:
        for p in self.protocols:
            await p.keep_alive()

    async def send(self, prefix: Prefix, data: bytes | memoryview) -> None:
        for p in self.protocols:
            await p.send(prefix, data)

    async def exit(self, returncode: int) -> None:
        for p in self.protocols:
            await p.exit(returncode)

    async def terminate(self) -> None:
        for p in self.protocols:
            await p.terminate()

    def is_terminated(self) -> bool:
        for p in self.protocols:
            if p.is_terminated():
                return True
        return False

    def collected(self) -> list[tuple[Prefix, bytes | memoryview]]:
        for p in self.protocols:
            if p.collected():
                return p.collected()
        return []


class WriterProtocol(Protocol):
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer

    async def sink(self, v: bytes | memoryview) -> None:
        self.writer.write(v)
        await self.writer.drain()

    async def keep_alive(self) -> None:
        await self.sink(Prefix.KEEP_ALIVE.bytes())

    async def send(self, prefix: Prefix, data: bytes | memoryview) -> None:
        bs = encode_data(prefix, data)
        await self.sink(bs)
        logger.debug("SEND (%s bytes)", len(bs))
        logger.debug("SEND %s", abbreviate(bs))

    async def exit(self, returncode: int) -> None:
        await self.sink(bytes([Prefix.EXIT.byte(), returncode]))
        logging.debug("EXIT %s", returncode)

    async def terminate(self) -> None:
        pass

    def is_terminated(self) -> bool:
        return False

    def collected(self) -> list[tuple[Prefix, bytes | memoryview]]:
        return []


class ClientProtocol(Protocol):
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer

    async def sink(self, v: bytes | memoryview) -> None:
        self.writer.write(v)
        await self.writer.drain()
        logger.debug("SEND (%s bytes)", len(v))
        logger.debug("SEND %s", abbreviate(v))

    async def keep_alive(self) -> None:
        await self.sink(Prefix.KEEP_ALIVE.bytes())

    async def send(self, prefix: Prefix, data: bytes | memoryview) -> None:
        bs = encode_data(prefix, data)
        await self.sink(bs)

    async def exit(self, returncode: int) -> None:
        await self.sink(bytes([Prefix.EXIT.byte(), returncode]))

    async def terminate(self) -> None:
        pass

    def is_terminated(self) -> bool:
        return False

    def collected(self) -> list[tuple[Prefix, bytes | memoryview]]:
        return []


class CollectProtocol(Protocol):
    def __init__(self) -> None:
        self.bs: list[tuple[Prefix, bytes | memoryview]] = []
        self.returncode = 0
        self.terminated = False

    async def keep_alive(self) -> None:
        pass

    async def send(self, prefix: Prefix, data: bytes | memoryview) -> None:
        self.bs.append(
            (
                prefix,
                data,
            )
        )

    async def exit(self, returncode: int) -> None:
        self.returncode = returncode

    async def terminate(self) -> None:
        self.terminated = True

    def is_terminated(self) -> bool:
        return self.terminated

    def collected(self) -> list[tuple[Prefix, bytes | memoryview]]:
        return self.bs


async def read_incoming(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
    while True:
        if reader.at_eof():
            break
        read = await reader.read(BUFFER_SIZE)
        if read:
            logging.debug("Read %s", abbreviate(read))
            yield read
        else:
            break


async def recv_binary(iter_bytes: AsyncIterator[bytes]) -> AsyncIterator[tuple[Prefix, memoryview]]:
    logger.debug("RECV")
    data: bytearray | memoryview = bytearray()

    async def get(end: int) -> memoryview:
        nonlocal data
        while len(data) < end:
            n = await anext(iter_bytes)
            data = bytearray(data) + n
        r = memoryview(data)[:end]
        data = memoryview(data)[end:]
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Get %s %s", end, abbreviate(r))
        return r

    while True:
        try:
            first = await get(1)
            prefix = first[0]
            if prefix == Prefix.KEEP_ALIVE.byte():
                continue
            if prefix == Prefix.EXIT.byte():
                r = (Prefix.EXIT, await get(1))
                logger.debug("RECV %s %s", r[0], abbreviate(r[1]))
                yield r
                break
            length = await get(2)
            r = (
                Prefix(prefix),
                await get(length[0] << 8 | length[1]),
            )
            logger.debug("RECV %s %s", r[0], abbreviate(r[1]))
            yield r
        except (BrokenPipeError, ConnectionResetError, StopAsyncIteration) as e:
            logger.debug("Error %s", e, exc_info=True)
            break
