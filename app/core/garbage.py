"""받아쓰기가 실패한 줄을 알아본다.

whisper 는 못 알아들으면 비워 두지 않는다. 그럴듯한 글자를 지어낸다. 그것이
번역으로 넘어가면 AI 가 헛소리를 그럴듯한 한국어로 바꿔 주고, 자막을 켜면 대사와
전혀 맞지 않는다. 재료가 썩으면 누가 요리해도 안 된다.

실제로 받은 결과에서 본 것들이다.

    39  お疲れ様でした
    40  ご視聴ありがとうございました      ← 유튜브 마무리 인사. 음성에 없다
    73  あ、いっく、あ welcome           ← 일본어 음성에 영어가 튀어나온다
    74  ううううう…(300자)               ← 같은 글자 폭주
    87  ゴク゛ウ゛ギィイイ inter
    101 acidを
    102 隠ししばらくがdaughter

신음처럼 말이 아닌 소리를 억지로 글자로 만들다 나온다. 그래서 한 군데가 망가지면
그 언저리가 통째로 망가진다.

여기서는 알아보기만 하고 고치지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# whisper 가 무음에서 잘 지어내는 상투구.
# 유튜브 자막으로 배워서 영상 마무리 인사를 끼워 넣는다.
HALLUCINATION_PHRASES = (
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "チャンネル登録",
    "最後までご視聴",
    "お疲れ様でした",
    "ありがとうございました",
    "次回もお楽しみに",
    "字幕は",
    "Thanks for watching",
    "Subscribe",
    "音量注意",
)

# 일본어 줄에 섞여 나오는 영어. 말이 아닌 소리를 글자로 만들다 나온다
_LATIN = re.compile(r"[A-Za-z]{3,}")

# 일본어 글자가 하나라도 있는지
_JAPANESE = re.compile(r"[ぁ-んァ-ヴ一-龥ー]")

# 같은 글자가 이만큼 이어지면 폭주다
RUN_LENGTH = 12

# 한 줄이 이보다 길면 사람이 한 호흡에 한 말이 아니다
TOO_LONG = 120

# 이 비율 넘게 망가지면 그 구간을 통째로 다시 봐야 한다
BROKEN_RATIO = 0.5


def _글자폭주(text: str) -> bool:
    """`ううううう…` 처럼 같은 글자가 이어지는지."""
    이어짐, 앞글자 = 1, ""
    for 글자 in text:
        if 글자 == 앞글자:
            이어짐 += 1
            if 이어짐 >= RUN_LENGTH:
                return True
        else:
            이어짐, 앞글자 = 1, 글자
    return False


def _섞인영어(text: str) -> bool:
    """일본어 줄에 영어 단어가 끼어 있는지."""
    if not _JAPANESE.search(text):
        return False
    return bool(_LATIN.search(text))


# 탁점·반탁점. 말이 아닌 소리를 글자로 만들다 보면 이것이 잔뜩 붙는다
_다쿠텐 = "\u3099\u309a\u309b\u309c"


def _읽을수없는글자(text: str) -> bool:
    """조합이 깨진 글자가 섞였는지. `う゛ーっう゛ぉぉ゛` 같은 것."""
    붙는것 = sum(1 for 글자 in text if unicodedata.combining(글자) or 글자 in _다쿠텐)
    return 붙는것 >= 3


def _일본어가없음(text: str) -> bool:
    """일본어 음성인데 일본어가 한 글자도 없는 줄. `launch` 같은 것."""
    if _JAPANESE.search(text):
        return False
    return bool(_LATIN.search(text))


def why_broken(text: str) -> str:
    """이 줄이 왜 망가졌는지. 멀쩡하면 빈 값."""
    글 = (text or "").strip()
    if not 글:
        return ""
    if any(말 in 글 for 말 in HALLUCINATION_PHRASES):
        return "지어낸 상투구"
    if _글자폭주(글):
        return "같은 글자 폭주"
    if _일본어가없음(글):
        return "일본어가 한 글자도 없음"
    if _섞인영어(글):
        return "일본어에 영어가 섞임"
    if _읽을수없는글자(글):
        return "읽을 수 없는 글자"
    if len(글) >= TOO_LONG:
        return "한 줄이 너무 김"
    return ""


def find_broken(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """망가진 줄들. `{index, at, why}` 목록."""
    걸린것 = []
    for segment in segments:
        까닭 = why_broken(str(segment.get("ja", "")))
        if 까닭:
            걸린것.append(
                {
                    "index": segment["index"],
                    "start": float(segment.get("start", 0.0)),
                    "end": float(segment.get("end", 0.0)),
                    "why": 까닭,
                }
            )
    return 걸린것


def find_broken_spans(
    segments: list[dict[str, Any]],
    *,
    window: int = 8,
    ratio: float = BROKEN_RATIO,
) -> list[tuple[float, float]]:
    """통째로 망가진 구간. 그 자리만 다시 받아쓰면 된다.

    한 줄만 이상한 것은 그냥 그 줄이 나쁜 것이다. 여러 줄이 몰려서 망가졌으면
    그 언저리 소리를 whisper 가 통째로 못 알아들은 것이라, 다시 훑을 값어치가
    있다.
    """
    if not segments:
        return []

    망가짐 = {줄["index"] for 줄 in find_broken(segments)}
    if not 망가짐:
        return []

    나쁜자리 = [i for i, s in enumerate(segments) if s["index"] in 망가짐]
    구간: list[tuple[int, int]] = []
    for 자리 in 나쁜자리:
        시작 = max(0, 자리 - window // 2)
        끝 = min(len(segments) - 1, 자리 + window // 2)
        이웃 = segments[시작 : 끝 + 1]
        나쁜수 = sum(1 for s in 이웃 if s["index"] in 망가짐)
        if 나쁜수 / len(이웃) < ratio:
            continue  # 혼자 이상한 줄이다
        if 구간 and 시작 <= 구간[-1][1] + 1:
            구간[-1] = (구간[-1][0], max(구간[-1][1], 끝))
        else:
            구간.append((시작, 끝))

    return [
        (float(segments[a]["start"]), float(segments[b]["end"])) for a, b in 구간
    ]
