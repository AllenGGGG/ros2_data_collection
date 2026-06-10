"""采集员终端提示：扫码成功醒目打印，每次带序号便于区分。"""

from __future__ import annotations

import sys
from datetime import datetime

_R = "\033[0m"
_GREEN = "\033[1;92m"
_CYAN = "\033[1;96m"
_YELLOW = "\033[1;93m"
_DIM = "\033[2m"
_BG_GREEN = "\033[42;30m"
_BG_YELLOW = "\033[43;30m"
_WIDTH = 60

_scan_success_count = 0
_last_success_code = ""


def _c(text: str, *codes: str) -> str:
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + _R


def _line(text: str = "") -> None:
    print(text, flush=True)


def announce_code_read(code: str, *, source: str = "") -> None:
    """Matrix220 读到新条码、发布 /scan/code 时（轻量一行）。"""
    src = f" [{source}]" if source else ""
    _line(_c(f"  📦  读到条码{src}: {code}", _CYAN))


def announce_scan_success(code: str) -> int:
    """/scan/success 置 1：醒目横幅，序号递增，与上次扫码区分。"""
    global _scan_success_count, _last_success_code
    _scan_success_count += 1
    now = datetime.now()
    time_str = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
    seq = _scan_success_count
    prev = _last_success_code
    _last_success_code = code

    _line()
    _line(_c("  " + "═" * _WIDTH, _GREEN))
    _line(_c(f"  ✅  扫 码 成 功  ·  第 {seq:03d} 次  ", _BG_GREEN))
    _line(_c("  " + "═" * _WIDTH, _GREEN))
    _line(_c(f"  🏷️  条码: {code}", _GREEN))
    _line(_c(f"  🕐  时间: {time_str}", _GREEN))
    _line(_c(f"  📡  /scan/success → 1  (30Hz)", _GREEN))
    if prev and prev != code:
        _line(_c(f"  ↩️  上次: {prev}", _DIM))
    elif prev == code and seq > 1:
        _line(_c(f"  ↩️  与上次条码相同（新一次扫码周期）", _DIM))
    _line(_c("  " + "─" * _WIDTH, _DIM))
    _line()
    return seq


def announce_record_stop() -> None:
    _line()
    _line(_c("  ⏹️  录包已停 → /scan/success 复位为 0", _BG_YELLOW, _YELLOW))
    _line(_c("  ⏳  移开扫码器或出现 NG 后，可扫下一条", _YELLOW))
    _line()


def announce_arm_next() -> None:
    _line(_c("  🔓  已允许下一次扫码", _CYAN))


def announce_mock_scan_success() -> int:
    """模拟器无真实条码时的扫码成功横幅。"""
    return announce_scan_success(code="(模拟扫码)")
