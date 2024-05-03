import os
import subprocess
import retry

from magic_ringneck.directories import RINGNECK_DIR, SUPERVISOR_CONF


def start_supervisor() -> None:
    os.makedirs(RINGNECK_DIR, exist_ok=True)
    sock_file = RINGNECK_DIR / "supervisord.sock"
    pidfile = RINGNECK_DIR / "supervisord.pid"
    logfile = RINGNECK_DIR / "supervisord.log"
    cfg = (
        "[supervisord]\n"
        f"pidfile={pidfile}\n"
        f"logfile={logfile}\n"
        "\n"
        "[unix_http_server]\n"
        f"file={sock_file}\n"
        "\n"
        "[supervisorctl]\n"
        f"serverurl=unix://{sock_file}\n"
        f"\n"
        "[rpcinterface:supervisor]\n"
        "supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface\n"
        "\n"
        "[program:nestbox]\n"
        "command=nestbox\n"
    )

    with open(SUPERVISOR_CONF, "w", encoding="UTF-8") as fp:
        fp.write(cfg)

    p = subprocess.run(
        ["supervisorctl", "-c", SUPERVISOR_CONF, "pid"],
        capture_output=True,
        check=False,
    )
    if p.returncode == 0:
        return
    subprocess.run(["supervisord", "-c", SUPERVISOR_CONF], check=True)


def shutdown_supervisord() -> None:
    subprocess.run(
        ["supervisorctl", "-c", SUPERVISOR_CONF, "shutdown"],
        check=False,
    )


class NotUpError(Exception):
    pass


@retry.retry(delay=0.3, tries=10)
def nestbox_is_up() -> None:
    out = subprocess.run(
        ["supervisorctl", "-c", SUPERVISOR_CONF, "status", "nestbox"],
        capture_output=True,
        check=True,
    ).stdout
    if "RUNNING" in out.decode():
        return
    raise NotUpError(f"Nestbox isn't up yet: {out!r}")


def start_nestbox() -> None:
    start_supervisor()
    nestbox_is_up()


def init_fish() -> None:
    fish = (
        "function +\n"
        "PYTHONUNBUFFERED=1 ringneck $argv\n"
        "end\n"
        "function ++\n"
        "PYTHONUNBUFFERED=1 ringneck --force $argv\n"
        "end\n"
    )
    print(fish)
