"""Regression tests for non-blocking wakeup pipe writes (D26 / T10)."""

from __future__ import annotations

import errno
import os
import sys
import time

import pytest

from tkwry._host import _open_wakeup_pipe


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="nonblocking wakeup pipe is unix-only",
)
def test_wakeup_pipe_write_does_not_block_when_full() -> None:
    read_fd, write_fd = _open_wakeup_pipe()
    try:
        while True:
            try:
                os.write(write_fd, b"\x01")
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                raise

        start = time.monotonic()
        for _ in range(2_000):
            try:
                os.write(write_fd, b"\x01")
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
        elapsed = time.monotonic() - start
        assert elapsed < 0.25, f"wakeup write blocked for {elapsed:.3f}s"
    finally:
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
