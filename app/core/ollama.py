"""Ollama 를 프로그램이 직접 다룬다.

사용자에게 "명령 프롬프트를 열고 `ollama pull qwen2.5:14b` 를 치세요" 라고
말하는 순간 끝이다. 그것은 침팬치가 할 수 있는 일이 아니다.

여기서 하는 것.

    켜져 있나 본다        → GET  /api/tags
    안 켜져 있으면 켠다    → ollama.exe 를 찾아서 실행
    모델이 있나 본다       → 위 목록에서
    없으면 받는다          → POST /api/pull (진행률이 줄줄이 온다)

전부 표준 라이브러리로만 한다. Ollama 는 OpenAI 와 같은 모양의 API 도 주지만
켜기·받기는 자기 API 를 써야 한다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

# 기본 주소. 사용자가 바꿀 수 있다
BASE = "http://localhost:11434"

# 켜져 있는지 보는 것은 빨라야 한다. 안 켜져 있으면 바로 알아야 하니까
PROBE_TIMEOUT = 2.0

# 켜고 나서 이만큼까지 기다린다
START_TIMEOUT = 20.0

# 받는 것은 오래 걸린다. 9GB 를 받는다
PULL_TIMEOUT = 3600.0


# 없을 때 받으러 보낼 곳. 화면이 이 주소를 연다
INSTALL_URL = "https://ollama.com/download"

# 아무것도 안 받아 뒀을 때 권하는 모델
DEFAULT_MODEL = "qwen2.5:7b"


def _api(base: str, path: str) -> str:
    return base.rstrip("/").removesuffix("/v1/chat/completions").rstrip("/") + path


def base_from(url: str) -> str:
    """설정에 적힌 주소에서 뿌리를 뽑는다.

    설정에는 `http://localhost:11434/v1/chat/completions` 가 들어 있다.
    켜기·받기 API 는 `/api/...` 라 뿌리가 필요하다.
    """
    글 = (url or "").strip() or BASE
    for 꼬리 in ("/v1/chat/completions", "/v1", "/api/chat", "/api/generate"):
        if 글.endswith(꼬리):
            글 = 글[: -len(꼬리)]
    return 글.rstrip("/") or BASE


def _get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def is_running(base: str = BASE, *, timeout: float = PROBE_TIMEOUT) -> bool:
    try:
        _get_json(_api(base, "/api/tags"), timeout)
        return True
    except Exception:
        return False


def models(base: str = BASE, *, timeout: float = PROBE_TIMEOUT) -> list[str]:
    """깔려 있는 모델 이름들."""
    try:
        답 = _get_json(_api(base, "/api/tags"), timeout)
    except Exception:
        return []
    이름 = []
    for 하나 in (답 or {}).get("models") or []:
        if not isinstance(하나, dict):
            continue
        값 = str(하나.get("name") or 하나.get("model") or "").strip()
        if 값:
            이름.append(값)
    return 이름


def model_sizes(base: str = BASE, *, timeout: float = PROBE_TIMEOUT) -> dict[str, float]:
    """깔려 있는 모델의 **실제 파일 크기**(GB). 이름 → 크기.

    이름에서 "14b" 를 읽어 크기를 어림하지 않는다. 같은 14B 라도 양자화에 따라
    9GB 도 되고 16GB 도 된다. Ollama 가 실제 값을 알려 주므로 그것을 쓴다.
    """
    try:
        답 = _get_json(_api(base, "/api/tags"), timeout)
    except Exception:
        return {}
    크기 = {}
    for 하나 in (답 or {}).get("models") or []:
        if not isinstance(하나, dict):
            continue
        이름 = str(하나.get("name") or 하나.get("model") or "").strip()
        try:
            바이트 = float(하나.get("size") or 0)
        except (TypeError, ValueError):
            바이트 = 0.0
        if 이름 and 바이트 > 0:
            크기[이름] = 바이트 / (1024 ** 3)
    return 크기


def has_model(name: str, base: str = BASE) -> bool:
    """이름이 정확히 같거나, 태그만 다른 같은 모델이 있으면 있는 것으로 본다."""
    원하는것 = (name or "").strip()
    if not 원하는것:
        return False
    있는것 = models(base)
    if 원하는것 in 있는것:
        return True
    뿌리 = 원하는것.split(":")[0]
    return any(m.split(":")[0] == 뿌리 for m in 있는것)


def find_exe() -> Path | None:
    """`ollama` 실행 파일을 찾는다.

    설치하면 보통 PATH 에 들어가지만, 방금 깔았으면 이 프로그램이 켜질 때의
    PATH 에는 아직 없다. 그래서 흔한 자리도 함께 본다.
    """
    찾음 = shutil.which("ollama")
    if 찾음:
        return Path(찾음)

    후보: list[Path] = []
    if sys.platform == "win32":
        for 환경 in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            뿌리 = os.environ.get(환경)
            if 뿌리:
                후보 += [
                    Path(뿌리) / "Programs" / "Ollama" / "ollama.exe",
                    Path(뿌리) / "Ollama" / "ollama.exe",
                ]
    else:
        후보 += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]

    for 길 in 후보:
        if 길.is_file():
            return 길
    return None


def start(base: str = BASE, *, wait_sec: float = START_TIMEOUT) -> tuple[bool, str]:
    """안 켜져 있으면 켠다. `(됐나, 한마디)`."""
    if is_running(base):
        return True, "이미 켜져 있습니다."

    exe = find_exe()
    if exe is None:
        return False, (
            "Ollama 가 안 깔려 있습니다.\n"
            "ollama.com 에서 받아 설치한 뒤 다시 눌러 주세요."
        )

    try:
        # 창을 띄우지 않는다. 검은 창이 하나 더 뜨면 사용자가 놀란다
        플래그 = 0
        if sys.platform == "win32":
            플래그 = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [str(exe), "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=플래그,
        )
    except OSError as error:
        return False, f"켜지 못했습니다: {error}"

    import time

    끝날때 = time.monotonic() + wait_sec
    while time.monotonic() < 끝날때:
        if is_running(base):
            return True, "켰습니다."
        time.sleep(0.5)
    return False, "켰는데 응답이 없습니다. 잠시 뒤 다시 눌러 주세요."


def pull(
    name: str,
    base: str = BASE,
    *,
    on_progress: Callable[[str, float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """모델을 받는다. 진행률이 줄줄이 오므로 그대로 넘겨 준다."""
    보낼것 = json.dumps({"model": name, "stream": True}).encode("utf-8")
    request = urllib.request.Request(
        _api(base, "/api/pull"),
        data=보낼것,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=PULL_TIMEOUT) as response:
            for 줄 in response:
                if should_stop and should_stop():
                    return False, "받다가 멈췄습니다."
                조각 = 줄.strip()
                if not 조각:
                    continue
                try:
                    상태 = json.loads(조각.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if 상태.get("error"):
                    return False, str(상태["error"])
                말 = str(상태.get("status") or "")
                전체 = float(상태.get("total") or 0)
                받음 = float(상태.get("completed") or 0)
                비율 = (받음 / 전체) if 전체 > 0 else 0.0
                if on_progress:
                    on_progress(말, min(1.0, max(0.0, 비율)))
    except urllib.error.HTTPError as error:
        몸 = error.read().decode("utf-8", errors="replace")[:300]
        return False, f"받지 못했습니다 (HTTP {error.code}): {몸}"
    except urllib.error.URLError as error:
        return False, f"Ollama 에 연결하지 못했습니다: {error.reason}"
    except Exception as error:
        return False, f"받지 못했습니다: {error}"

    return True, f"{name} 을 받았습니다."


def _풀이름(이름: str) -> str:
    """`qwen2.5` → `qwen2.5:latest`. Ollama 가 태그를 그렇게 푼다.

    예전에는 콜론 앞부분만 견줬다. 그래서 `qwen2.5:7b` 를 받아 두고
    `qwen2.5:14b` 를 쓰겠다고 하면 **"바로 쓸 수 있습니다"** 라고 해 놓고
    정작 번역할 때 404 가 났다. 다른 모델인데 같다고 한 것이다.
    """
    이름 = (이름 or "").strip()
    return 이름 if ":" in 이름 else f"{이름}:latest"


def status(url: str, model: str) -> dict[str, Any]:
    """지금 어디까지 됐는지 한 번에.

    화면은 이것만 보고 "다음에 뭘 눌러야 하는지" 를 그린다.
    """
    base = base_from(url)
    깔림 = find_exe() is not None
    켜짐 = is_running(base)
    있는것 = models(base) if 켜짐 else []
    원하는것 = (model or "").strip()
    모델있음 = bool(원하는것) and _풀이름(원하는것) in {_풀이름(m) for m in 있는것}

    if not 깔림:
        다음 = "install"
        한마디 = "Ollama 가 안 깔려 있습니다"
    elif not 켜짐:
        다음 = "start"
        한마디 = "깔려 있는데 안 켜져 있습니다"
    elif not 모델있음:
        다음 = "pull"
        한마디 = f"{원하는것} 모델을 아직 안 받았습니다"
    else:
        다음 = "ready"
        한마디 = "바로 쓸 수 있습니다"

    return {
        "installed": 깔림,
        "running": 켜짐,
        "has_model": 모델있음,
        "models": 있는것,
        "next": 다음,
        "message": 한마디,
        "base": base,
    }
