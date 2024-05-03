# Ringneck

Ringneck is a Unix command line tool that caches the output of programs it runs.


When you run
```
ringneck md5sum myfile
```
this executes md5sum on the first run and shows stdout and stderr as you
would expect. On the second execution is will replay the output of the first execution.

Ringneck caches command output for successful and failing executions.

## Installation

You can use plain `pip` or [`pipx`](https://github.com/pypa/pipx/blob/main/README.md)
to install `ringneck`.

```
pipx install supervisor==4.2.5
pipx install magic-ringneck
```

## Shell integration

### Fish

You can source the fish `+` and `++` functions with:
```
ringneck --init | source
```
and run Ringneck like this:
```
+ pwd
+ ls -la
```

## Performance Goals

Ringneck should have reasonable performance. It should be be usable in any context
without much of a downside.

Non goal: At the moment I'm pretty unconcerned with the memory utilization.

Metrics:
1. 25% time overhead for 10,000 1kb files running md5sum.
1. For 1GB files throughput is roughly ~500Mb/sec.

## Todos


1. Change + function to do nothing is there aren't any arguments to ringneck
1. Support user input from the terminal.
1. Stdin can be infinite (`tail -f`)
1. Tail mode - write to cache while program running.
1. In force mode - store all outputs and make them available in the history
1. Replay with exact timing. With `--replay` you can reproduce the exact timing - the program will take as long on the first execution only skipping the command execution and taking the output from cache.
1. Security - Check who can connect to the daemon (only the same local user)
1. Include cwd in the command key
1. Let the user decide what to use in the key (for example only the command line ignore the cwd).
1. Process return code can be negative
1. Nestbox catch all exceptions and send error
1. Rust Ringneck implementation to avoid the 100ms Python startup penalty.
1. Multiplexing - Ringneck is only a frontend. The user can start the same command line multiple times and
receives the same output. Nestbox makes sure that the command only runs once.
1. Ttl - keep result for a day. Keep the result for a terminal session.


## Output handling

Ringneck tries to replay the stdout, stderr and stdin ordering as it sees it on the
first execution. There are the options `--stdout`, `--stderr` and `--stdin` to show
specific output. Stdout and stderr are the default output. Stdin is the data piped
into the command.

Ringneck reads all stdin input, i.e. if you're input doesn't stop Ringneck won't either.
Ringneck won't stop because of the output pipeline being broken. It always caches the complete
input and output it sees, independently if it was consumed. Example:
`ringneck cat a | head -1` caches the complete cat output and won't stop when head
stops reading.

## History management

You can use `--history` to query the cached command history.
`--shutdown` stops the Nestbox daemon and `--forget` clears the cache.
If you run `ringneck --force` Ringneck ignores the history and always runs the command.

### Access by key

Every command in the history has a key. You can retrieve the cached output using this key.
This can be useful with `--stdin`, `--stdout`, `--stderr`.

```
$ echo 1 | ringneck tr "1-9" "a-i"
a

$ ringneck --history
2024-04-21T11:59:03.124610+00:00 4597d53c7cfbd758fc755a5f3c4aa6b3 0 /home/thomas/Development/ringneck ['tr', '1-9', 'a-i']

$ ringneck --key 4597d53c7cfbd758fc755a5f3c4aa6b3
a

$ ringneck --stdin --key  4597d53c7cfbd758fc755a5f3c4aa6b3
1
```

## Nestbox daemon Design

### Caching

In the background, Ringneck uses the its own Nestbox daemon to store the command output in memory.
When you stop the Nestbox daemon the cache is gone. The motivation for this
design to be able to cache sensitive output. Unless you enable detailed logging
or the daemon process is swapped to disk, the command output
is never written to disk. The daemon starts lazily on the first Ringneck invocation.

### Process Management

Nestbox also runs the commands for Ringneck. So if you run `ringneck ls -la` Ringneck
forwards this information to Nestbox that runs the `ls -la`.


If you kill the Ringneck process Nestbox stops the command is runs for Ringneck. Nestbox
doesn't cache the output in this case.

### Socket protocol

Ringneck connects to Nestbox with a Unix socket. They talk a binary protocol that adds minimal
overhead to the raw binary data.


### Design Motivation

The motivation for this design is that
Ringneck can a very small process optimized for minimal startup overhead. It only sends
and receives data to and from Nestbox. Nestbox is long running and can be optimized for throughput (support 1GB output).

The downside designing Ringneck this way is that getting the process environment
(cmd, env, cwd, terminal) right in Nestbox is more effort than in Ringneck.

Future - Ringneck can be reimplemented in Rust and Nestbox stays in Python.

Future - Ringneck is only a frontend. The user can start the same command line multiple times and
receives the same output. Nestbox makes sure that it's only executed once in parallel.

Future - Tail implementation is easy. Ringneck asks Nestbox for a stream of the output
and as long as the process runs Ringneck receives the output.
