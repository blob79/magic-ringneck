import hashlib
import random
import subprocess
import time
import uuid
import pytest


@pytest.fixture(scope="module", autouse=True)
def shutdown(ringneck):
    ringneck.run("--shutdown")
    ringneck.run("true")  # supervisord is up
    yield
    ringneck.run("--shutdown")


@pytest.fixture(autouse=True)
def forget(ringneck, shutdown):
    ringneck.run("--forget")


@pytest.fixture(scope="module")
def ringneck(shell):

    class Checked:
        def __init__(self, shell):
            self.shell = shell

        def run(self, *args, **kwargs):
            return self.execute(0, *args, **kwargs)

        def execute(self, returncode, *args):
            a = " ".join(args)
            r = self.shell.run("sh", "-c", f"echo -n '' | ringneck {a}", _timeout=5)
            assert returncode == r.returncode, f"Command args {args} stdout {r.stdout} stderr {r.stderr}"
            return r

    return Checked(shell)


@pytest.fixture
def checked_shell(shell):

    class Checked:
        def __init__(self, shell):
            self.shell = shell

        def run(self, *args, **kwargs):
            return self.execute(0, *args, **kwargs)

        def execute(self, returncode, *args, **kwargs):
            kw = dict(_timeout=5)
            kw.update(**kwargs)
            r = self.shell.run(*args, **kwargs)
            assert returncode == r.returncode, f"Command {args}"
            return r

    return Checked(shell)


def test_need_cmd(ringneck):
    ringneck.execute(1)


def test_cmdline_parsing(ringneck):
    assert "--history" == ringneck.run("--", "echo", "-n", "--history").stdout


def test_caches(ringneck):
    assert ringneck.run("uuidgen") == ringneck.run("uuidgen")
    assert 37 == len(ringneck.run("uuidgen").stdout)  # sanity


def test_force(ringneck):
    assert ringneck.run("uuidgen") != (ringneck.run("--force", "uuidgen") == ringneck.run("uuidgen"))


def test_caches_long(ringneck):
    n = 999999
    cmd = [
        "seq",
        "1",
        str(n),
    ]
    expected = "".join(f"{i}\n" for i in range(1, n + 1))
    assert expected == ringneck.run(*cmd).stdout
    assert expected == ringneck.run(*cmd).stdout


def test_with_broken_pipe(checked_shell, ringneck):
    n = 999999
    assert "1\n" == checked_shell.run("sh", "-c", f"ringneck seq 1 {n} | head -1").stdout
    assert "seq" in ringneck.run("--history").stdout
    assert "1\n" == checked_shell.run("sh", "-c", f"ringneck seq 1 {n} | head -1").stdout

    expected = "".join(f"{i}\n" for i in range(1, n + 1))
    assert expected == checked_shell.run("sh", "-c", f"ringneck seq 1 {n}").stdout


def test_history_contains_command(ringneck):
    ringneck.run("uuidgen")
    ringneck.run("--history").stdout.matcher.fnmatch_lines(["*uuidgen*"])


def test_cache_output_history(ringneck):
    ringneck.run("pwd")
    history_before = ringneck.run("--history").stdout
    ringneck.run("pwd")
    assert history_before == ringneck.run("--history").stdout


def test_success(ringneck):
    cmd = ["bash", "-c", "uuidgen; true"]
    assert ringneck.run(*cmd).stdout == ringneck.run(*cmd).stdout
    assert 37, len(ringneck.run(*cmd).stdout)


def test_fails(ringneck):
    ringneck.execute(1, "false")
    cmd = ["bash", "-c", "uuidgen; false"]
    assert ringneck.execute(1, *cmd).stdout == ringneck.execute(1, *cmd).stdout
    assert 37, len(ringneck.execute(1, *cmd).stdout)


def test_stdout_stderr_interleave(checked_shell):
    expected = "\n".join(("eoe", "oe", "eo", "oe", "eo", "eoe\n"))
    assert expected == checked_shell.run("sh", "-c", "parakeet 2>&1").stdout
    assert expected == checked_shell.run("sh", "-c", "echo -n '' | ringneck parakeet 2>&1").stdout
    assert expected == checked_shell.run("sh", "-c", "echo -n '' | ringneck parakeet 2>&1").stdout

    expected = checked_shell.run("sh", "-c", "parakeet -l2000 2>&1").stdout
    assert expected == checked_shell.run("sh", "-c", "echo -n '' | ringneck parakeet -l2000 2>&1").stdout


def test_stdout_stdin_interleave(checked_shell):
    cmd = [
        "sh",
        "-c",
        "for x in $(seq 1 3) ; do echo $x ; sleep 0.3 ; done | ringneck --stdin --stdout stdbuf -o0 tr '1-9' 'a-i'",
    ]
    expected = "\n".join(["1", "a", "2", "b", "3", "c", ""])
    assert expected == checked_shell.run(*cmd).stdout
    assert expected == checked_shell.run(*cmd).stdout


