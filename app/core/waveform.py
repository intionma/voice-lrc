"""트랙 전체의 소리 크기 곡선을 뽑는다. 검수 화면의 파형 띠가 쓴다.

자막 시각이 맞는지는 **소리가 어디 있는지** 를 봐야 가릴 수 있다. 목록의
시각 숫자만으로는 「1분 넘게 자막이 없는」 자리가 진짜 조용한 것인지 말을
놓친 것인지 알 수 없다. 파형 위에 자막 구간을 얹으면 눈으로 갈린다.

전체를 다 듣지 않고도 보이는 것이 목적이므로 정밀할 필요가 없다.
0.25초에 값 하나 — 3시간짜리도 4만 3천 개, JSON 으로 100KB 남짓이다.

3시간짜리를 처음 읽는 데는 시간이 걸린다. 그래서 한 번 잰 것은
`waveforms/` 아래에 담아 두고, 파일이 그대로면 다시 재지 않는다.
열쇠는 받아쓰기 캐시와 같은 `경로 + 크기 + 수정시각` 이다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core import settings as settings_store

# 값 하나가 맡는 시간. 검수 화면 폭이 1천~2천 픽셀이라 이보다 잘게 재도
# 화면에서 뭉개진다
BUCKET_SEC = 0.25

# 읽을 때 낮춰 잡는 표본 속도. 크기만 보면 되므로 이것으로 충분하고,
# 낮출수록 3시간짜리를 읽는 시간이 준다
READ_RATE = 4000


class WaveUnavailable(RuntimeError):
    """파형을 뽑을 수 없다. 화면은 띠를 안 보여 주면 된다."""


def cache_dir() -> Path:
    return settings_store.config_dir() / "waveforms"


def _cache_path(audio: Path) -> Path:
    # 받아쓰기 캐시와 같은 열쇠. 파일을 바꿔치기했으면 다시 재야 한다
    from app.core.job import cache_key
    return cache_dir() / f"{cache_key(audio)}.json"


def peaks(audio: Path, *, bucket_sec: float = BUCKET_SEC) -> tuple[list[int], float]:
    """`(0~100 크기 목록, 전체 길이 초)`. 값 하나가 `bucket_sec` 를 맡는다."""
    try:
        import av
        import numpy as np
    except ImportError as error:
        raise WaveUnavailable(f"음원을 다룰 부품이 없습니다: {error}") from error

    표본수 = max(1, int(READ_RATE * bucket_sec))
    조각들: list[int] = []
    남은것 = np.empty(0, dtype=np.int16)

    try:
        with av.open(str(audio)) as container:
            if not container.streams.audio:
                raise WaveUnavailable("소리가 들어 있지 않은 파일입니다.")
            stream = container.streams.audio[0]
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=READ_RATE
            )
            for frame in container.decode(stream):
                for 조각 in resampler.resample(frame) or []:
                    소리 = np.frombuffer(
                        bytes(조각.planes[0])[: 조각.samples * 2], dtype=np.int16
                    )
                    남은것 = np.concatenate([남은것, 소리]) if 남은것.size else 소리
                    while 남은것.size >= 표본수:
                        조각들.append(int(np.abs(남은것[:표본수]).max()))
                        남은것 = 남은것[표본수:]
    except WaveUnavailable:
        raise
    except Exception as error:
        raise WaveUnavailable(f"음원을 읽지 못했습니다: {error}") from error

    if 남은것.size:
        조각들.append(int(np.abs(남은것).max()))
    if not 조각들:
        raise WaveUnavailable("소리를 찾지 못했습니다.")

    # 0~100 으로 눕힌다. 제일 큰 소리를 100 으로 — 조용한 속삭임 트랙도
    # 곡선이 보여야 한다
    제일큼 = max(max(조각들), 1)
    눕힌것 = [round(v * 100 / 제일큼) for v in 조각들]
    return 눕힌것, round(len(조각들) * bucket_sec, 2)


def peaks_cached(audio: Path) -> tuple[list[int], float]:
    """담아 둔 것이 있으면 그것을, 없으면 재서 담고 돌려준다."""
    자리 = _cache_path(audio)
    try:
        담긴것 = json.loads(자리.read_text(encoding="utf-8"))
        if (isinstance(담긴것, dict)
                and isinstance(담긴것.get("peaks"), list) and 담긴것.get("duration")):
            return 담긴것["peaks"], float(담긴것["duration"])
    except Exception:
        pass  # 없거나 깨졌으면 다시 잰다. 캐시는 편의일 뿐이다

    난것, 길이 = peaks(audio)
    try:
        자리.parent.mkdir(parents=True, exist_ok=True)
        자리.write_text(
            json.dumps({"peaks": 난것, "duration": 길이}), encoding="utf-8"
        )
    except OSError:
        pass  # 못 담아도 이번 것은 돌려준다
    return 난것, 길이
