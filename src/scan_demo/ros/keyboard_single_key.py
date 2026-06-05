"""终端单键读取（无需回车），供 mock_scanner 在独立线程使用。"""

from __future__ import annotations

import atexit
import select
import sys
import termios
import tty
from contextlib import contextmanager


class SingleKeyKeyboard:
    """raw 模式下读单个按键；退出时自动恢复终端。"""

    def __init__(self, stdin=None):
        self._stdin = stdin or sys.stdin
        self._fd = self._stdin.fileno()
        self._old_term: list | None = None

    def __enter__(self) -> SingleKeyKeyboard:
        if not self._stdin.isatty():
            raise RuntimeError("stdin 不是 TTY，请在真实终端里运行 mock_scanner_ros2.py")
        self._old_term = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        atexit.register(self._restore)
        return self

    def __exit__(self, *_) -> None:
        self._restore()
        try:
            atexit.unregister(self._restore)
        except Exception:
            pass

    def _restore(self) -> None:
        if self._old_term is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_term)
            self._old_term = None

    def read_key(self, timeout_sec: float = 0.1) -> str | None:
        """有键则返回单字符，超时返回 None。"""
        ready, _, _ = select.select([self._stdin], [], [], timeout_sec)
        if not ready:
            return None
        ch = self._stdin.read(1)
        return ch if ch else None


@contextmanager
def cooked_mode(stdin=None):
    """临时恢复行缓冲，用于输入条码等。"""
    stdin = stdin or sys.stdin
    if not stdin.isatty():
        yield
        return
    fd = stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
