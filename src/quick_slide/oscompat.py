"""OS の差（mac・Linux と Windows）を吸収する 1 か所。標準ライブラリだけで書く。

**同じ中身のファイルを 3 か所に置く**: `src/quick_slide/oscompat.py`（サーバ）と、`skills/html-slide/scripts/`・
`skills/image-slide/scripts/`（エンジン。単体でも使うのでサーバを import しない）。片方を直したら全部に写す
（試験がバイト単位で見比べる）。

| やること | mac・Linux | Windows |
|---|---|---|
| 二重起動のロック | `fcntl.flock` | `msvcrt.locking`（先頭の 1 バイト） |
| PID が生きているか | `os.kill(pid, 0)` | `OpenProcess` ＋ `GetExitCodeProcess`（`os.kill(pid, 0)` は CTRL_C_EVENT になる） |
| 子を自分の組で起こす | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP`・`CREATE_NO_WINDOW` |
| 子を孫ごと止める | 組に SIGTERM → 待つ → SIGKILL | 起こした直後に入れた Job Object を止める（入れられなければ `taskkill /T /F`） |
| 終わった子の後始末 | 何もしない | Job Object を閉じる（`end_group`） |
| 待ち受けの口を自分だけにする | `SO_REUSEADDR`（TIME_WAIT の口を使い直すだけ） | `SO_REUSEADDR` を使わず `SO_EXCLUSIVEADDRUSE` |
| 日本語の字体ファイル | 游ゴシックは PowerPoint for Mac 同梱、ほかはヒラギノ | `%WINDIR%\\Fonts` の游ゴシック・メイリオ・BIZ UDゴシック |
| 台本のパスが URL か | スキーム（`https:` など）で始まるか | 同じ。ただしドライブ文字（`C:\\`・`C:/`）は URL でない |

Windows で孫ごと止めるのに Job Object を使うのは、`codex.cmd` のような包みが cmd.exe → node → codex.exe と
段を重ねるため。`taskkill /T` は親子の記録をたどるので、cmd.exe が先に終わると下の段にたどり着けない。
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
from pathlib import Path
from typing import Any

WINDOWS = os.name == "nt"

if WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class _BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
else:
    import fcntl


# --- 二重起動のロック ------------------------------------------------------------------

def lock_nowait(fd: int) -> bool:
    """開いたファイルに排他ロックを掛ける。ほかが持っていれば待たずに False。プロセスが終われば OS が外す。"""
    try:
        if WINDOWS:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def unlock(fd: int) -> None:
    if WINDOWS:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


# --- 台本のパスが URL か ----------------------------------------------------------------------

def looks_like_url(text: str) -> bool:
    """`https:`・`data:`・`file:` などのスキームで始まるか。スキームは 2 文字以上に限る（1 文字だと Windows の
    ドライブ文字の `C:\\…`・`C:/…` も URL と読み、台本の絶対パスを断ってしまう。N21）。"""
    return re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+:", text) is not None


# --- 日本語の字体ファイル ------------------------------------------------------------------------

_MAC_YU_GOTHIC = Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts")
_MAC_FONTS = Path("/System/Library/Fonts")


def _windows_fonts() -> Path:
    return Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts"


def yu_gothic_files() -> dict[str, str]:
    """游ゴシックの regular・bold（プレゼンが文字の幅を測る字体）。mac は PowerPoint for Mac 同梱、Windows は OS の字体。"""
    base = _windows_fonts() if WINDOWS else _MAC_YU_GOTHIC
    return {"regular": str(base / "YuGothR.ttc"), "bold": str(base / "YuGothB.ttc")}


def japanese_fonts(weight: str) -> tuple[str, ...]:
    """日本語を描ける字体ファイルの候補。先頭から、あるものを使う。`weight` は "bold" か "regular"。"""
    first = yu_gothic_files()[weight]
    if WINDOWS:
        names = ("meiryob.ttc", "BIZ-UDGothicB.ttc") if weight == "bold" else ("meiryo.ttc", "BIZ-UDGothicR.ttc")
        return (first, *(str(_windows_fonts() / n) for n in names))
    names = ("ヒラギノ角ゴシック W6.ttc", "Hiragino Sans GB.ttc") if weight == "bold" else ("ヒラギノ角ゴシック W3.ttc",)
    return (first, *(str(_MAC_FONTS / n) for n in names))


# --- 待ち受けの口 ------------------------------------------------------------------------------

# `socketserver` の `allow_reuse_address` に渡す値。mac・Linux の SO_REUSEADDR は TIME_WAIT の口を使い直すだけで、
# ほかが待ち受けている口は取れない。Windows の SO_REUSEADDR は待ち受け中の口も横取りできてしまうので使わない
REUSE_ADDRESS = not WINDOWS


def bind_exclusively(sock: socket.socket) -> None:
    """bind の前に呼ぶ。Windows では SO_EXCLUSIVEADDRUSE を付け、ほかが使っている口への bind を失敗させる
    （付けないと、ほかのアプリが待ち受けている口に重ねて bind できてしまう）。mac・Linux は何もしない。"""
    if WINDOWS:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)


# --- PID ---------------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """その PID のプロセスがまだ終わっていないか。ほかの利用者のプロセスで調べられなければ、生きているとみなす。"""
    if pid <= 0:
        return False
    if WINDOWS:
        handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == _STILL_ACTIVE
        finally:
            _k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- 子を組で起こし、孫ごと止める ------------------------------------------------------------

def group_kwargs() -> dict[str, Any]:
    """`subprocess.Popen` に足す引数: 子を自分の組で起こす（端末の Ctrl+C を受けない・Windows では窓を出さない）。

    常駐のサーバのように、起こした側が終わっても生き残らせる子はこれだけで起こす（`popen_group` は使わない）。
    """
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def popen_group(cmd: list[str], **kwargs: Any) -> subprocess.Popen:
    """子を自分の組で起こし、あとで `kill_group` で孫ごと止められるようにする。

    Windows では起こした直後に Job Object へ入れる（その後に子が起こす孫も同じ Job に入る）。Job は閉じると中を
    全部止める作りにしてあるので、起こした側が落ちても孫が残らない。
    """
    proc = subprocess.Popen(cmd, **kwargs, **group_kwargs())
    if WINDOWS:
        proc._oscompat_job = _job_for(proc)  # type: ignore[attr-defined]
    return proc


def kill_group(proc: subprocess.Popen, *, grace: float = 5.0) -> None:
    """子を孫ごと止める。mac・Linux は組に SIGTERM を送って `grace` 秒待ち、残っていれば SIGKILL。子が先に
    終わっていても、組に残った孫（出力の口を握ったままのもの）を止める。"""
    if WINDOWS:
        job = getattr(proc, "_oscompat_job", None)
        if job:
            _k32.TerminateJobObject(job, 1)
            end_group(proc)
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **group_kwargs())
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def end_group(proc: subprocess.Popen) -> None:
    """子が終わったあとの後始末。Windows は Job Object を閉じる（残っていた孫も止まる）。mac・Linux は何もしない。

    `popen_group` で起こした子は、終わったら必ず呼ぶ（呼ばないと、常駐のサーバで Job の握りが溜まる）。
    """
    job = getattr(proc, "_oscompat_job", None)
    if job:
        proc._oscompat_job = None  # type: ignore[attr-defined]
        _k32.CloseHandle(job)


def _job_for(proc: subprocess.Popen) -> int | None:
    """閉じると中を全部止める Job Object を作り、子を入れる。入れられなければ None（止めるときは taskkill に頼る）。"""
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = (_k32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                       ctypes.sizeof(info))
          and _k32.AssignProcessToJobObject(job, int(proc._handle)))  # type: ignore[attr-defined]
    if not ok:
        _k32.CloseHandle(job)
        return None
    return job
