"""모델에 넣기 전에 소리를 고른다.

**작은 소리가 통째로 사라지는 가장 큰 원인은 VAD 다.** VAD 는 소리의 크기를
보고 "말인지 아닌지" 를 정하는데, 속삭임은 원래 작아서 기준에 못 미친다.
기준(`threshold`)을 낮추는 것으로도 되지만 한계가 있다. 아예 **소리를 키워서
넣으면** VAD 가 넉넉히 잡는다.

동인음성은 녹음 레벨이 제각각이다. 어떤 작품은 -30dB 로 아주 작게 녹음돼 있고,
그런 파일은 무엇을 해도 절반이 사라진다.

여기서 하는 것은 셋뿐이다.

    1. 아주 낮은 웅웅거림을 깎는다 (80Hz 아래)
    2. 전체를 적당한 크기로 키운다
    3. 튀는 곳이 찢어지지 않게 누른다

**소리를 "예쁘게" 만들려는 것이 아니다.** 잡음 제거나 음질 개선은 하지 않는다.
그런 것은 자음을 뭉개서 오히려 잘못 알아듣게 만든다. 크기만 손댄다.
"""

from __future__ import annotations

from pathlib import Path

SAMPLE_RATE = 16000

# 목표 크기. 이 언저리로 맞춘다 (RMS 기준, 대략 -20 dBFS)
TARGET_RMS = 0.1

# 아무리 작아도 이만큼까지만 키운다. 무한정 키우면 잡음까지 같이 커져서
# 모델이 없는 말을 지어내기 시작한다
MAX_GAIN = 12.0

# 이보다 크면 줄인다. 이미 큰 것을 더 키울 이유가 없다
MIN_GAIN = 0.5

# 이보다 긴 파일은 손대지 않는다. 통째로 메모리에 올려야 하는데
# 1시간이 대략 230MB 다
MAX_MINUTES = 200


class PreprocessUnavailable(RuntimeError):
    """소리를 고를 수 없다. 원본 그대로 넣으면 된다."""


def _highpass(소리, rate: int = SAMPLE_RATE, cutoff: float = 80.0):
    """아주 낮은 웅웅거림을 깎는다.

    에어컨 소리나 마이크 진동 같은 것이다. 사람 목소리는 여기 없다.
    이것이 남아 있으면 소리를 키울 때 같이 커져서, 정작 목소리는 못 키운다.

    움직이는 평균을 빼는 방식이다. 거칠지만 셈이 안전하고 아주 빠르다.
    되풀이식 필터(`y = a*(y + x - x_prev)`)를 벡터로 풀어 보려 했는데,
    `a**n` 이 금세 0 으로 내려가서 0 으로 나누게 된다. scipy 를 끌어오면
    깔끔하지만 그것 하나 때문에 30MB 를 더 받게 할 이유가 없다.
    """
    import numpy as np

    창 = max(3, int(rate / max(1.0, cutoff)))
    if len(소리) <= 창:
        return 소리

    # 누적합으로 움직이는 평균을 구한다.
    #
    # **통째로 하면 안 된다.** 2시간짜리는 표본이 1억이 넘는데, 셈이 틀어지지
    # 않게 float64 로 올리면 그것만 900MB 고 누적합이 또 900MB 다. 조각내서
    # 한 번에 조금씩만 올린다. 조각마다 앞뒤로 창만큼 겹쳐서 이어 붙인다.
    앞 = 창 // 2
    뒤 = 창 - 앞
    결과 = np.empty_like(소리)
    덩어리 = 1 << 22  # 표본 400만 개씩. float64 로 올려도 60MB 안쪽이다

    for 시작 in range(0, len(소리), 덩어리):
        끝 = min(시작 + 덩어리, len(소리))
        # 이 조각의 평균을 구하려면 앞뒤로 창만큼 더 봐야 한다
        왼쪽 = max(0, 시작 - 앞)
        오른쪽 = min(len(소리), 끝 + 뒤)
        조각 = 소리[왼쪽:오른쪽].astype(np.float64)

        # 가장자리는 첫/끝 값으로 채워서 길이를 맞춘다
        앞채움 = np.full(앞 - (시작 - 왼쪽), 조각[0], dtype=np.float64)
        뒤채움 = np.full(뒤 - (오른쪽 - 끝), 조각[-1], dtype=np.float64)
        확장 = np.concatenate([앞채움, 조각, 뒤채움])

        누적 = np.concatenate([[0.0], np.cumsum(확장)])
        평균 = (누적[창:] - 누적[:-창]) / 창
        결과[시작:끝] = (소리[시작:끝] - 평균[: 끝 - 시작].astype(np.float32))

    return 결과


def measure(소리) -> dict[str, float]:
    """이 소리가 얼마나 작은지 재 본다. 진단에 쓴다."""
    import numpy as np

    if len(소리) == 0:
        return {"rms": 0.0, "peak": 0.0, "gain": 1.0}
    rms = float(np.sqrt(np.mean(소리.astype(np.float64) ** 2)))
    peak = float(np.max(np.abs(소리)))
    gain = TARGET_RMS / rms if rms > 1e-6 else 1.0
    return {"rms": rms, "peak": peak, "gain": min(max(gain, MIN_GAIN), MAX_GAIN)}


def load_leveled(audio: Path, *, target_rms: float = TARGET_RMS):
    """음원을 읽어 크기를 고른 배열로 돌려준다.

    `(소리, 잰 것)` 을 준다. faster-whisper 는 배열을 그대로 받는다.
    """
    try:
        import numpy as np
        from faster_whisper.audio import decode_audio
    except ImportError as error:
        raise PreprocessUnavailable(f"소리를 고를 부품이 없습니다: {error}") from error

    try:
        소리 = decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
    except Exception as error:
        raise PreprocessUnavailable(f"음원을 읽지 못했습니다: {error}") from error

    소리 = np.asarray(소리, dtype=np.float32)
    분 = len(소리) / SAMPLE_RATE / 60
    if 분 > MAX_MINUTES:
        raise PreprocessUnavailable(
            f"{분:.0f}분짜리는 통째로 올리기에 너무 큽니다. 원본 그대로 넣습니다."
        )

    소리 = _highpass(소리)
    잰것 = measure(소리)
    배율 = 잰것["gain"]
    if target_rms != TARGET_RMS and 잰것["rms"] > 1e-6:
        배율 = min(max(target_rms / 잰것["rms"], MIN_GAIN), MAX_GAIN)

    소리 = 소리 * np.float32(배율)
    # 찢어지지 않게 누른다. 넘치는 부분만 부드럽게 깎는다
    소리 = np.tanh(소리 * np.float32(1.2)).astype(np.float32) * np.float32(0.95)

    잰것["applied_gain"] = float(배율)
    잰것["minutes"] = 분
    return 소리, 잰것
