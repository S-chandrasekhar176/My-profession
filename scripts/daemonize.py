#!/usr/bin/env python3
"""Double-fork daemonizer — run a command detached from the invoking shell.

Usage:
    python3 daemonize.py <logfile> <command> [args...]

Why this exists in this repo's sandbox/dev environment: some hosting
sandboxes reap all descendant processes of a tool shell when the shell
exits. A classic double-fork daemon (reparented to init, own session)
survives that cleanup, so the backend/frontend keep running.

On a normal machine you can equally use `nohup`/`systemd`/`start.sh`.
"""
import os
import sys


def main() -> None:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: daemonize.py <logfile> <command> [args...]\n")
        sys.exit(2)

    logfile = sys.argv[1]
    cmd = sys.argv[2:]

    # First fork: parent exits immediately, child continues.
    if os.fork() > 0:
        sys.exit(0)

    # New session, detached from controlling terminal.
    os.setsid()

    # Second fork: guarantee the daemon can never re-acquire a terminal
    # and is reparented to init (PPID=1).
    if os.fork() > 0:
        sys.exit(0)

    # Redirect stdout/stderr into the logfile, then replace this process
    # with the actual command.
    lf = open(logfile, "ab", buffering=0)
    os.dup2(lf.fileno(), 1)
    os.dup2(lf.fileno(), 2)
    os.close(lf.fileno()) if lf.fileno() > 2 else None

    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
