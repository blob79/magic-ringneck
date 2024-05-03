import argparse
import sys
import time
import itertools

# Parakeet produces deterministially interleaved stdout and stderr output. It depends
# on the processes output buffering what you see on the command line (`python -u`).

O_PERIOD = 3  # produces o unless '\n'
O_N_PERIOD = 7
E_PERIOD = 2  # produces e unless '\n'
E_N_PERIOD = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--length", default=20, type=int)
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-s", "--sleep", default=0.001, type=float, help="Wait time between output.")
    args = parser.parse_args()

    debug, length, sleep = args.debug, args.length, args.sleep

    os = (("\n" if i % O_N_PERIOD == 0 else ("o" if i % O_PERIOD == 0 else None)) for i in itertools.count(1))
    es = (("\n" if i % E_N_PERIOD == 0 else ("e" if i % E_PERIOD == 0 else None)) for i in itertools.count(1))
    cs = itertools.count(1)
    i = 0
    for c, o, e in zip(cs, os, es):
        time.sleep(sleep)
        if debug:
            print("")
            print(c, " ", end="")
            o = "n" if o == "\n" else o
            e = "n" if e == "\n" else e
        if o:
            i += 1
            if debug:
                print(o, " ", end="")
            else:
                print(o, sep="", end="", file=sys.stdout)
        if i == length:
            break
        if e:
            i += 1
            if debug:
                print(e, " ", end="")
            else:
                print(e, sep="", end="", file=sys.stderr)
        if i == length:
            break
