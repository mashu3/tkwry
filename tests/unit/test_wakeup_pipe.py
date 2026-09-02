"""Regression tests for non-blocking wakeup pipe writes."""

from __future__ import annotations

import errno
import os
import time

from tkwry._host import _open_wakeup_pipe


def _fill_wakeup_pipe(write_fd: int) -> None:
    while True:
        try:
            wrote = os.write(write_fd, b"\x01")
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return
            raise
        if wrote != 1:
            return


def test_wakeup_pipe_write_does_not_block_when_full() -> None:
    read_fd, write_fd = _open_wakeup_pipe()
    try:
        _fill_wakeup_pipe(write_fd)

        start = time.monotonic()
        for _ in range(2_000):
            try:
                wrote = os.write(write_fd, b"\x01")
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
            else:
                if wrote not in (0, 1):
                    raise AssertionError(f"unexpected write length: {wrote}")
        elapsed = time.monotonic() - start
        assert elapsed < 0.25, f"wakeup write blocked for {elapsed:.3f}s"
    finally:
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
