import asyncio
from magic_ringneck.message import WriterProtocol, Prefix, recv_binary
from hypothesis import given, strategies as st


class Writer:
    def __init__(self):
        self.bs = bytearray()

    def write(self, bs):
        self.bs += bs

    async def drain(self):
        pass


def writer(expected):
    writer = Writer()
    p = WriterProtocol(writer)

    async def do():
        for v in expected:
            if v == Prefix.KEEP_ALIVE:
                await p.keep_alive()
            else:
                prefix, data = v
                if prefix in [Prefix.STDOUT, Prefix.STDERR, Prefix.STDIN]:
                    await p.send(prefix, data)
                elif prefix == Prefix.EXIT:
                    await p.exit(data[0])
                else:
                    raise ValueError()

    asyncio.run(do())
    return writer.bs


async def recv_bin(iter):
    return [x async for x in recv_binary(iter)]


async def aiter(list):
    for x in list:
        yield x


@given(
    st.lists(
        st.one_of(
            st.just(Prefix.KEEP_ALIVE),
            st.tuples(
                st.sampled_from([Prefix.STDERR, Prefix.STDOUT]),
                st.binary(),
            ),
        )
    ),
    st.lists(st.tuples(st.just(Prefix.STDIN), st.binary()), max_size=1),
    st.integers(min_value=0, max_value=255),
    st.lists(st.integers(min_value=0)),
)
def test_send_recv_message(stdout_stderr, stdin, returncode, splits):
    ext = [
        (
            Prefix.EXIT,
            bytes([returncode]),
        )
    ]
    into = stdin + stdout_stderr + ext
    binary = writer(into)

    idx_split = sorted(set([0, len(binary)] + [s % len(binary) for s in splits]))
    split_binary = [binary[l:r] for l, r in zip(idx_split, idx_split[1:])]
    assert binary == b"".join(split_binary)

    assert [e for e in into if e != Prefix.KEEP_ALIVE] == [
        (
            prefix,
            bytes(d),
        )
        for (prefix, d) in asyncio.run(recv_bin(aiter(split_binary)))
    ]
