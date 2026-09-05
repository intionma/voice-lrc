"""whisper가 잘게 뱉은 세그먼트를 읽을 만한 자막 줄로 합친다.

whisper는 숨 쉬는 곳마다 끊어서 1초짜리 토막을 연달아 내놓는다. 그대로 두면
    1  어...
    2  그러니까
    3  오늘은
이 되어 자막이 깜빡거리고, 번역에 보낼 줄 수만 두 배가 된다.

붙어 있는 토막을 한 줄로 합치면 세 가지가 같이 좋아진다.
    - 자막이 안 깜빡인다
    - 번역에 보낼 줄이 절반으로 준다 (AI가 줄을 빠뜨릴 확률도 준다)
    - AI가 앞뒤 문맥을 더 많이 본다
"""

from __future__ import annotations

from typing import Any, Iterable

# 이 시간 이상 벌어져 있으면 다른 대사로 보고 합치지 않는다
MAX_GAP_SEC = 0.6

# 합친 줄이 이보다 길어지면 더 붙이지 않는다. 자막 한 줄로 읽을 수 있는 한계
MAX_CHARS = 40

# 합친 줄이 이보다 오래 걸리면 더 붙이지 않는다
MAX_DURATION_SEC = 7.0

# 이 글자로 끝나면 문장이 끝난 것으로 보고 거기서 끊는다
SENTENCE_END = "。．.！!？?…♪"

# 한 줄이 이보다 오래 떠 있거나 이보다 길면 나눈다.
#
# **여기가 비어 있었다.** 위의 `MAX_DURATION_SEC` 과 `MAX_CHARS` 는 **합치는 것만**
# 막는다. 모델이 처음부터 60초짜리 한 덩이로 뱉으면 그것을 나누는 곳이 어디에도
# 없어서, 그대로 `.lrc` 까지 갔다. 로그에 「자막 한 줄이 858초 동안 떠 있습니다」
# 가 찍혀 있었는데 그것을 손볼 자리가 없었다.
#
# 단어 시각이 꺼진 모델에서는 더 심해진다. 그때는 디코딩 창 하나가 통째로 한
# 줄이 되기 때문이다.
SPLIT_DURATION_SEC = 10.0
SPLIT_CHARS = 60

# 나눈 토막이 이보다 짧아지면 나누지 않는다.
#
# 「んっ…」 세 글자가 858초를 물고 있는 줄이 있다. 받아쓰기가 무음 구간을
# 통째로 문 것이다. 시간만 보고 나누면 「ん」 「っ」 「…」 이 되어 아무 도움이
# 안 된다. 그런 줄은 `lrc` 쪽에서 **일찍 지우는** 것으로 다룬다.
MIN_PIECE_CHARS = 8

# 나눌 자리를 찾을 때 문장 끝 다음으로 쳐 주는 글자
SOFT_BREAK = "、，,・…♪♡ "


def _is_sentence_end(text: str) -> bool:
    return bool(text) and text[-1] in SENTENCE_END


def _join(left: str, right: str) -> str:
    """일본어는 띄어쓰기가 없으므로 그냥 붙인다. 다만 이미 있는 공백은 살린다."""
    if not left:
        return right
    if not right:
        return left
    if left[-1].isspace() or right[0].isspace():
        return f"{left}{right}"
    # 문장부호 뒤에는 한 칸 띄워야 읽기 편하다
    if left[-1] in SENTENCE_END or left[-1] in "、，,":
        return f"{left} {right}"
    return f"{left}{right}"


