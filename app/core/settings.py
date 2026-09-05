"""설정 저장.

API 키가 들어가는 파일이므로 저장소 안에 절대 만들지 않는다.
    윈도우: %APPDATA%/trans-text/settings.json
    그 외:  ~/.config/trans-text/settings.json
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "trans-text"

DEFAULTS: dict[str, Any] = {
    "asr": {
        # 받아쓰기 강도. app/core/preset.py 에 무엇이 있는지 적혀 있다
        "preset": "whisper",
        "model": "large-v3",   # 강도가 정하지만, 손으로 덮어쓸 수 있게 남긴다
        "device": "cuda",  # cuda / cpu
        "keep_nonverbal": True,
        "rescan_gaps": True,  # 빈 구간을 VAD 끄고 다시 훑는다
        # 관대한 VAD를 한 번 더 돌려 "소리는 있는데 자막이 없는 곳"을 찾는다.
        # 찾기만 하고 다시 받아쓰지는 않는다
        "check_coverage": True,
    },
    "translation": {
        # **번역을 어디로 보내는가 — 길 하나.** 손잡이 넷은 이 길이 정한다
        # (app/core/route.py). 길 밖의 값은 여기 안 둔다
        "route": "chat",
        # 사용자가 손으로 건드린 손잡이만 담긴다. 안 건드린 것은 길을 따라간다
        "고친것": {},
        "model": "",
        # 내 컴퓨터에서 도는 번역 모델의 주소. 비우면 공급자 기본값을 쓴다
        "url": "",
    },
    "output": {
        "offset_sec": 0.0,  # 자막이 밀리면 여기서 보정
        "gap_clear_sec": 1.5,
        "next_to_audio": True,  # 음원 옆에 저장. 못 쓰면 대체 폴더로
        "fallback_dir": "",
    },
    # **첫 실행에서 길을 물었나.** 물은 것은 다시 묻지 않는다 — 매번 나오는
    # 인트로는 두 번째부터 방해다
    "onboarded": False,
    # 공급자별 API 키. 사용자 PC에만 남는다
    "keys": {},
    # **파일 이름의 품번(RJ…)으로 DLsite 에 작품 정보를 물어본다.** 인터넷에 나가는
    # 것은 품번 하나다. 그래도 「내 파일 어디로 보내?」 하는 사람은 끌 수 있어야 하고,
    # 켜져 있다는 것을 어디선가 읽을 수 있어야 한다
    "works": {"lookup_online": True},
    # 창 크기와 자리. **읽기만 하고 담지 않았다** — 창을 키워 놓고 껐다
    # 켜면 도로 1100×760 이었다. 앱은 내가 해 둔 것을 기억하고, 스크립트는
    # 매번 처음부터다
    "window": {"width": 1100, "height": 760, "x": None, "y": None},
}


def config_dir() -> Path:
    override = os.environ.get("TRANSTEXT_CONFIG_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def settings_path() -> Path:
    return config_dir() / "settings.json"


def models_dir() -> Path:
    """받아쓰기 모델을 담아 둘 곳."""
    return config_dir() / "models"


def _cache_has_model(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any(folder.glob("models--*whisper*"))


def existing_model_cache() -> Path | None:
    """이미 받아 둔 받아쓰기 모델이 있으면 그 자리를 알려준다.

    예전에 이 컴퓨터에서 whisper 를 써 봤다면 모델이 이미 어딘가에 있다.
    3GB 를 두 번 받게 하면 안 된다.
    """
    후보 = []
    for name in ("HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE"):
        value = os.environ.get(name)
        if value:
            후보.append(Path(value))
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        후보.append(Path(hf_home) / "hub")
    후보.append(models_dir() / "hub")
    후보.append(Path.home() / ".cache" / "huggingface" / "hub")

    for folder in 후보:
        if _cache_has_model(folder):
            return folder
    return None


def use_our_model_dir() -> Path:
    """모델을 사용자 폴더 아래에 받게 만든다.

    두지 않으면 홈 폴더 여기저기에 캐시가 흩어져서, 3GB 가 어디 있는지 사용자가
    알 수 없다. `faster_whisper` 를 부르기 전에 해야 한다.

    다만 이미 받아 둔 모델이 있으면 그 자리를 그대로 쓴다. 자리를 옮기자고 3GB 를
    다시 받게 하는 것은 정리보다 훨씬 나쁘다.
    """
    이미있음 = existing_model_cache()
    if 이미있음 is not None:
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(이미있음))
        return 이미있음

    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(target))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(target / "hub"))
    return target


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 설정이 깨졌다고 프로그램이 안 켜지면 안 된다
        return copy.deepcopy(DEFAULTS)
    if not isinstance(stored, dict):
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, _옛것옮기기(stored))


def _옛것옮기기(값: dict[str, Any]) -> dict[str, Any]:
    """이미 깔아 쓰던 사람의 설정을 새 모양으로 옮긴다.

    **판올림했더니 고른 것이 조용히 바뀌어 있으면** 왜 그런지 알 길이 없다.
    번역기로 쓰던 사람이 갑자기 지시문 붙은 것을 받아 그것까지 번역된
    자막을 얻는 식이다. 그래서 옛 값을 읽어 새 자리에 옮겨 놓는다.

    옮긴 뒤에도 옛 열쇠는 지우지 않는다. 판을 되돌릴 수도 있기 때문이다 —
    **읽는 쪽이 새 자리만 본다**(`route.정해진값`). 남은 값은 안 읽힌다.

    **기본값을 얹기 전의 날것을 받는다.** 얹은 뒤에 보면 `route` 가 이미
    기본값으로 차 있어서, 옛 파일에 길이 없었다는 것을 알 수 없다.
    """
    if not isinstance(값.get("translation"), dict):
        return 값
    쪽 = 값.setdefault("translation", {})
    고친것 = 쪽.setdefault("고친것", {})

    # 「어디로 보낼까」 여섯 줄짜리 목록에서 고른 것이 곧 길이었다
    옛공급자 = str(쪽.get("provider") or "")
    if 옛공급자 and not 쪽.get("_옮김"):
        if 옛공급자 == "manual":
            쪽.setdefault("route", "chat")
        else:
            쪽.setdefault("route", "endpoint")
            고친것.setdefault("보내는길", 옛공급자)

    # 복사 형식은 「지시문 붙이기」 손잡이가 됐다
    옛형식 = str((값.get("output") or {}).get("copy_style") or "")
    if 옛형식 in ("ai", "plain") and not 쪽.get("_옮김"):
        if 옛형식 == "plain" and 쪽.get("route") == "chat":
            # 복붙 길에서 번역기용을 골라 두었다면 번역기 길로 옮긴다
            쪽["route"] = "translator"
        else:
            고친것.setdefault("지시문", 옛형식 == "ai")

    if 옛공급자 or 옛형식:
        쪽["_옮김"] = True
    return 값


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """기존 설정 위에 덮어쓰고 저장한 뒤 전체를 돌려준다."""
    merged = _merge(load(), patch)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)  # 쓰다 만 파일이 남지 않게 한 번에 바꾼다
    except OSError:
        # 디스크가 꽉 찼거나 다른 프로그램이 파일을 붙잡고 있을 수 있다.
        # 설정을 못 저장한다고 프로그램이 죽으면 안 된다. 이번 판만 못 남는다
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return merged
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # 윈도우 등에서 실패해도 넘어간다
    return merged


def for_display(settings: dict[str, Any]) -> dict[str, Any]:
    """화면으로 내보낼 때 API 키를 가린다."""
    shown = copy.deepcopy(settings)
    shown["keys"] = {
        name: ("●" * 12 if value else "") for name, value in settings.get("keys", {}).items()
    }
    return shown


def 비밀지우기(글: str, settings: dict[str, Any]) -> str:
    """적어 둔 키가 글 안에 있으면 지운다.

    **`for_display` 만으로는 모자란다.** 그것은 설정 칸만 가린다. 진단 파일은
    설정 말고 **기록(로그)도 통째로 실어 나른다.** 밖의 AI 가 401 을 주면서
    보낸 키를 답에 되비추면 그것이 그대로 기록에 남고, 진단 파일에 실려
    나간다. 화면은 「API 키는 들어가지 않습니다」 라고 적혀 있고, 사용자는
    그 말을 믿고 만든 사람에게 파일을 보낸다.

    그래서 **다 만든 글을 마지막에 한 번 훑어서** 실제 키 값을 지운다.
    어느 길로 새어 들어왔든 여기서 걸린다.
    """
    난것 = str(글 or "")
    for 값 in (settings or {}).get("keys", {}).values():
        비밀 = str(값 or "").strip()
        # 너무 짧으면 지우다가 멀쩡한 글을 망친다. 키는 원래 길다
        if len(비밀) >= 8:
            난것 = 난것.replace(비밀, "●●●(키를 지웠습니다)")
    return 난것


def api_key(settings: dict[str, Any], provider_id: str) -> str:
    return str(settings.get("keys", {}).get(provider_id, "")).strip()

# 창이 이보다 작아지면 안 된다 (`window.py` 의 `min_size` 와 같은 값).
# 최소화된 창은 크기를 아주 작게 또는 0 으로 말한다 — 그것을 담으면
# 다음에 켤 때 손톱만 한 창이 뜬다
창최소 = (880, 620)


def 창자리다듬기(값, 화면=None):
    """담기 전에 걸러 낸다. 담을 만한 것이 아니면 `None`.

    `화면` 은 (너비, 높이). 주면 그 안으로 밀어 넣는다 — 두 번째 모니터에서
    키워 놓고 그 모니터를 뽑으면, 담아 둔 자리가 화면 밖이라 **창이 안
    보이는 데서 뜬다.** 그러면 앱이 안 켜진 것처럼 보인다.
    """
    try:
        w = int((값 or {}).get("width") or 0)
        h = int((값 or {}).get("height") or 0)
    except (TypeError, ValueError):
        return None
    # 최소화·숨김 상태. 담으면 다음에 손톱만 하게 뜬다
    if w < 창최소[0] or h < 창최소[1]:
        return None

    난것 = {"width": w, "height": h}

    자리 = {}
    for 이름 in ("x", "y"):
        try:
            것 = (값 or {}).get(이름)
            자리[이름] = None if 것 is None else int(것)
        except (TypeError, ValueError):
            자리[이름] = None

    if 화면 and 자리["x"] is not None and 자리["y"] is not None:
        폭, 높 = int(화면[0]), int(화면[1])
        # 창이 화면보다 크면 화면에 맞춘다
        난것["width"] = min(난것["width"], 폭)
        난것["height"] = min(난것["height"], 높)
        # 제목줄이 조금이라도 보여야 손으로 옮길 수 있다
        자리["x"] = max(0, min(자리["x"], 폭 - 100))
        자리["y"] = max(0, min(자리["y"], 높 - 40))

    난것.update(자리)
    return 난것
