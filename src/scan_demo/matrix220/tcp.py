"""Matrix 220 以太网 TCP 收码（与 ROS 无关的共用逻辑）。"""

from __future__ import annotations

import re
import socket
import threading
import time

DEFAULT_PORT = 51236


def normalize_barcode(raw: str) -> str:
    # 得利捷默认：STX(0x02) + 条码 + ETX(0x03) + CR/LF；部分固件还会发 CAN(0x18) 等控制符
    stripped = raw.strip("\x00\r\n\t \x02\x03\x18")
    return "".join(ch for ch in stripped if ch >= " " or ch == "\t")


_INVALID_READS = frozenset(
    {
        "NOREAD",
        "NO READ",
        "NO_READ",
        "ERROR",
        "ERR",
        "NG",
        "NR",
        "FAIL",
        "FAILED",
    }
)


def is_valid_read(text: str) -> bool:
    if not text:
        return False
    if not any(ch.isprintable() and not ch.isspace() for ch in text):
        return False
    upper = text.upper().strip()
    if upper in _INVALID_READS:
        return False
    if upper.startswith("NOREAD") or upper.startswith("ERROR"):
        return False
    return True


def iter_tcp_lines(sock: socket.socket) -> str:
    """从已连接的 TCP socket 按行 yield 文本。"""
    buf = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            continue
        if not chunk:
            break
        buf += chunk
        while True:
            m = re.search(rb"[\r\n]", buf)
            if not m:
                break
            line = buf[: m.start()]
            buf = buf[m.end() :]
            yield line.decode("utf-8", errors="replace")


class TcpServerReceiver:
    """电脑监听，扫码器配置为 TCP Client。"""

    def __init__(self, bind_host: str, bind_port: int, on_line) -> None:
        self._on_line = on_line
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind_host, bind_port))
        self._sock.listen(1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._sock.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                self._sock.settimeout(0.5)
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self._handle_conn(conn, addr)

    def _handle_conn(self, conn: socket.socket, addr) -> None:
        conn.settimeout(1.0)
        source = f"{addr[0]}:{addr[1]}"
        try:
            for text in iter_tcp_lines(conn):
                if self._stop.is_set():
                    break
                self._on_line(text, source=source)
        finally:
            conn.close()


class TcpClientReceiver:
    """电脑主动连扫码器（扫码器 TCP Server）。"""

    def __init__(self, host: str, port: int, on_line) -> None:
        self._host = host
        self._port = port
        self._on_line = on_line
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self._host, self._port), timeout=5.0)
            except OSError as exc:
                print(
                    f"连接 {self._host}:{self._port} 失败: {exc}，3s 后重试",
                    flush=True,
                )
                time.sleep(3.0)
                continue
            sock.settimeout(1.0)
            source = f"{self._host}:{self._port}"
            try:
                for text in iter_tcp_lines(sock):
                    if self._stop.is_set():
                        break
                    self._on_line(text, source=source)
            finally:
                sock.close()
            if not self._stop.is_set():
                time.sleep(1.0)