def _쪼갤자리(글: str, 최대: int) -> int:
    """`최대` 글자 안에서 끊기 가장 좋은 자리. 없으면 `최대`.

    문장 끝이 제일 좋고, 쉼표 같은 것이 그다음이다. 둘 다 없으면 그냥 자른다 —
    한 줄이 60글자를 넘는 것보다는 어색하게 잘리는 편이 읽을 수 있다.
    """
    창 = 글[:최대]
    # 너무 앞에서 끊으면 토막이 된다. 뒤쪽 절반에서만 찾는다
    바닥 = max(1, 최대 // 2)
    for 글자들 in (SENTENCE_END, SOFT_BREAK):
        자리 = max((창.rfind(c) for c in 글자들), default=-1)
        if 자리 >= 바닥:
            return 자리 + 1
    return 최대


def split_long(
    segment: dict[str, Any],
    *,
    max_duration_sec: float = SPLIT_DURATION_SEC,
    max_chars: int = SPLIT_CHARS,
) -> list[dict[str, Any]]:
    """너무 긴 한 줄을 여러 줄로 나눈다. 나눌 것이 없으면 그대로 하나.

    시각은 **글자 수에 비례해서** 나눠 준다. 단어 시각이 없으면 이보다 잘할
    방법이 없다. 정확하지는 않지만, 60초짜리 한 줄이 통째로 떠 있는 것보다는
    훨씬 낫다.

    글자는 짧은데 시간만 긴 줄(무음 구간을 통째로 문 것)은 나누지 않는다.
    나눠 봐야 같은 글자를 여러 번 띄우게 될 뿐이다. 그것은 `lrc` 쪽에서
    **일찍 지우는** 것으로 다룬다.
    """
    글 = str(segment.get("ja", ""))
    시작 = float(segment["start"])
    끝 = float(segment["end"])
    걸린 = 끝 - 시작

    if len(글) <= max_chars and 걸린 <= max_duration_sec:
        return [dict(segment)]

    # 시간으로도 글자로도 몇 조각이 필요한지 보고 큰 쪽을 따른다.
    # 글자가 한도 안이어도 시간이 길면 나눈다 — 30초짜리 창 하나가 통째로 한
    # 줄이 된 경우다. 토막마다 제 시각을 가지면 자막이 말을 훨씬 잘 따라간다
    글자로 = -(-len(글) // max_chars)
    시간으로 = -(-int(걸린) // int(max_duration_sec)) if max_duration_sec >= 1 else 1
    조각수 = max(1, 글자로, 시간으로)
    # 토막이 잘아지면 나누지 않는다. 「ん」 「っ」 「…」 로 쪼개 봐야 소용없다
    조각수 = min(조각수, len(글) // MIN_PIECE_CHARS)
    if 조각수 <= 1:
        return [dict(segment)]
    한조각 = -(-len(글) // 조각수)

    토막들: list[str] = []
    남은 = 글
    while len(남은) > 한조각:
        자리 = _쪼갤자리(남은, 한조각)
        토막들.append(남은[:자리])
        남은 = 남은[자리:].lstrip()
    if 남은:
        토막들.append(남은)
    if len(토막들) <= 1:
        return [dict(segment)]

    총글자 = sum(len(t) for t in 토막들) or 1
    나온것: list[dict[str, Any]] = []
    자리 = 시작
    for 몇번째, 토막 in enumerate(토막들):
        몫 = 걸린 * len(토막) / 총글자
        이끝 = 끝 if 몇번째 == len(토막들) - 1 else min(끝, 자리 + 몫)
        새것 = dict(segment)
        새것.update({
            "ja": 토막, "start": round(자리, 3), "end": round(이끝, 3),
            # **나눠 준 시각은 짐작이다.** 글자 수로 나눈 것이라 그 토막이
            # 실제로 어디서 말해졌는지는 모른다. 들어 볼 때는 이 짐작이 아니라
            # **원래 한 덩이였던 구간**을 통째로 들려줘야 그 말이 그 안에 있다.
            # 짐작한 1~2초만 잘라 들려주면 숨소리만 나오고 끝난다
            "heard_start": round(시작, 3),
            "heard_end": round(끝, 3),
        })
        나온것.append(새것)
        자리 = 이끝
    return 나온것


def merge_segments(
    segments: Iterable[dict[str, Any]],
    *,
    max_gap_sec: float = MAX_GAP_SEC,
    max_chars: int = MAX_CHARS,
    max_duration_sec: float = MAX_DURATION_SEC,
) -> list[dict[str, Any]]:
    """붙어 있는 짧은 세그먼트를 합치고 번호를 1부터 다시 매긴다.

    합쳐진 줄에는 원래 어떤 세그먼트들이었는지 `sources` 로 남긴다.
    나중에 특정 구간만 다시 전사하거나 확인할 때 쓴다.
    """
    merged: list[dict[str, Any]] = []

    # **먼저 나누고 나서 합친다.** 위의 한도들은 합치는 것만 막는다. 모델이
    # 처음부터 60초짜리 한 덩이로 뱉으면 나눌 곳이 어디에도 없었다
    쪼갠것 = [조각 for s in segments for 조각 in split_long(s)]

    for segment in 쪼갠것:
        text = str(segment.get("ja", "")).strip()
        if not text:
            continue  # 빈 세그먼트는 자막으로 쓸 수 없다

        start = float(segment["start"])
        end = float(segment["end"])
        source = segment.get("index", len(merged) + 1)

        current = merged[-1] if merged else None
        if current is not None and _can_merge(
            current,
            start,
            end,
            text,
            max_gap_sec=max_gap_sec,
            max_chars=max_chars,
            max_duration_sec=max_duration_sec,
        ):
            current["ja"] = _join(current["ja"], text)
            # **끝은 뒤로 못 간다.** 다음 토막이 지금 줄 안에 통째로 들어
            # 있으면(0~6초 안의 2~4초 — whisper 가 실제로 이렇게 뱉는다)
            # `end` 를 그대로 받아 자막 끝이 6초에서 4초로 당겨졌다.
            # 말은 6초까지 하는데 자막이 2초 먼저 사라졌고, 다음 줄과의
            # 사이도 줄어든 끝 기준으로 재서 엉뚱하게 합쳐졌다
            current["end"] = max(current["end"], end)
            current["sources"].append(source)
            # 들려줄 구간도 함께 넓힌다. 나눠 준 시각이 짐작이어도,
            # **원래 덩이를 다 덮으면** 그 말이 그 안에 반드시 있다
            current["heard_start"] = min(
                current.get("heard_start", current["start"]),
                float(segment.get("heard_start", start)))
            current["heard_end"] = max(
                current.get("heard_end", current["end"]),
                float(segment.get("heard_end", end)))
            # 합친 줄의 확신도는 **가장 나쁜 토막**을 따른다. 하나라도 흐리게
            # 들었으면 그 줄은 흐리게 들은 것이다
            current["avg_logprob"] = min(current["avg_logprob"], _확신(segment))
            current["no_speech_prob"] = max(
                current["no_speech_prob"], _무음확률(segment)
            )
            continue

        merged.append(
            {
                "ja": text,
                "start": start,
                "end": end,
                # 들어 볼 때 쓸 구간. 나뉜 토막이면 원래 한 덩이였던 자리다
                "heard_start": float(segment.get("heard_start", start)),
                "heard_end": float(segment.get("heard_end", end)),
                "sources": [source],
                "avg_logprob": _확신(segment),
                "no_speech_prob": _무음확률(segment),
            }
        )

    for position, item in enumerate(merged, start=1):
        item["index"] = position

    return merged


def _확신(segment: dict[str, Any]) -> float:
    """이 토막을 얼마나 자신 있게 받아적었는가.

    합치면서 이 값을 버렸더니 `quality._check_confidence` 가 **한 번도 켜지지
    않았다.** 기본값 0.0 은 기준(-1.0)보다 높아서 늘 "자신 있음" 이 된다.
    실제로는 흐리게 들은 줄이 있어도 아무 말이 없었다.
    """
    try:
        return float(segment.get("avg_logprob", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _무음확률(segment: dict[str, Any]) -> float:
    try:
        return float(segment.get("no_speech_prob", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _can_merge(
    current: dict[str, Any],
    start: float,
    end: float,
    text: str,
    *,
    max_gap_sec: float,
    max_chars: int,
    max_duration_sec: float,
) -> bool:
    if start - current["end"] > max_gap_sec:
        return False  # 사이가 비면 다른 대사다
    if _is_sentence_end(current["ja"]):
        return False  # 문장이 이미 끝났으면 거기서 끊는 편이 자연스럽다
    if len(current["ja"]) + len(text) > max_chars:
        return False
    # 겹치는 토막이면 `end` 가 지금 끝보다 앞일 수 있다. 합친 뒤의 끝
    # (`max`) 으로 재야 한도가 맞는다
    if max(end, current["end"]) - current["start"] > max_duration_sec:
        return False
    return True
