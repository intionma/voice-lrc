"""받아쓰기 모델의 낱말 사전이 몇 칸인지 본다.

## 왜 따로 뒀나

`flyfront/anime-whisper-faster` 는 `tokenizer.json` 이 딸려 오지 않는다.
그러면 faster-whisper 가 **`openai/whisper-tiny` 것을 대신 쓴다.**

칸 수가 다르다. tiny 는 51,865, large-v3 계열은 51,866 — 광둥어(`<|yue|>`)
하나가 더 있다. 위스퍼는 **시각 토큰이 사전 맨 끝**에 있어서

    timestamp_begin = no_timestamps + 1        (faster_whisper/tokenizer.py)

한 칸이 밀리면 그 뒤가 통째로 밀린다. 글자는 거의 맞게 나오는데 **시각이
망가진다.** 실제로 잰 값이다 — 구간 가운데 길이 0.37초 (large-v3 는 4.72초).

같은 어긋남이 `suppress_tokens=[]` · `word_timestamps=True` 에서는 파이썬
오류도 없이 **프로세스를 죽인다.**

고치는 쪽(`scripts/fix_model_tokenizer.py`)과 재는 쪽
(`scripts/try_anime_model.py`)이 같은 셈을 써야 해서 여기로 뺐다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def 담아두는곳들() -> list[Path]:
    """모델을 받아 두는 곳들. 앞에서부터 본다."""
    곳들: list[Path] = []
    if os.environ.get("HUGGINGFACE_HUB_CACHE"):
        곳들.append(Path(os.environ["HUGGINGFACE_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        곳들.append(Path(os.environ["HF_HOME"]) / "hub")
    곳들.append(Path.home() / ".cache" / "huggingface" / "hub")

    본것: list[Path] = []
    for 곳 in 곳들:
        if 곳 not in 본것:
            본것.append(곳)
    return 본것


def 담아둔데서찾기(이름: str) -> Path | None:
    """받아 둔 곳을 직접 뒤진다.

    `model.bin` 이 있어야 **받은 것으로 친다.** 받다 만 것은 폴더만 남는다.
    """
    폴더이름 = "models--" + 이름.replace("/", "--")
    for 곳 in 담아두는곳들():
        찍은것들 = 곳 / 폴더이름 / "snapshots"
        if not 찍은것들.is_dir():
            continue
        for 하나 in sorted(찍은것들.iterdir()):
            if (하나 / "model.bin").is_file():
                return 하나
    return None


def 모델폴더(이름: str) -> Path | None:
    """받아 둔 모델이 어디 있는지. 안 받았으면 `None`.

    **받아쓰기가 쓰는 길을 그대로 쓴다.** 여기서 한 번 헛돌았다 —
    `snapshot_download(이름, local_files_only=True)` 를 조건 없이 부르면
    「파일이 다 없다」며 튕긴다. faster-whisper 는 `model.bin` 등 다섯 가지만
    골라 받기 때문이다(`allow_patterns`). 그래서 분명히 받아 둔 모델을
    「아직 안 받았습니다」라고 했다.
    """
    # 폴더 이름을 그대로 준 것이면 그것이 답이다. 받아쓰기도 그렇게 본다
    # (`WhisperModel.__init__` 의 `os.path.isdir`). **이것을 안 보면 직접
    # 바꿔 둔 모델이 「안 받았다」로 나와서 또 막힌다**
    if 이름 and Path(이름).is_dir():
        return Path(이름)

    try:
        from faster_whisper.utils import download_model
    except ImportError:
        download_model = None
    if download_model is not None:
        try:
            # **받으러 가지 않는다.** 이미 있는 것만 본다
            return Path(download_model(이름, local_files_only=True))
        except Exception:
            pass
    return 담아둔데서찾기(이름)


def 어휘목록(폴더: Path) -> list[str]:
    """ct2 모델이 실제로 쓰는 낱말들을 **번호 차례대로.**"""
    for 이름 in ("vocabulary.json", "vocabulary.txt"):
        길 = 폴더 / 이름
        if not 길.is_file():
            continue
        try:
            if 길.suffix == ".json":
                것 = json.loads(길.read_text(encoding="utf-8"))
                if isinstance(것, dict):
                    # {낱말: 번호} 로 담긴 판도 있다. 번호 차례로 편다
                    return [낱말 for 낱말, _ in sorted(것.items(), key=lambda 짝: 짝[1])]
                return [str(낱말) for 낱말 in 것]
            return [줄.rstrip("\n") for 줄 in 길.open(encoding="utf-8")]
        except (OSError, json.JSONDecodeError, TypeError):
            return []
    return []


def 어휘칸수(폴더: Path) -> int:
    """ct2 모델이 실제로 쓰는 칸 수. **이것이 맞춰야 할 값이다.**"""
    return len(어휘목록(폴더))


def 사전의번호(길: Path) -> dict[str, int]:
    """사전이 낱말마다 몇 번을 매기는지. `{낱말: 번호}`."""
    try:
        것 = json.loads(길.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    모델 = 것.get("model") or {}
    번호 = dict(모델.get("vocab") or {})
    for 덧붙인것 in 것.get("added_tokens") or []:
        낱말, 매긴번호 = 덧붙인것.get("content"), 덧붙인것.get("id")
        if 낱말 is not None and 매긴번호 is not None:
            번호[str(낱말)] = int(매긴번호)
    return 번호


def 사전칸수(길: Path) -> int:
    return len(사전의번호(길))


def 어긋난자리(어휘: list[str], 번호: dict[str, int]) -> int | None:
    """**칸 수가 같아도 내용이 다르면 소용없다.**

    여기서 한 번 헛돌았다. `large-v3` 사전이 51,866칸이라 맞다고 보고 물려
    줬는데, 낱말 시각을 켜자 프로세스가 접근 위반(0xC0000005)으로 죽었다.
    번호가 어긋나면 C++ 이 임베딩 행렬 **밖을 읽는다.**

    칸 수는 같은데 자리가 다를 수 있다. anime-whisper 는 kotoba-whisper
    계열이라 openai 것과 낱말이 같으리라는 보장이 없다.

    첫 번째로 어긋나는 번호를 돌려준다. 다 맞으면 `None`.
    """
    if not 어휘 or not 번호:
        return 0
    if len(어휘) != len(번호):
        return min(len(어휘), len(번호))
    for 자리, 낱말 in enumerate(어휘):
        if 번호.get(낱말) != 자리:
            return 자리
    return None


# 시각이 어디서부터 시작하는지를 정하는 낱말들.
#
# `timestamp_begin = no_timestamps + 1` (faster_whisper/tokenizer.py 78줄).
# 그리고 그 번호는 **이름으로 찾는다** — `token_to_id("<|notimestamps|>")`.
# 그러니 모델과 사전이 이 낱말들을 **같은 번호로 봐야** 시각이 맞는다.
볼낱말 = ("<|endoftext|>", "<|startoftranscript|>", "<|ja|>",
         "<|transcribe|>", "<|notimestamps|>")


def 특별한자리(어휘: list[str]) -> dict[str, int | None]:
    """모델이 이 낱말들을 몇 번으로 보는지."""
    자리 = {낱말: i for i, 낱말 in enumerate(어휘)}
    return {낱말: 자리.get(낱말) for 낱말 in 볼낱말}


def 정렬머리(폴더: Path) -> dict[str, Any]:
    """ct2 가 낱말 정렬에 쓰는 어텐션 머리들.

    **여기가 두 번째 용의자다.** 낱말 시각을 켜면 `model.align()` 이 이것을
    보는데, 없는 층을 가리키고 있으면 C++ 이 그냥 죽는다(0xC0000005).

        config.alignment_heads = gen_config.alignment_heads   ← 그대로 물려받는다
        ...
        num_layers = model.config.decoder_layers              ← 없을 때만 이렇게
        range(num_layers // 2, num_layers)

    anime-whisper 는 kotoba-whisper 를 거친 **디코더가 얇은** 모델인데,
    `generation_config` 를 large-v3 에서 물려받았으면 **32층짜리 머리 목록**이
    그대로 들어간다. 2층짜리 모델이 32층을 가리키면 밖을 읽는다.
    """
    길 = 폴더 / "config.json"
    try:
        것 = json.loads(길.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"있나": False}
    머리들 = 것.get("alignment_heads")
    if not isinstance(머리들, list) or not 머리들:
        return {"있나": False}
    층들 = [짝[0] for 짝 in 머리들 if isinstance(짝, (list, tuple)) and 짝]
    return {"있나": True, "개수": len(머리들),
            "제일높은층": max(층들) if 층들 else None}


def 직접바꾼곳() -> Path:
    """우리가 직접 ct2 로 바꾼 모델을 두는 곳.

    남이 바꿔 놓은 `flyfront/anime-whisper-faster` 는 `tokenizer.json` 이
    빠져 있어서 남의 사전을 빌려 쓴다. **직접 바꾸면 그 문제가 없어진다** —
    변환기가 어휘를 그 모델의 사전에서 그대로 뽑아 쓰기 때문에 어긋날 수가
    없다(`ctranslate2/converters/transformers.py` 의 `get_vocabulary`).
    """
    from app.core import settings as settings_store

    return settings_store.config_dir() / "models" / "anime-whisper-ct2"


def 쓸모델(이름: str) -> str:
    """직접 바꿔 둔 것이 있으면 그것을, 없으면 받아 온 이름을.

    **`model.bin` 이 있어야 바꾼 것으로 친다.** 바꾸다 만 폴더가 남아 있으면
    faster-whisper 가 그것을 열려다 죽는다.
    """
    곳 = 직접바꾼곳()
    if (곳 / "model.bin").is_file():
        return str(곳)
    return 이름


def 사전이맞나(이름: str) -> tuple[bool, str]:
    """`(맞나, 사람이 읽을 한 줄)`.

    **모르면 「안 맞는다」고 한다.** 모르면서 맞다고 하면, 시각이 망가진 채로
    12분을 받아쓰게 만든다.
    """
    폴더 = 모델폴더(이름)
    if 폴더 is None:
        return False, "아직 안 받은 모델입니다"
    어휘 = 어휘목록(폴더)
    if not 어휘:
        return False, "어휘 파일을 못 읽어 몇 칸인지 모릅니다"
    있는사전 = 폴더 / "tokenizer.json"
    if not 있는사전.is_file():
        return False, f"모델 어휘 {len(어휘)}칸 · 딸려 온 사전 없음 (남의 것을 빌려 씁니다)"
    번호 = 사전의번호(있는사전)
    자리 = 어긋난자리(어휘, 번호)
    if 자리 is not None:
        # **칸 수만 맞다고 하면 안 된다.** 51,866 대 51,866 이라 맞다고 봤다가
        # 낱말 시각에서 접근 위반으로 죽었다
        틀린것 = 어휘[자리] if 자리 < len(어휘) else "(없음)"
        return False, (f"모델 어휘 {len(어휘)}칸 · 사전 {len(번호)}칸 — "
                       f"**{자리}번째부터 어긋납니다** (모델은 {틀린것!r}, "
                       f"사전은 그것을 {번호.get(틀린것)}번으로 봅니다)")
    return True, f"모델 어휘 {len(어휘)}칸 · 사전이 낱말까지 다 맞습니다"