def test_random_bytes(tmp_path, ringneck):
    f = tmp_path / "data"
    data = random.randbytes(1 << 20)
    with f.open("wb") as ff:
        assert 1 << 20 == ff.write(data)
    assert (
        hashlib.md5(data).hexdigest() == ringneck.run("sh", "-c", f"echo -n '' | ringneck cat {f}| md5sum").stdout[:32]
    )


def test_killing_ringneck_stops_command(shell, ringneck):
    marker = uuid.uuid4().hex
    p = subprocess.Popen(["ringneck", "python3", "-c", f"import time; time.sleep(3);#{marker}"])
    time.sleep(0.5)  # Let the command actually block io for some time
    p.kill()
    time.sleep(0.5)  # Let nestbox some time to kill the process
    p.wait()
    g = shell.run("pgrep", "-f", marker)
    assert 1 == g.returncode, f"{p.pid} {g.stdout}"
    ringneck.run("--history").stdout.matcher.no_fnmatch_line(f"*{marker}*")


def test_invalid_cmd(ringneck):
    cmd = "i_am_not_there"
    ringneck.execute(1, cmd)
    ringneck.run("--history").stdout.matcher.no_fnmatch_line(f"*{cmd}*")


def test_stdin(checked_shell):
    assert "1" == checked_shell.run("sh", "-c", "echo -n 1 | ringneck cat", _timeout=1).stdout
    assert "1" == checked_shell.run("sh", "-c", "echo -n 1 | ringneck cat", _timeout=1).stdout


def test_long_stdin(shell):
    cmd = "seq 1 900000 | ringneck cat | tail -1"
    assert "900000\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout
    assert "900000\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout


def test_long_tail_stdin(shell):
    cmd = "seq 1 1000000 | ringneck tail -1"
    assert "1000000\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout
    assert "1000000\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout


def test_long_tail_show_stdin(shell):
    cmd = "seq 1 1000000 | ringneck --stdin cat | tail -1"
    assert "1000000\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout
    assert "1000000\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout


def test_long_stdin_broken_pipe(shell):
    cmd = "seq 1 50000 | ringneck cat | head -2"
    assert "1\n2\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout
    assert "1\n2\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout

    cmd = "ringneck cat | tail -1"
    assert "50000\n" == shell.run("sh", "-c", cmd, _timeout=2).stdout


def test_get_key(ringneck):
    expected = ringneck.run("pwd").stdout
    assert expected == ringneck.run("--key", "2a0a3031d9c37d89ab8184bd7e79a82c").stdout


def test_invalid_key(ringneck):
    ringneck.run("pwd").stdout
    actual = ringneck.execute(1, "--key", "666")
    assert "" == actual.stdout
    assert "Invalid key" == actual.stderr


def test_output_stdout(ringneck):
    expected = "oo\noo\noo"
    assert expected == ringneck.run("--stdout", "ringneck", "parakeet").stdout
    assert "" == ringneck.run("--stdout", "ringneck", "parakeet").stderr

    k = "d2de781838a621ca546ea264517adc65"
    assert expected == ringneck.run("--stdout", "--key", k).stdout
    assert "" == ringneck.run("--stdout", "--key", k).stderr


def test_output_stderr(ringneck):
    expected = "ee\nee\nee\nee\n"
    assert "" == ringneck.run("--stderr", "ringneck", "parakeet").stdout
    assert expected == ringneck.run("--stderr", "ringneck", "parakeet").stderr
    cmd = "--stderr", "--key", "d2de781838a621ca546ea264517adc65"
    assert "" == ringneck.run(*cmd).stdout
    assert expected == ringneck.run(*cmd).stderr


def test_output_stdin(checked_shell, ringneck):
    cmd = "sh", "-c", "echo -n 1 | ringneck --stdin echo -n out"
    assert "1" == checked_shell.run(*cmd, _timeout=1).stdout
    assert "" == checked_shell.run(*cmd, _timeout=1).stderr

    k = "2b0d740593a1d44e081461e5d3da1cf3"
    assert "1" == ringneck.run("--stdin", "--key", k).stdout
    assert "out" == ringneck.run("--stdout", "--key", k).stdout


def test_forget_history(ringneck):
    assert "" == ringneck.run("--history").stdout
    ringneck.run("pwd")
    assert len(ringneck.run("--history").stdout) > 0
    ringneck.run("--forget")
    assert 0 == len(ringneck.run("--history").stdout)


def test_fish_source(checked_shell):
    checked_shell.execute(1, "/usr/bin/fish", "-c", "type +")
    checked_shell.execute(1, "/usr/bin/fish", "-c", "type ++")

    checked_shell.execute(0, "/usr/bin/fish", "-c", "ringneck --init | source ; type +")
    checked_shell.execute(0, "/usr/bin/fish", "-c", "ringneck --init | source ; type ++")

    cmd = ["/usr/bin/fish", "-c", "ringneck --init | source ; + uuidgen"]
    assert checked_shell.execute(0, *cmd).stdout == checked_shell.execute(0, *cmd).stdout
