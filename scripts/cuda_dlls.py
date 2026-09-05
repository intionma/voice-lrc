"""Windows에서 pip로 설치한 CUDA 라이브러리를 찾게 만든다.

nvidia-cublas-cu12 등은 DLL을 site-packages/nvidia/*/bin 에 넣는데
Windows는 그 경로를 자동으로 뒤지지 않는다. 그래서
"cublas64_12.dll is not found" 오류가 난다.

세 가지를 모두 한다. 앞의 두 개만으로는 부족한 경우가 있다.

1. os.add_dll_directory 등록
   파이썬이 직접 여는 DLL에만 적용된다. ctranslate2는 네이티브 코드
   안에서 cublas를 부르므로 이것만으로는 해결되지 않는다.
2. PATH 앞에 추가
   이름만으로 DLL을 찾을 때 쓰이는 경로다.
3. DLL을 미리 메모리에 적재
   가장 확실하다. 같은 이름의 모듈이 이미 적재돼 있으면 Windows는
   다시 찾지 않고 그것을 돌려준다.

faster_whisper 또는 ctranslate2를 import 하기 전에 register()를 부른다.
"""

import os
import sys
from pathlib import Path

# os.add_dll_directory()가 돌려주는 핸들을 붙잡아 둔다.
# 이 객체가 수집되면 등록했던 경로가 다시 해제된다.
_HANDLES: list = []

# 적재한 DLL 핸들도 붙잡아 둔다.
_LOADED: list = []


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
    """CUDA DLL을 찾을 수 있게 만들고 사용한 폴더 목록을 돌려준다."""
    if sys.platform != "win32":
        return []

    dll_dirs = find_dll_dirs()
    if not dll_dirs:
        return []

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


def explain_failure() -> str:
    """CUDA 로드 실패 시 보여줄 안내문."""
    return (
        "\nGPU 라이브러리를 불러오지 못했습니다.\n"
        "다음을 시도하세요.\n"
        "  1) 9_check_gpu.bat 을 실행해 결과를 확인한다\n"
        "  2) GPU 없이 돌린다 (느리지만 동작함): 1b_transcribe_cpu.bat\n"
    )
