"""음원에서 한 구간만 잘라 낸다.

자막이 맞는지는 **들어 봐야** 안다. 글자만 봐서는 받아쓰기가 틀렸는지, 번역이
어색한지, 시각이 밀렸는지 가릴 수 없다.

그런데 2시간짜리 mp3 를 통째로 화면에 넘길 수는 없다. 그래서 그 줄의 앞뒤
몇 초만 잘라서 작은 WAV 로 만들어 넘긴다. 10초짜리는 320KB 라 화면에 그대로
실어 보낼 수 있다.

파일 주소(`file://`)를 화면에 그대로 주지 않는 까닭:

- 창을 띄우는 부품이 `file://` 끼리의 접근을 막는 판이 있다. 그러면 눌러도
  아무 일도 안 일어난다
- 경로에 한글이나 공백이 있으면 또 다른 문제가 생긴다
- 잘라서 주면 2시간짜리를 통째로 읽지 않아도 된다

`av` 는 faster-whisper 가 끌고 오므로 따로 깔 것이 없다.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

# 잘라 낼 때 쓰는 표본 속도. 듣고 판단하는 용도라 이것으로 충분하다
SAMPLE_RATE = 16000

# 앞뒤로 붙이는 여유. 딱 맞춰 자르면 첫 글자가 잘려 나가서 맞는지 알 수 없다
PAD_SEC = 0.6

# 아무리 길어도 이만큼만 준다. 실수로 2시간을 통째로 잘라 넘기지 않게.
#
# 예전에는 30초였다. 그런데 **자막 한 줄이 60초를 넘는 일이 흔하다.** 그러면
# 앞 30초만 들리고 뒤가 잘려서, 「대사에 비해 소리가 너무 짧게 나온다」 가 된다.
# 들어 보는 목적이 그 줄이 맞는지 보는 것인데 뒷부분을 못 들으면 소용이 없다.
#
# 3분이면 16kHz 모노 16비트로 약 5.8MB 다. 화면에 그대로 실어 보낼 만하다.
# 그보다 긴 한 줄은 자막으로서 이미 망가진 것이라 여기서 지킬 일이 아니다.
MAX_SEC = 180.0


class ClipUnavailable(RuntimeError):
    """음원을 잘라 낼 수 없다. 화면은 그냥 듣기를 못 하게 두면 된다."""


def _to_wav(samples: bytes, *, rate: int = SAMPLE_RATE) -> bytes:
    """16비트 모노 PCM 을 WAV 로 감싼다."""
    통 = io.BytesIO()
    with wave.open(통, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples)
    return 통.getvalue()


def wav_seconds(데이터: bytes) -> float:
    """만들어 놓은 WAV 가 몇 초짜리인가. 못 읽으면 0.

    「소리가 짧게 들리고 끝난다」 를 쫓을 때, **앱이 몇 초를 보냈는지**부터
    알아야 한다. 그것을 모르면 자르는 쪽을 고쳐야 하는지 넘기는 쪽을 고쳐야
    하는지 가릴 수 없어서 같은 자리를 여러 번 돈다.
    """
    try:
        with wave.open(io.BytesIO(데이터), "rb") as f:
            빠르기 = f.getframerate() or SAMPLE_RATE
            return round(f.getnframes() / 빠르기, 3)
    except Exception:
        return 0.0


def extract_wav(
    audio: Path,
    start_sec: float,
    end_sec: float,
    *,
    pad_sec: float = PAD_SEC,
    max_sec: float = MAX_SEC,
) -> tuple[bytes, float]:
    """그 구간만 잘라 WAV 로 돌려준다. `(WAV 바이트, 실제 시작 시각)`.

    시작 시각을 함께 주는 까닭은, 앞에 여유를 붙였으므로 화면이 "몇 초부터
    나가는지" 를 알아야 하기 때문이다.
    """
    try:
        import av
    except ImportError as error:
        raise ClipUnavailable(f"음원을 다룰 부품이 없습니다: {error}") from error

    시작 = max(0.0, float(start_sec) - pad_sec)
    끝 = max(시작 + 0.2, float(end_sec) + pad_sec)
    끝 = min(끝, 시작 + max_sec)

    try:
        with av.open(str(audio)) as container:
            if not container.streams.audio:
                raise ClipUnavailable("소리가 들어 있지 않은 파일입니다.")
            stream = container.streams.audio[0]

            # 그 자리로 건너뛴다. 처음부터 읽으면 2시간짜리가 한참 걸린다.
            # 조금 앞에서 시작해야 프레임 경계 때문에 앞이 잘리지 않는다
            try:
                container.seek(
                    int(max(0.0, 시작 - 1.0) * av.time_base), stream=None, any_frame=False
                )
            except Exception:
                pass  # 건너뛰기를 못 하는 형식이면 처음부터 읽는다

            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=SAMPLE_RATE
            )
            모은것 = bytearray()
            시간단위 = stream.time_base
            실제시작: float | None = None

            for frame in container.decode(stream):
                자리 = float(frame.pts * 시간단위) if frame.pts is not None else None
                if 자리 is not None:
                    if 자리 + 0.5 < 시작:
                        continue  # 아직 멀었다
                    if 자리 > 끝:
                        break
                if 실제시작 is None:
                    # 프레임 경계 때문에 요청한 곳보다 조금 앞에서 시작한다.
                    # 화면이 "몇 초부터 나가는지" 를 알아야 하므로 실제 값을 준다
                    실제시작 = 시작 if 자리 is None else 자리
                for 조각 in resampler.resample(frame) or []:
                    모은것 += bytes(조각.planes[0])[: 조각.samples * 2]
    except ClipUnavailable:
        raise
    except Exception as error:
        raise ClipUnavailable(f"음원을 읽지 못했습니다: {error}") from error

    if not 모은것:
        raise ClipUnavailable("그 자리에서 소리를 찾지 못했습니다.")

    # 길이를 넘기지 않게 다시 잘라 맞춘다
    필요 = int((끝 - (실제시작 or 시작)) * SAMPLE_RATE) * 2
    if 0 < 필요 < len(모은것):
        모은것 = 모은것[:필요]

    return _to_wav(bytes(모은것)), round(실제시작 if 실제시작 is not None else 시작, 3)
