"""윈도우에서 pip로 설치한 CUDA 라이브러리를 찾게 만든다.

`scripts/cuda_dlls.py`에서 옮겨 왔다. 실제로 겪은 문제를 막는 코드라 내용은
그대로 두고 안내 문구만 새 화면에 맞게 고쳤다.

nvidia-cublas-cu12 등은 DLL을 `site-packages/nvidia/*/bin`에 넣는데 윈도우는 그
경로를 자동으로 뒤지지 않는다. 그래서 "cublas64_12.dll is not found"가 난다.

세 가지를 모두 한다. 앞의 두 개만으로는 부족한 경우가 있다.

1. `os.add_dll_directory` 등록
   파이썬이 직접 여는 DLL에만 적용된다. ctranslate2는 네이티브 코드 안에서
   cublas를 부르므로 이것만으로는 해결되지 않는다.
2. `PATH` 앞에 추가
   이름만으로 DLL을 찾을 때 쓰이는 경로다.
3. DLL을 미리 메모리에 적재
   가장 확실하다. 같은 이름의 모듈이 이미 적재돼 있으면 윈도우는 다시 찾지 않고
   그것을 돌려준다.

`faster_whisper`나 `ctranslate2`를 import 하기 전에 `register()`를 부른다.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from pathlib import Path

# 받아쓰기가 그래픽카드를 쓰려면 이 셋이 있어야 한다.
#
# **`scripts/check_gpu.py` 에 있던 것을 옮겨 왔다.** 그 파일은 점검 탭과
# 함께 없앴는데, 목록 자체는 `setup.bat` 이 「CUDA libraries OK」 를 찍기
# 전에 무엇을 봐야 하는지 정하는 값이라 살아 있어야 한다.
#
# cuBLAS 만 보고 OK 를 찍으면 cuDNN 이 빠졌을 때 **설치는 성공한 것처럼
# 보이고** 받아쓰기가 돌 때 알 수 없는 말로 터진다.
# (`test_setup_이_받아쓰기에_필요한_CUDA_를_다_확인한다` 가 지킨다)
REQUIRED_DLLS = ["cublas64_12.dll", "cudnn_ops64_9.dll", "cudnn64_9.dll"]


# os.add_dll_directory()가 돌려주는 핸들을 붙잡아 둔다.
# 이 객체가 수집되면 등록했던 경로가 다시 해제된다.
_HANDLES: list = []

# 적재한 DLL 핸들도 붙잡아 둔다.
_LOADED: list = []

# 이미 등록했으면 그때 쓴 폴더 목록. 두 번 하지 않는다
_REGISTERED: list | None = None


def _candidate_site_packages() -> list[Path]:
    import site

    candidates = list(site.getsitepackages())
    user_site = site.getusersitepackages()
    if isinstance(user_site, str):
        candidates.append(user_site)
    # 가상환경 안에서 실행될 때를 대비해 실행 파일 기준 경로도 본다
    candidates.append(str(Path(sys.executable).parent.parent / "Lib" / "site-packages"))
    return [Path(item) for item in candidates]


def find_dll_dirs() -> list[Path]:
    """CUDA DLL이 들어 있는 폴더를 모은다."""
    found, seen = [], set()
    for base in _candidate_site_packages():
        nvidia_dir = base / "nvidia"
        if not nvidia_dir.is_dir():
            continue
        for bin_dir in sorted(nvidia_dir.glob("*/bin")):
            resolved = bin_dir.resolve()
            if resolved in seen or not resolved.is_dir():
                continue
            seen.add(resolved)
            found.append(resolved)
    return found


def _preload(dll_dirs: list[Path]) -> list[Path]:
    """DLL을 미리 적재한다. 서로 의존하므로 진전이 없을 때까지 반복한다."""
    import ctypes

    pending = [dll for directory in dll_dirs for dll in sorted(directory.glob("*.dll"))]
    loaded = []
    while pending:
        remaining = []
        for dll in pending:
            try:
                _LOADED.append(ctypes.WinDLL(str(dll)))
                loaded.append(dll)
            except OSError:
                # 아직 의존 DLL이 안 올라온 것일 수 있으니 다음 바퀴에 다시 시도한다
                remaining.append(dll)
        if len(remaining) == len(pending):
            break
        pending = remaining
    return loaded


def register() -> list[Path]:
    """CUDA DLL을 찾을 수 있게 만들고 사용한 폴더 목록을 돌려준다.

    한 번만 한다. 모델을 다시 올릴 때마다 부르는 자리라, 그대로 두면 같은
    폴더가 `PATH` 앞에 계속 쌓이고 DLL도 매번 다시 적재한다. 윈도우 환경
    변수에는 길이 한도가 있어서 언젠가 터진다.
    """
    global _REGISTERED
    if sys.platform != "win32":
        return []
    if _REGISTERED is not None:
        return _REGISTERED

    dll_dirs = find_dll_dirs()
    if not dll_dirs:
        return []  # 아직 없는 것뿐일 수 있으니 다음에 다시 본다
    _REGISTERED = dll_dirs

    for directory in dll_dirs:
        try:
            _HANDLES.append(os.add_dll_directory(str(directory)))
        except OSError:
            pass

    # 이름만으로 DLL을 찾는 경로에도 넣는다
    path_entries = [str(d) for d in dll_dirs]
    existing = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([*path_entries, existing]) if existing else os.pathsep.join(path_entries)

    _preload(dll_dirs)
    return dll_dirs


def can_load(name: str = "cublas64_12.dll") -> tuple[bool, str]:
    """ctranslate2가 하는 것처럼 이름만으로 DLL을 열어 본다."""
    if sys.platform != "win32":
        return False, "윈도우가 아님"
    import ctypes

    try:
        ctypes.WinDLL(name)
        return True, "성공"
    except OSError as error:
        return False, str(error)


def looks_like_cuda_problem(message: str) -> bool:
    """오류 문구가 GPU 라이브러리 문제인지 본다.

    이게 맞으면 다음 파일도 똑같이 실패하므로 대기열을 계속 돌릴 이유가 없다.
    """
    lowered = message.lower()
    return any(sign in lowered for sign in ("cublas", "cudnn", "cuda", "libcu"))


def explain_failure() -> str:
    """GPU를 못 쓸 때 화면에 띄울 안내문."""
    return (
        "그래픽카드를 쓰지 못했습니다.\n"
        "느리지만 CPU로 계속할 수 있습니다. 설정에서 'CPU로 처리'를 켜 주세요."
    )


def _smi(무엇: str) -> list[float]:
    """`nvidia-smi` 에 숫자 하나를 물어본다. 못 물어보면 빈 목록.

    `nvidia-smi` 는 NVIDIA 드라이버를 깔면 같이 들어오므로 따로 받을 것이 없다.
    """
    import subprocess

    플래그 = 0
    if sys.platform == "win32":
        플래그 = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        결과 = subprocess.run(
            ["nvidia-smi", f"--query-gpu={무엇}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=플래그,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if 결과.returncode != 0:
        return []

    값들 = []
    for 줄 in (결과.stdout or "").splitlines():
        줄 = 줄.strip()
        if not 줄:
            continue
        try:
            값들.append(float(줄))
        except ValueError:
            continue
    return 값들


# 화면 맨 위에 늘 띄우는 값이라 자주 물어보게 된다. `nvidia-smi` 는 한 번에
# 0.1~0.3초쯤 걸리므로 그대로 두면 화면이 그것 때문에 굼떠진다. 잠깐 담아 둔다
_잰것: dict[str, Any] = {"때": 0.0, "값": None}
VRAM_CACHE_SEC = 2.0


def state() -> dict[str, Any] | None:
    """지금 그래픽카드가 어떤 상태인지 한 번에. 못 재면 `None`.

    **이것이 화면 맨 위에 늘 떠 있어야 한다.**

    지금까지는 볼 방법이 없었다. 자리가 모자라 받아쓰기가 통째로 죽었는데도
    무엇이 얼마나 쓰고 있는지 알 수가 없어서, 사흘을 엉뚱한 데를 팠다.
    번역 모델이 눌러앉아 있는지, 받아쓰기 모델이 아직 안 내려갔는지를
    숫자 하나로 보여 준다.

    한 번의 `nvidia-smi` 로 다 가져온다. 항목마다 따로 부르면 그만큼 느리다.
    """
    import time as _time

    지금 = _time.monotonic()
    if _잰것["값"] is not None and (지금 - _잰것["때"]) < VRAM_CACHE_SEC:
        return _잰것["값"]

    줄들 = _smi_rows("memory.total,memory.used,memory.free,utilization.gpu")
    값 = None
    if 줄들:
        # 카드가 여러 장이면 제일 큰 것. 모델은 한 장에 올라간다
        칸 = max(줄들, key=lambda r: r[0] if r else 0)
        if len(칸) >= 4:
            값 = {
                "total_gb": round(칸[0] / 1024.0, 1),
                "used_gb": round(칸[1] / 1024.0, 1),
                "free_gb": round(칸[2] / 1024.0, 1),
                "util_pct": int(칸[3]),
            }
    _잰것.update({"때": 지금, "값": 값})
    return 값


def _smi_rows(무엇: str) -> list[list[float]]:
    """여러 항목을 한 번에 물어본다. 줄마다 카드 하나."""
    import subprocess

    플래그 = 0
    if sys.platform == "win32":
        플래그 = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        결과 = subprocess.run(
            ["nvidia-smi", f"--query-gpu={무엇}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=플래그,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if 결과.returncode != 0:
        return []

    줄들 = []
    for 줄 in (결과.stdout or "").splitlines():
        조각 = [칸.strip() for 칸 in 줄.split(",") if 칸.strip()]
        try:
            줄들.append([float(칸) for 칸 in 조각])
        except ValueError:
            continue
    return 줄들


def free_vram_gb() -> float | None:
    """지금 **비어 있는** VRAM(GB). 못 재면 `None`.

    가진 것이 아니라 남은 것을 봐야 하는 이유가 있다. Ollama 는 마지막으로 쓴
    뒤에도 한동안 모델을 붙잡고 있고, 다른 프로그램도 VRAM 을 쓴다. 12GB 짜리
    카드라도 그 순간 남은 것이 2GB 면 `large-v3`(약 4.7GB)는 못 올라간다.

    그때 ctranslate2 는 파이썬 오류를 내지 않는다. **프로세스가 그냥 죽는다.**
    그래서 죽기 전에 이 값을 자취에 남겨 둔다. 그것이 유일한 단서가 된다.
    """
    값들 = _smi("memory.free")
    return (max(값들) / 1024.0) if 값들 else None


def total_vram_gb() -> float | None:
    """그래픽카드가 가진 VRAM(GB). 못 재면 `None`.

    번역 모델이 그래픽카드에 들어가는지 알려 주려면 실제 값이 있어야 한다.
    문서에 적힌 12GB 를 코드에 박아 두면 다른 컴퓨터에서 거짓말이 된다.
    """
    # 카드가 여러 장이면 가장 큰 것. 모델은 한 장에 올라간다
    값들 = _smi("memory.total")
    if not 값들:
        return None
    return max(값들) / 1024.0
