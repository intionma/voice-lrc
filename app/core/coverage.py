"""소리는 있는데 자막이 없는 자리를 찾는다.

받아쓰기가 대사를 통째로 빠뜨린다. 번역이 틀린 것이 아니라 **일본어 줄 자체가
없다.** 그러면 번역할 것도 없고 자막에도 없고, 플레이어는 앞 줄에서 멈춘 것처럼
보인다.

왜 빠지는가. `vad_filter` 가 모델에 넘기기 **전에** 잘라 내기 때문이다.

    소리 → [VAD] → 모델 → 받아쓴 줄
            ↑ 여기서 버려지면 모델은 구경도 못 한다

`no_speech_threshold` 를 아무리 올려도 소용이 없다. 그것은 모델이 받아적은 **뒤에**
버릴지 정하는 값이다. 이미 VAD 가 버린 구간에는 적용될 일이 없다.

그래서 여기서는 **전사용과 다른, 더 관대한 VAD 를 한 번 더 돌린다.** 같은 VAD 로
다시 보면 같은 것을 또 버리므로 아무것도 못 찾는다.

    전사용 VAD    잡음과 무음을 걷어 내고 깨끗하게 받아적는 것이 목적
    검사용 VAD    속삭임·신음·작은 소리까지 넓게 잡아 "빠진 곳" 후보를 찾는 것이 목적

검사용이 "여기 소리 있다" 는데 받아쓴 줄이 없으면 그 자리가 누락 후보다.

**여기서는 찾기만 한다. 다시 받아쓰지 않는다.** 관대한 VAD 는 BGM, 옷 스치는
소리, 숨소리, 침대 소리도 소리로 잡는다. 확인하지 않고 다시 받아쓰면 효과음을
그럴듯한 일본어로 받아적어서 자막에 넣게 된다. 그것이 빈 자막보다 나쁘다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 검사용 VAD 값. 전사용보다 넓게 잡는다.
# 확정값이 아니라 실제 음원으로 맞춰 갈 출발점이다.
DETECT_THRESHOLD = 0.30
DETECT_MIN_SPEECH_MS = 100
DETECT_MIN_SILENCE_MS = 500
DETECT_SPEECH_PAD_MS = 600

# 이보다 짧게 겹치는 것은 겹친 것으로 치지 않는다.
# 받아쓴 줄의 끝자락이 살짝 물린 것뿐일 수 있다
MIN_OVERLAP_SEC = 0.3

# 이보다 짧은 후보는 버린다. 클릭음이나 숨 한 번일 가능성이 크다
MIN_SPAN_SEC = 0.8

# 이만큼 가까운 후보끼리는 하나로 합친다. 잘게 쪼개 놓으면 확인할 수가 없다
MERGE_GAP_SEC = 1.0


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """두 구간이 겹치는 시간."""
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _겹치지않게_모으기(구간: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """겹치는 구간을 하나로 합쳐 서로 안 겹치게 만든다.

    자막 줄끼리 조금씩 겹칠 수 있는데(VAD 여유 400ms), 그대로 더하면 같은
    시간을 두 번 센다. 합쳐 놓으면 두 번 세지 않고, 아래 훑기도 빨라진다.
    """
    if not 구간:
        return []
    정렬 = sorted(구간)
    합침 = [정렬[0]]
    for 시작, 끝 in 정렬[1:]:
        앞시작, 앞끝 = 합침[-1]
        if 시작 <= 앞끝:
            합침[-1] = (앞시작, max(앞끝, 끝))
        else:
            합침.append((시작, 끝))
    return 합침


def _덮인시간(
    말: list[tuple[float, float]], 자막: list[tuple[float, float]]
) -> list[float]:
    """말 구간마다 자막이 덮은 시간. 앞에서 뒤로 한 번만 훑는다.

    예전에는 말 구간 하나마다 자막 **전부**를 훑었다. 2시간짜리는 양쪽이 수천
    줄이라 곱하면 수천만 번이 되어, 이 계산 하나가 **9초**씩 걸렸다.
    받아쓰기가 끝난 뒤 아무 표시 없이 멈춰 있는 것처럼 보이던 시간이다.

    양쪽 다 시각 순이므로 손가락 하나로 따라가면 된다.
    """
    덮인것 = []
    자리 = 0
    for 시작, 끝 in 말:
        while 자리 < len(자막) and 자막[자리][1] <= 시작:
            자리 += 1        # 이 자막은 이제 다시 볼 일이 없다
        합 = 0.0
        훑기 = 자리
        while 훑기 < len(자막) and 자막[훑기][0] < 끝:
            합 += _overlap((시작, 끝), 자막[훑기])
            훑기 += 1
        덮인것.append(합)
    return 덮인것


def find_uncovered(
    speech: list[tuple[float, float]],
    segments: list[dict[str, Any]],
    *,
    min_overlap_sec: float = MIN_OVERLAP_SEC,
    min_span_sec: float = MIN_SPAN_SEC,
    merge_gap_sec: float = MERGE_GAP_SEC,
) -> list[tuple[float, float]]:
    """소리는 잡혔는데 받아쓴 줄이 없는 구간.

    모델도 음원도 없이 도는 순수 계산이다. 시험은 여기에 붙는다.
    """
    if not speech:
        return []

    자막구간 = _겹치지않게_모으기(
        [(float(s["start"]), float(s["end"])) for s in segments]
    )
    볼것 = [(float(a), float(b)) for a, b in speech if float(b) > float(a)]
    볼것.sort()
    덮인것 = _덮인시간(볼것, 자막구간)

    빠진것: list[tuple[float, float]] = []
    for (시작, 끝), 덮인시간 in zip(볼것, 덮인것):
        if 덮인시간 >= min_overlap_sec:
            continue  # 자막이 있다
        if 끝 - 시작 < min_span_sec:
            continue  # 너무 짧다. 클릭음이나 숨소리일 가능성이 크다
        빠진것.append((시작, 끝))

    return _merge_close(빠진것, merge_gap_sec)


def _merge_close(
    구간: list[tuple[float, float]], gap: float
) -> list[tuple[float, float]]:
    """가까운 것끼리 하나로 합친다."""
    if not 구간:
        return []
    구간 = sorted(구간)
    합침 = [구간[0]]
    for 시작, 끝 in 구간[1:]:
        앞시작, 앞끝 = 합침[-1]
        if 시작 - 앞끝 <= gap:
            합침[-1] = (앞시작, max(앞끝, 끝))
        else:
            합침.append((시작, 끝))
    return 합침


def covered_ratio(
    segments: list[dict[str, Any]], speech: list[tuple[float, float]]
) -> float:
    """검사용 VAD 가 잡은 소리 중 자막이 붙은 비율."""
    if not speech:
        return 1.0
    자막구간 = _겹치지않게_모으기(
        [(float(s["start"]), float(s["end"])) for s in segments]
    )
    소리시간 = sum(max(0.0, b - a) for a, b in speech)
    if 소리시간 <= 0:
        return 1.0
    볼것 = sorted((float(a), float(b)) for a, b in speech)
    덮인시간 = sum(
        min(max(0.0, b - a), 덮인)
        for (a, b), 덮인 in zip(볼것, _덮인시간(볼것, 자막구간))
    )
    return max(0.0, min(1.0, 덮인시간 / 소리시간))


# ---- faster-whisper 쪽에 붙는 부분 ----
#
# 여기부터는 판에 따라 이름이 달라진다. 못 부르면 진단을 건너뛰고 넘어간다.
# 진단이 안 된다고 자막을 못 만들면 안 된다.


def _이미읽은소리(것) -> bool:
    """경로가 아니라 이미 읽어 놓은 소리 배열인가.

    numpy 를 여기서 부르지 않는다. 이 모듈은 numpy 없이도 읽혀야 한다.
    """
    if isinstance(것, (str, bytes, Path)):
        return False
    return hasattr(것, "shape") and hasattr(것, "dtype")


class DetectorUnavailable(RuntimeError):
    """설치된 faster-whisper 로는 검사용 VAD 를 돌릴 수 없다."""


def detect_speech(
    audio,
    *,
    threshold: float = DETECT_THRESHOLD,
    min_speech_ms: int = DETECT_MIN_SPEECH_MS,
    min_silence_ms: int = DETECT_MIN_SILENCE_MS,
    speech_pad_ms: int = DETECT_SPEECH_PAD_MS,
) -> list[tuple[float, float]]:
    """음원에서 소리가 나는 구간을 넓게 잡는다. 초 단위로 돌려준다.

    모델을 부르지 않는다. VAD 만 돌리므로 GPU 도 쓰지 않고 빠르다.

    `audio` 는 **파일 경로이거나 이미 읽어 놓은 소리 배열**이다. 소리를 고른
    강도에서는 고른 배열이 그대로 들어온다. 예전에는 경로만 받아서, 배열이
    들어오면 `str(배열)` 을 파일 이름으로 알고 읽다가 실패했다. 그러면 이 검사가
    **조용히 건너뛰어졌다.** 하필 소리를 고르는 강도가 「속삭임」·「아주 정확」·
    「극한」이라, 빠진 곳 찾기가 가장 필요한 자리에서만 안 돌았다.
    """
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError as error:
        raise DetectorUnavailable(f"검사용 VAD 를 불러오지 못했습니다: {error}") from error

    SAMPLE_RATE = 16000
    if _이미읽은소리(audio):
        소리 = audio
    else:
        try:
            소리 = decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
        except Exception as error:
            raise DetectorUnavailable(f"음원을 읽지 못했습니다: {error}") from error

    # 판마다 인자 이름이 조금씩 다르다. 아는 것만 넣고, 모르는 것이 있으면
    # 하나씩 빼면서 다시 해 본다
    후보 = [
        dict(
            threshold=threshold,
            min_speech_duration_ms=min_speech_ms,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
        ),
        dict(threshold=threshold, min_silence_duration_ms=min_silence_ms),
        dict(threshold=threshold),
        dict(),
    ]
    마지막오류: Exception | None = None
    for 인자 in 후보:
        try:
            찾은것 = get_speech_timestamps(소리, VadOptions(**인자))
            break
        except TypeError as error:
            마지막오류 = error
    else:
        raise DetectorUnavailable(f"검사용 VAD 설정을 넘기지 못했습니다: {마지막오류}")

    구간 = []
    for 하나 in 찾은것 or []:
        시작 = float(하나["start"]) / SAMPLE_RATE
        끝 = float(하나["end"]) / SAMPLE_RATE
        if 끝 > 시작:
            구간.append((round(시작, 3), round(끝, 3)))
    return 구간
