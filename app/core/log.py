"""무슨 일이 있었는지 파일에 남긴다.

만드는 쪽에는 GPU 도 음원도 윈도우도 없다. 그래서 "이상하다" 는 말을 들었을 때
볼 것이 화면에 뜬 글자뿐이었다. 그것만으로는 프로그램 문제인지 음성 문제인지
가릴 수 없다.

남기는 곳은 사용자 폴더 아래다. 저장소에는 들어가지 않는다.
    %APPDATA%/trans-text/logs/trans-text.log

API 키는 절대 남기지 않는다.
"""

from __future__ import annotations

import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.core import settings as settings_store

# 이보다 커지면 절반을 버린다. 로그가 디스크를 채우면 안 된다
MAX_BYTES = 2_000_000

_lock = Lock()


def log_path() -> Path:
    return settings_store.config_dir() / "logs" / "trans-text.log"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def write(kind: str, message: str, **값) -> None:
    """한 줄 남긴다. 실패해도 프로그램을 멈추지 않는다."""
    붙임 = " ".join(f"{k}={v}" for k, v in 값.items() if v is not None)
    _남기기([f"{_now()} [{kind}] {message}{(' ' + 붙임) if 붙임 else ''}"])


def _남기기(줄들: list[str]) -> None:
    """여러 줄을 **한 번에** 남긴다.

    한 줄씩 따로 남기면 자물쇠를 줄마다 놓았다 잡는다. 받아쓰는 일꾼과 작품
    정보를 가져오는 일꾼이 같이 도는 동안 오류 자취를 남기면, 그 사이사이에
    다른 줄이 끼어들어 **자취가 토막 나서 읽을 수 없게 된다.**

    사용자가 "이상하다" 며 보내 오는 것이 이 파일이다. 토막 난 자취를 받으면
    어디서 터졌는지 못 읽는다.
    """
    if not 줄들:
        return
    try:
        with _lock:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _trim(path)
            with path.open("a", encoding="utf-8") as f:
                f.write("".join(줄 + "\n" for 줄 in 줄들))
    except OSError:
        pass  # 로그를 못 남기는 것으로 프로그램이 죽으면 안 된다


def _trim(path: Path) -> None:
    """너무 커지면 뒤쪽 절반만 남긴다."""
    try:
        if not path.is_file() or path.stat().st_size <= MAX_BYTES:
            return
        글 = path.read_text(encoding="utf-8", errors="replace")
        path.write_text(글[len(글) // 2 :], encoding="utf-8")
    except OSError:
        pass


def error(message: str, exc: BaseException | None = None, **값) -> None:
    """오류 한 건을 남긴다. 자취까지 **끊기지 않게 한 덩어리로** 남긴다."""
    붙임 = " ".join(f"{k}={v}" for k, v in 값.items() if v is not None)
    때 = _now()
    줄들 = [f"{때} [오류] {message}{(' ' + 붙임) if 붙임 else ''}"]
    if exc is not None:
        for 줄 in traceback.format_exception(type(exc), exc, exc.__traceback__):
            for 조각 in 줄.rstrip().splitlines():
                줄들.append(f"{때} [오류]   {조각}")
    _남기기(줄들)


def start() -> None:
    """켤 때 한 번. 어떤 환경인지 남겨 둔다."""
    from app import __version__

    write("시작", "trans-text", 판=__version__)
    write("환경", platform.platform())
    write("환경", f"python={sys.version.split()[0]}")


def read_tail(limit: int = 400) -> list[str]:
    """마지막 몇 줄. 진단 파일에 넣는다."""
    try:
        줄들 = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return 줄들[-limit:]


def gpu_state() -> dict[str, Any]:
    """그래픽카드를 쓸 수 있는 상태인지 본다.

    **먼저 `register()` 를 부른다.** 예전에는 안 불렀다. pip 로 깔린 CUDA
    DLL 은 `register()` 가 `PATH` 에 넣어 줘야 이름만으로 열린다. 그래서
    모델을 한 번도 안 올린 상태에서 진단을 누르면 **멀쩡한 컴퓨터에서도**
    `cublas: False` 가 떴다. 진단이 거짓말을 한 것이다.

    실제로 그 거짓말을 보고 「CUDA 가 깨졌다」고 잘못 짚었다. 진단 화면이
    틀리면 없는 것을 고치느라 시간을 버린다.

    남은 VRAM 도 같이 준다. 자리가 모자라서 ctranslate2 가 죽는 경우가
    있는데, 그때는 파이썬 오류가 없어서 이 숫자 말고는 단서가 없다.
    """
    from app.core import gpu

    gpu.register()
    폴더 = [str(p) for p in gpu.find_dll_dirs()]
    열림, 까닭 = gpu.can_load()
    가진것 = gpu.total_vram_gb()
    남은것 = gpu.free_vram_gb()
    return {
        "dll_dirs": 폴더,
        "cublas": 열림,
        "reason": 까닭,
        "vram_total_gb": None if 가진것 is None else round(가진것, 1),
        "vram_free_gb": None if 남은것 is None else round(남은것, 1),
    }
