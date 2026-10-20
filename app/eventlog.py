"""事件日志与设备信息采集（用来排查问题）。

- 每次处理（渲染 / 导出 / AI / 导入…）都会带**具体参数 + 时间**追加写进 `logs/*.log`；
- 换设备时（CPU / 内存 / 显卡 / 驱动与上次不同）额外记一条完整设备档案；
- 「导出使用报告」按钮把设备信息 + 日志拼成一个 txt，方便用户发给开发者。

本模块不依赖 Qt，也不依赖任何第三方库，日志写失败绝不抛异常影响主流程。
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def timestamp():
    """毫秒精度的本地时间戳（日志行首时间）。"""
    return _dt.datetime.now().strftime(_TIME_FORMAT + ".%f")[:-3]


# -- 设备信息 ---------------------------------------------------------------


def _ram_gb():
    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullTotalPhys / (1024.0 ** 3), 1)
    except Exception:
        pass
    return None


def _gpus():
    """显卡型号 + 驱动版本，返回 ["型号|驱动版本", ...]；取不到就返回空表。"""
    shell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    script = (
        "Get-CimInstance Win32_VideoController | Select-Object -First 4 | "
        "ForEach-Object { $_.Name + '|' + $_.DriverVersion + '|' + $_.DriverDate }"
    )
    for command in (["powershell", "-NoProfile", "-Command", script],
                    [shell, "-NoProfile", "-Command", script]):
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        lines = []
        for raw in (done.stdout or "").splitlines():
            raw = raw.strip()
            if raw.count("|") >= 2:
                lines.append(raw)
        if lines:
            return lines
    return []


def collect_device_profile():
    """CPU / 内存 / 显卡 / 驱动 / 系统 / Python 等排查用信息。"""
    profile = {
        "machine": os.environ.get("COMPUTERNAME", ""),
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "arch": platform.machine(),
        "cpu": os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or "",
        "cpu_cores": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "gpus": _gpus(),
    }
    return profile


def device_signature(profile):
    """设备指纹：只取会随设备变化的字段，用来判断"是不是换了机器"。"""
    relevant = {
        "cpu": profile.get("cpu"),
        "cpu_cores": profile.get("cpu_cores"),
        "ram_gb": profile.get("ram_gb"),
        "os": profile.get("os"),
        "os_version": profile.get("os_version"),
        "arch": profile.get("arch"),
        "gpus": profile.get("gpus"),
    }
    blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


# -- 事件日志 ---------------------------------------------------------------


class EventLog:
    """按天追加写的日志文件；写失败也不抛异常。"""

    def __init__(self, directory, prefix="app"):
        self.directory = Path(directory)
        self.prefix = prefix
        self.session = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self._lock = threading.Lock()
        self.last_error = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_error = str(exc)

    @property
    def path(self):
        return self.directory / f"{self.prefix}-{_dt.datetime.now():%Y%m%d}.log"

    @staticmethod
    def _render(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return f"{value:.3f}"
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if value is None:
            return "-"
        return str(value).replace("\n", " ")

    def log(self, event, **fields):
        """追加一条事件：`时间 | 事件 | key=value key=value`。"""
        text = " | ".join(
            [timestamp(), str(event)]
            + [" ".join(f"{key}={self._render(value)}" for key, value in fields.items())]
        )
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(text.rstrip(" |") + "\n")
            except OSError as exc:
                self.last_error = str(exc)
        return text

    def read_all(self):
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    def tail(self, lines=200):
        content = self.read_all().splitlines()
        return "\n".join(content[-lines:])
