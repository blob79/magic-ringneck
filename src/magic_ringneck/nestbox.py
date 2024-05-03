import argparse
import asyncio
import datetime
import hashlib
import json
import logging
import sys
from typing import Any, AsyncIterator
from magic_ringneck.directories import NESTBOX_SOCK
from magic_ringneck.message import (
    BUFFER_SIZE,
    Prefix,
    Protocol,
    WriterProtocol,
    CompositeProtocol,
    CollectProtocol,
    recv_binary,
    abbreviate,
    read_incoming,
)

lock = asyncio.Lock()
cache: dict[str, dict[Any, Any]] = dict()


def key(cmd: str) -> str:
    h = hashlib.new("md5")
    for c in cmd:
        h.update(c.encode())
        h.update(b"\0")
    return h.hexdigest()


STOP = "END_QUEUE"
TIMEOUT = "TIMEOUT"


async def read_cmd_output(
    queue: asyncio.Queue[tuple[Prefix, bytes] | str], prefix: Prefix, stream: asyncio.streams.StreamReader
) -> None:
    while data := await stream.read(n=BUFFER_SIZE):
        await queue.put((prefix, data))
    await queue.put(STOP)


async def append_output(
    proc: asyncio.subprocess.Process, queue: asyncio.Queue[tuple[Prefix, bytes] | str], protocol: Protocol
) -> None:
    try:
        stop = 0
        while stop < 2:
            try:
                v = await asyncio.wait_for(queue.get(), timeout=0.3)
            except TimeoutError:
                v = TIMEOUT

            if v == STOP:
                stop += 1
            elif v == TIMEOUT:
                logging.debug("Send keep alive %s", proc)
                await protocol.keep_alive()
            else:
                assert isinstance(v, tuple)
                prefix, data = v
                await protocol.send(prefix, data)
                logging.debug("Send snippet %s %s", proc, abbreviate(data))
    except ConnectionError as e:
        logging.info("Terminate proc %s cannot write %s", proc, e, exc_info=True)
        proc.terminate()
        await protocol.terminate()


async def devnull_stdin(rc: AsyncIterator[tuple[int, bytes]]) -> None:
    async for prefix, d in rc:
        logging.debug("Pipe stding %s len %s to /dev/null", repr(prefix), len(d))
        if prefix == Prefix.STDIN:
            pass
        elif prefix == Prefix.EXIT:
            logging.info("devnull_stdin break")
            break
        else:
            raise ValueError()


async def pipe_stdin(
    rc: AsyncIterator[tuple[int, bytes]],
    proc: asyncio.subprocess.Process,
    queue: asyncio.Queue[tuple[Prefix, bytes] | str],
) -> None:
    async for prefix, d in rc:
        logging.debug("Pipe stding %s %s", repr(prefix), bytes(d))
        assert proc.stdin
        if prefix == Prefix.STDIN:
            stdin = bytes(d)
            await queue.put((prefix, stdin))
            proc.stdin.write(stdin)
        elif prefix == Prefix.EXIT:
            proc.stdin.close()
            break
        else:
            raise ValueError()


async def run(cmd: list[str], cwd: str, rc: AsyncIterator[tuple[int, bytes]], protocol: Protocol) -> int:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        logging.info("Started process %s", proc)
        queue: asyncio.Queue[tuple[Prefix, bytes] | str] = asyncio.Queue()
        asyncio.create_task(pipe_stdin(rc, proc, queue))
        assert proc.stderr and proc.stdout
        await asyncio.gather(
            append_output(proc, queue, protocol),
            read_cmd_output(queue, Prefix.STDOUT, proc.stdout),
            read_cmd_output(queue, Prefix.STDERR, proc.stderr),
        )
        await proc.wait()
        logging.info("Done %s", proc)
        assert proc.returncode is not None
        return proc.returncode

    except Exception as e:
        await protocol.exit(1)
        await protocol.terminate()
        logging.exception("Process failed %s", e)
        return 1


async def get_request(rc: AsyncIterator[tuple[int, bytes]]) -> Any:
    prefix, data = await anext(rc)
    assert prefix == Prefix.STDOUT
    message = json.loads(bytes(data).decode())
    logging.info("HANDLE %s (%s bytes)", message.keys(), len(data))
    logging.debug("HANDLE details %s", message)
    return message


async def handle_key(writer: asyncio.StreamWriter, message: dict[Any, Any]) -> None:
    writer_proto = WriterProtocol(writer)
    k = message["KEY"]
    async with lock:
        v = cache.get(
            k,
            dict(
                output=[(Prefix.STDERR, b"Invalid key")],
                returncode=1,
            ),
        )
        for prefix, data in v["output"]:
            await writer_proto.send(prefix, data)

        await writer_proto.exit(v["returncode"])


async def handle_get(
    writer: asyncio.StreamWriter, message: dict[Any, Any], rc: AsyncIterator[tuple[int, bytes]]
) -> None:
    writer_proto = WriterProtocol(writer)
    get = message["GET"]
    k = key(get["cmd"])
    async with lock:
        v = cache.get(k, dict())
    if not get.get("force", False) and v:
        logging.info("Use cached version for %s", k)
        asyncio.create_task(devnull_stdin(rc))
        for prefix, data in v["output"]:
            await writer_proto.send(prefix, data)
        await writer.drain()
    else:
        logging.info("Run command for %s", k)
        protocol = CompositeProtocol([writer_proto, CollectProtocol()])
        returncode = await run(get["cmd"], get["cwd"], rc, protocol)
        if protocol.is_terminated():
            logging.info("Not storing.")
            return
        payload = dict(
            cmd=get["cmd"],
            output=protocol.collected(),
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            returncode=returncode,
            cwd=get["cwd"],
        )
        async with lock:
            cache[k] = payload
        v = payload
    await writer_proto.exit(v["returncode"])


async def handle_history(writer: asyncio.StreamWriter) -> None:
    async with lock:
        view = {
            k: dict(
                cmd=v["cmd"],
                timestamp=v["timestamp"],
                returncode=v["returncode"],
                cwd=v["cwd"],
            )
            for k, v in cache.items()
        }
    out = json.dumps(view).encode()
    writer.write(out)
    logging.info("SEND nestbox (%s bytes)", len(out))
    writer.close()
    await writer.wait_closed()


async def handle_forget(writer: asyncio.StreamWriter) -> None:
    out = json.dumps(dict()).encode()
    writer.write(out)
    logging.info("Forget history of %s items.", len(cache.items()))
    async with lock:
        cache.clear()
    writer.close()
    await writer.wait_closed()


async def handle_incoming(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        rc = recv_binary(read_incoming(reader))
        message = await get_request(rc)
        if "GET" in message:
            await handle_get(writer, message, rc)
        elif "KEY" in message:
            await handle_key(writer, message)
        elif "HISTORY" in message:
            await handle_history(writer)
        elif "FORGET" in message:
            await handle_forget(writer)
        logging.info("Handled")
    except Exception as e:
        logging.exception("ERROR %s", e)


async def go() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    logging.info("STARTING")
    server = await asyncio.start_unix_server(handle_incoming, path=NESTBOX_SOCK)
    async with server:
        await server.serve_forever()
    logging.info("END")


def main() -> None:
    asyncio.run(go())
