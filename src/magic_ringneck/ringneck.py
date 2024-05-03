import argparse
import asyncio
import json
import os
import sys
import logging

from typing import Any

from magic_ringneck import starter
from magic_ringneck.directories import NESTBOX_SOCK
from magic_ringneck.message import Prefix, Protocol, abbreviate, read_incoming, recv_binary, ClientProtocol, BUFFER_SIZE


async def connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(NESTBOX_SOCK)
    return reader, writer


async def send_json(message: dict[str, Any]) -> Any:
    reader, writer = await connect()
    proto = ClientProtocol(writer)
    await proto.send(Prefix.STDOUT, json.dumps(message).encode())
    data = bytearray()
    while True:
        if reader.at_eof():
            break
        read = await reader.read(BUFFER_SIZE)
        data += read

    writer.close()
    await writer.wait_closed()
    return json.loads(data)


async def get_stdin(proto: Protocol) -> None:
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        try:
            n = await reader.read(BUFFER_SIZE)
            try:
                await proto.send(Prefix.STDIN, n)
            except:
                logging.debug("Stopped writting stdin to nestbox")
                return

            if not n:
                break
        except (asyncio.CancelledError, BrokenPipeError) as e:
            logging.debug("Error %s", e, exc_info=True)
            return

    try:
        await proto.exit(0)
    except (asyncio.CancelledError, BrokenPipeError, ConnectionResetError) as e:
        logging.debug("Error %s", e, exc_info=True)


async def send_binary(message: dict[str, Any], enabled_prefix: Prefix) -> None:
    reader, writer = await connect()
    proto = ClientProtocol(writer)
    await proto.send(Prefix.STDOUT, json.dumps(message).encode())

    stdin_task = asyncio.create_task(get_stdin(proto))

    async for prefix, d in recv_binary(read_incoming(reader)):
        if prefix == Prefix.EXIT:
            logging.debug("Cancel stdin (done %s).", stdin_task.done())
            stdin_task.cancel()
            await stdin_task
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError) as e:
                logging.debug("Error %s", e, exc_info=True)
            logging.debug("EXIT 1")

            sys.exit(d[0])
        try:
            logging.debug("Stdin done %s", stdin_task.done())
            logging.debug("Read %s %s %s", repr(Prefix(prefix)), len(d), abbreviate(d))
            if (Prefix.STDOUT in enabled_prefix and prefix == Prefix.STDOUT.byte()) or (
                Prefix.STDIN in enabled_prefix and prefix == Prefix.STDIN.byte()
            ):
                while d:
                    x = sys.stdout.buffer.write(d)
                    d = d[x:]
            elif Prefix.STDERR in enabled_prefix and prefix == Prefix.STDERR.byte():
                while d:
                    x = sys.stderr.buffer.write(d)
                    d = d[x:]

        except BrokenPipeError as e:
            # Keep on running even if the pipe is broken
            # `ringneck cmd | head` closes the pipe early and we still want succeed in the
            # execution
            logging.debug("Error %s", e, exc_info=True)

    try:
        await stdin_task
    except (BrokenPipeError, ConnectionResetError) as e:
        logging.debug("Error %s", e, exc_info=True)


async def go(cmd: list[str], args: argparse.Namespace) -> None:
    if args.init:
        starter.init_fish()
        sys.exit(0)

    if args.shutdown:
        starter.shutdown_supervisord()
        sys.exit(0)

    if args.forget:
        await send_json(dict(FORGET=True))
        sys.exit(0)

    if args.history:
        hist = await send_json(dict(HISTORY=True))
        history = sorted(
            (
                (
                    v["timestamp"],
                    k,
                    v["returncode"],
                    v["cwd"],
                    v["cmd"],
                )
                for k, v in hist.items()
            )
        )
        for ts, k, returncode, cwd, cmd_str in history:
            print(ts, k, returncode, cwd, cmd_str)
        sys.exit(0)

    enabled_prefix = Prefix(0)
    if args.stdin:
        enabled_prefix |= Prefix.STDIN
    if args.stdout:
        enabled_prefix |= Prefix.STDOUT
    if args.stderr:
        enabled_prefix |= Prefix.STDERR
    if not enabled_prefix:
        enabled_prefix = Prefix.STDOUT | Prefix.STDERR

    if args.key:
        await send_binary(dict(KEY=args.key), enabled_prefix)

    if not cmd:
        raise ValueError("Ringneck needs a command.")
    get = dict(
        GET=dict(
            cmd=cmd,
            cwd=os.getcwd(),
            force=args.force,
        )
    )
    try:
        await send_binary(get, enabled_prefix)
    except (ConnectionRefusedError, FileNotFoundError):
        starter.start_nestbox()
        await send_binary(get, enabled_prefix)


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--forget", action="store_true")
    parser.add_argument("--shutdown", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--key")
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--stderr", action="store_true")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--init", action="store_true")

    args, cmd = parser.parse_known_args()
    if cmd and cmd[0] == "--":
        del cmd[0]
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(threadName)s %(name)s %(funcName)s %(message)s",
        stream=sys.stderr,
        level=logging.DEBUG if args.debug else logging.ERROR,
    )
    asyncio.run(go(cmd, args))
