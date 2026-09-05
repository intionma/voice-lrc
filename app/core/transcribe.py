"""음원에서 일본어를 받아적는다.

`scripts/transcribe.py`의 설정을 그대로 물려받았다. 그 값들은 실제 작품을 돌려
보며 맞춘 것이라 손대지 않는다.

- `condition_on_previous_text=False` — 앞 문맥을 물리면 무음 구간에서 같은 문장을
  무한히 반복 생성한다
- VAD 켜기 — 없는 말을 지어내는 것을 줄인다
- 비언어음 유지 — 무음 판정을 짧게 하고 `no_speech_threshold`를 높여서, 신음과
  숨소리가 "말이 아님"으로 버려지는 것을 막는다. 대신 환각이 조금 늘어난다

모델을 부르는 것 말고는 아무것도 하지 않는다. 합치기·번역·자막은 각자 따로 있다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from app.core import gpu

BEAM_SIZE = 5

# 무음이 긴 작품에서 같은 문장이 반복 생성되는 것을 줄인다
VAD_MIN_SILENCE_MS = 500

# 비언어음을 살리는 모드
NONVERBAL_VAD_MIN_SILENCE_MS = 250
NONVERBAL_NO_SPEECH_THRESHOLD = 0.85

# VAD가 "말이 아님"으로 판정하는 기준. 기본값 0.5는 속삭임과 숨소리를 버린다.
# VAD가 버린 구간은 모델이 구경도 못 하므로, 대사가 통째로 사라진다.
NONVERBAL_VAD_THRESHOLD = 0.25
VAD_THRESHOLD = 0.4

# 잘린 앞뒤를 되살리는 여유. 말의 첫 글자가 잘려 나가는 것을 막는다
VAD_SPEECH_PAD_MS = 400

# 이보다 짧은 소리는 VAD가 통째로 버린다.
#
# **여기서 한 번 틀렸다.** "기본이 250ms 라 짧은 한 음절이 버려진다" 고 보고
# 100 으로 낮췄는데, faster-whisper 1.2.1 의 실제 기본값은 **0** 이다.
# 낮춘 것이 아니라 오히려 걸러 내는 값을 새로 만든 셈이었다.
#
# 0 으로 되돌린다. 「んっ」 같은 한 음절을 버릴 이유가 없다.
NONVERBAL_MIN_SPEECH_MS = 0
MIN_SPEECH_MS = 0

# whisper는 자신 없는 줄을 조용히 버린다. 헐떡이는 소리는 당연히 자신이 없다.
# None 이면 버리지 않는다.
NONVERBAL_LOG_PROB_THRESHOLD = None
LOG_PROB_THRESHOLD = -1.0

# 같은 글자가 반복되면 환각으로 보고 버리는 기준. 의성어는 원래 반복이 많아
# 기본값 2.4 로는 멀쩡한 신음이 버려진다.
NONVERBAL_COMPRESSION_RATIO_THRESHOLD = 3.4
COMPRESSION_RATIO_THRESHOLD = 2.4

# whisper에 앞 문맥으로 넣어 의성어 표기를 유도하는 예시.
# 이 형태로 받아적으라는 신호이고 결과에는 포함되지 않는다.
NONVERBAL_PRIMER = "んっ、あっ、はぁ…、ふぅ…、ちゅっ、んちゅ、くちゅ、ぺろ、はむ、ん…っ"

AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma"}


# 모델이 그래픽카드에서 차지하는 자리(GB). float16 무게에 계산하면서 쓰는 여유를
# 얹은 값이다. 재어 본 값이 아니라 어림값이고, 모자란 쪽으로 잡지 않았다.
#
# 이 값이 왜 필요한가. 자리가 모자란 채로 모델을 밀어 넣으면 ctranslate2 는
# **파이썬 오류를 내지 않고 프로세스를 통째로 죽인다.** 자취도 안 남는다.
# 미리 재서 안 되겠으면 CPU 로 내려가는 편이 낫다. 느린 것이 죽는 것보다 낫다.
VRAM_NEEDED_GB = {
    "large": 5.0,   # large-v3 / large-v3-turbo / anime-whisper(large 계열)
    "medium": 2.5,
    "small": 1.5,
    "base": 1.0,
    "tiny": 0.8,
}
DEFAULT_VRAM_NEEDED_GB = 5.0


# 낱말 사전(`tokenizer.json`)이 딸려 오지 않는 모델들.
#
# **여기서 며칠을 버렸다.** 받아쓰기를 시작하면 앱이 통째로 사라졌다. 파이썬
# 오류 자취가 하나도 없어서 무엇이 죽였는지 알 수가 없었다. CPU 로 내려서도
# 똑같이 죽는 것을 로그로 보고서야 그래픽카드 문제가 아닌 것이 확실해졌다.
#
# `flyfront/anime-whisper-faster` 는 kotoba-whisper 계열이고 `tokenizer.json`
# 이 없다. 그러면 faster-whisper 가 `openai/whisper-tiny` 의 사전을 대신 쓴다.
# tiny 는 51,865칸, large-v3 계열은 51,866칸이다. **한 칸이 어긋난다.**
#
# 어긋난 채로 `suppress_tokens=[]`(눌러 둔 것을 다 풀기)을 주면, 사전에 없는
# 번호를 꺼내려 하다가 C++ 이 그냥 죽는다. `word_timestamps` 도 사전과 어텐션을
# 맞춰 보는 길이라 같은 이유로 위험하다.
#
# `large-v3` 는 사전이 딸려 와서 **같은 설정으로 수십 번 멀쩡히 돌았다.**
# 그것이 이 둘을 가르는 유일한 차이였다.
사전_없는_모델 = ("anime-whisper", "kotoba")


def 사전이수상한가(model: str) -> bool:
    """이름만 보고 「사전이 없을 수 있는 쪽」인지."""
    이름 = str(model or "").lower()
    return any(조각 in 이름 for 조각 in 사전_없는_모델)


@lru_cache(maxsize=8)
def 사전이_어긋날_수_있나(model: str) -> bool:
    """이 모델의 낱말 사전이 어긋나 있는가.

    **이름만 보면 안 된다.** 한때 사전을 맞춰 주는 도구가 있었지만 없앴다. 맞는
    사전을 물려 줄 수 있게 되었다. 맞춰 놓고도 이름만 보고 계속 막으면,
    이 모델은 `suppress_tokens=[]` 도 `word_timestamps` 도 영영 못 쓴다 —
    **바로 그 둘이 이 모델을 쓰는 까닭인데.**

    실제로 여기서 한 번 헛돌았다. 사전을 맞춰 놓고 견주었는데 낱말 시각을
    켠 판과 끈 판이 소수점까지 똑같이 나왔다. 안 켜진 것이었다.

    그래서 이름이 수상하면 **받아 둔 것을 실제로 세어 본다.** 모르면
    「어긋난다」고 한다(`model_dict.사전이맞나`) — 모르면서 풀어 주면 오류도
    없이 프로세스가 죽는다.

    한 번 센 값을 들고 있는다. 받아쓸 때마다 디스크를 뒤질 까닭이 없다.
    **사전을 고친 뒤에는 앱을 다시 켜야** 새로 센다.
    """
    if not 사전이수상한가(model):
        return False
    from app.core import model_dict

    맞나, _ = model_dict.사전이맞나(model)
    return not 맞나


# 빔 하나를 늘릴 때마다 더 드는 자리. 디코더가 후보를 그만큼 더 들고 있는다.
#
# **재어 본 값이 아니다.** 이 컨테이너에는 그래픽카드가 없다. 다만 안 세면
# 어떻게 되는지는 겪었다 — 「아주 정확하게」(beam=10)를 12GB 카드에서 돌리다
# 앱이 오류 한 줄 없이 통째로 죽었다. 로그에는 「훑기 시작」 다음이 없다.
# 적게 잡아 죽는 것보다 많게 잡아 빔을 낮추는 편이 낫다.
빔당_GB = 0.12

# 낱말 시각을 켜면 정렬용으로 조금 더 든다
낱말시각_GB = 0.3

# 여유. 딱 맞게 잡으면 그 판의 다른 것 하나에 밀려 죽는다
여유_GB = 0.5

# 묶어 넣을 때 한 자리가 더 먹는 값. 30초 창 하나를 인코더에 통째로 얹는 값이다.
#
# **여기도 재어 본 값이 아니다.** 넉넉하게 잡는다 — 적게 잡으면 자리가 모자라
# ctranslate2 가 오류 없이 프로세스를 죽인다. 그 자국을 이미 두 번 봤다.
묶음자리_GB = 0.35

# 이 위로 올려도 잘 안 빨라지고 자리만 먹는다. faster-whisper 자신의 기본값도
# 여기다 — 처리량은 이쯤에서 눕는데 자리는 계속 는다
묶음최대 = 8

# 묶음을 세기 전에 **따로 떼어 두는 자리.**
#
# `vram_needed_gb` 의 `여유_GB` 는 모델이 앉는 자리를 위한 것이라, 그것만
# 믿고 남은 것을 전부 묶음으로 채우면 계획이 카드 끝에 딱 붙는다. 실제로
# 재 보니 어느 크기에서든 여분이 0.3GB 밖에 안 남았다.
#
# 윈도우에서는 화면 합성기와 브라우저가 그만큼을 아무 때나 가져간다. 그때
# ctranslate2 는 오류 없이 프로세스를 죽인다 — 이 앱에서 제일 나쁜 죽음이다.
# 조금 느린 쪽을 고른다.
묶음여분_GB = 1.0


def vram_needed_gb(model: str, beam_size: int = 5,
                   word_timestamps: bool = True) -> float:
    """이 설정으로 올리려면 최소 몇 GB 가 비어 있어야 하는지.

    모르는 이름이면 큰 쪽으로 잡는다. 적게 잡았다가 죽는 것보다, 많게 잡아서
    빔을 낮추거나 CPU 로 내려가는 편이 낫다.

    **빔을 세는 것이 핵심이다.** 예전에는 모델 이름만 보고 5.0GB 라고 했다.
    남은 자리가 5.4GB 여서 검사를 통과했고, beam=10 으로 밀어 넣었다가
    그 자리에서 죽었다.
    """
    이름 = str(model or "").lower()
    바탕 = DEFAULT_VRAM_NEEDED_GB
    for 조각, 값 in VRAM_NEEDED_GB.items():
        if 조각 in 이름:
            바탕 = 값
            break
    # anime-whisper 는 이름에 크기가 안 들어 있다. large 계열이라 바탕이 맞다
    더 = max(0, int(beam_size or 1) - 1) * 빔당_GB
    if word_timestamps:
        더 += 낱말시각_GB
    return round(바탕 + 더 + 여유_GB, 2)


def 묶음크기(남은_GB: float | None, model: str, beam_size: int = 5,
           word_timestamps: bool = True, 최대: int = 묶음최대) -> int:
    """남은 자리로 몇 조각씩 묶어 넣을 수 있는지. 못 묶겠으면 0.

    순차로 넣으면 30초 창을 하나씩 도느라 그래픽카드가 대부분 논다. 카드는
    미지근하고 VRAM 도 모델 크기에서 안 움직이는데 몇 시간씩 걸린다.

    **모델이 앉을 자리를 먼저 빼고, 남은 것으로 센다.** 모델 자리까지 묶음에
    쓰면 올리는 그 순간 죽는다.

    못 재면(`nvidia-smi` 가 없으면) 0 이다. 모르는 채로 밀어 넣지 않는다 —
    이 자리에서 죽으면 파이썬 오류조차 안 남는다.
    """
    if 남은_GB is None:
        return 0
    필요 = vram_needed_gb(model, beam_size, word_timestamps)
    남는것 = float(남은_GB) - 필요 - 묶음여분_GB
    if 남는것 <= 0:
        return 0
    칸 = int(남는것 / 묶음자리_GB)
    칸 = min(칸, max(0, int(최대)))
    # 둘 미만이면 묶는 뜻이 없다. 껍질만 씌우고 똑같이 하나씩 도는 꼴이다
    return 칸 if 칸 >= 2 else 0


class NoSpeech(RuntimeError):
    """받아적을 말이 하나도 없었다.

    무음 트랙이나 효과음만 있는 트랙에서 나온다. **고장이 아니다.**
    빨간 '실패' 로 보이면 사용자가 원인을 찾느라 시간을 버린다.
    """


@dataclass
class TranscribeOptions:
    model: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    keep_nonverbal: bool = True
    initial_prompt: str | None = None
    # 단어 시간을 켜면 조금 느려지지만 자막이 밀리지 않는다
    word_timestamps: bool = True
    vad_filter: bool = True
    # 얼마나 넓게 찾는가. 크면 정확하고 느리다
    beam_size: int = BEAM_SIZE
    # 같은 말 되풀이를 막는다. anime-whisper 가 이 버릇이 있다
    no_repeat_ngram_size: int = 0
    repetition_penalty: float = 1.0
    # 앞 문맥 예시를 넣는가. anime-whisper 는 넣으면 오히려 환각이 는다
    use_primer: bool = True
    # 이 길이 넘게 조용하면 그 자리를 건너뛴다. 무음에서 지어내는 것을 막는다.
    # 단어 시각이 켜져 있어야 동작한다
    hallucination_silence_threshold: float | None = None
    # **한 번에 몇 조각씩 넣는가.** 0·1 이면 안 묶고 하나씩 넣는다.
    #
    # 순차로 넣으면 30초 창을 하나씩 도느라 그래픽카드가 대부분 논다. 카드는
    # 뜨겁지도 않고 VRAM 도 모델 크기(4~5GB)에서 안 움직이는데 몇 시간씩
    # 걸린다 — 「느린데 VRAM 은 적게 쓴다」 는 말이 이 모양이다.
    # 묶어서 넣으면 그만큼 자리를 더 먹는 대신 훨씬 빨리 끝난다.
    batch_size: int = 0

    def to_whisper(self) -> dict[str, Any]:
        살림 = self.keep_nonverbal
        # 남의 사전을 빌려 쓰는 모델에는 사전을 건드리는 값을 주지 않는다.
        # 주면 C++ 이 오류도 없이 프로세스를 죽인다. 위의 `사전_없는_모델` 을 보라
        빌린사전 = 사전이_어긋날_수_있나(self.model)
        options: dict[str, Any] = {
            "language": "ja",
            "beam_size": self.beam_size,
            "vad_filter": self.vad_filter,
            "vad_parameters": {
                "min_silence_duration_ms": (
                    NONVERBAL_VAD_MIN_SILENCE_MS if 살림 else VAD_MIN_SILENCE_MS
                ),
                # 기본 0.5는 속삭임을 통째로 버린다. 낮춰야 대사가 살아남는다
                "threshold": NONVERBAL_VAD_THRESHOLD if 살림 else VAD_THRESHOLD,
                "speech_pad_ms": VAD_SPEECH_PAD_MS,
                # 짧은 한 음절이 threshold 와 무관하게 버려지는 것을 막는다
                "min_speech_duration_ms": (
                    NONVERBAL_MIN_SPEECH_MS if 살림 else MIN_SPEECH_MS
                ),
            },
            # 기준을 높이면 신음처럼 말이 아닌 발성이 버려지지 않는다
            "no_speech_threshold": NONVERBAL_NO_SPEECH_THRESHOLD if 살림 else 0.6,
            # 자신 없다고 통째로 버리는 것을 막는다
            "log_prob_threshold": (
                NONVERBAL_LOG_PROB_THRESHOLD if 살림 else LOG_PROB_THRESHOLD
            ),
            # 의성어는 원래 반복이 많아 환각으로 오해받는다
            "compression_ratio_threshold": (
                NONVERBAL_COMPRESSION_RATIO_THRESHOLD if 살림 else COMPRESSION_RATIO_THRESHOLD
            ),
            "initial_prompt": (
                self.initial_prompt
                or (NONVERBAL_PRIMER if (살림 and self.use_primer) else None)
            ),
            # 앞 문맥을 물리면 무음 구간에서 같은 대사를 반복 생성한다
            "condition_on_previous_text": False,
            # 기본값 [-1] 은 "말이 아닌 것" 토큰을 통째로 눌러 버린다.
            # ♪ 같은 기호로 적히는 소리가 우리에게는 필요한 것이라 풀어 준다.
            # 또렷한 대사만 받아적을 때는 눌러 두는 편이 깔끔하다
            "suppress_tokens": [] if (살림 and not 빌린사전) else [-1],
            # 단어 단위로 맞춰야 긴 파일에서 자막이 밀리지 않는다.
            # 사전이 어긋난 모델에서는 이것이 프로세스를 죽인다. 자막이 조금
            # 헐렁해질 뿐이고, `_tight_bounds` 가 문장 시각으로 알아서 떨어진다
            "word_timestamps": self.word_timestamps and not 빌린사전,
        }
        # 0 이면 넘기지 않는다. 옛 판에는 이 인자가 없다
        if self.no_repeat_ngram_size:
            options["no_repeat_ngram_size"] = self.no_repeat_ngram_size
        if self.repetition_penalty and self.repetition_penalty != 1.0:
            options["repetition_penalty"] = self.repetition_penalty
        # 단어 시각이 있어야 동작한다. 사전이 어긋난 모델에서는 위에서 껐으므로
        # `self.word_timestamps` 가 아니라 **실제로 들어간 값**을 봐야 한다
        if self.hallucination_silence_threshold and options["word_timestamps"]:
            options["hallucination_silence_threshold"] = self.hallucination_silence_threshold
        return options

    def for_cpu(self) -> "TranscribeOptions":
        """GPU를 못 쓸 때 쓸 설정. 모델도 한 단계 낮춘다."""
        return TranscribeOptions(
            model="medium" if self.model.startswith("large") else self.model,
            device="cpu",
            compute_type="int8",
            keep_nonverbal=self.keep_nonverbal,
            initial_prompt=self.initial_prompt,
            word_timestamps=self.word_timestamps,
            vad_filter=self.vad_filter,
            beam_size=self.beam_size,
            no_repeat_ngram_size=self.no_repeat_ngram_size,
            repetition_penalty=self.repetition_penalty,
            use_primer=self.use_primer,
        )

    def for_retry(self) -> "TranscribeOptions":
        """망가진 구간을 다시 볼 때 쓸 설정.

        같은 설정으로 다시 보면 같은 헛소리가 나온다. 지어내는 쪽을 조여서 본다.
        비언어음을 살리는 설정이 헛소리를 부르는 쪽이라 그것부터 끈다.
        """
        return TranscribeOptions(
            model=self.model,
            device=self.device,
            compute_type=self.compute_type,
            keep_nonverbal=False,      # 지어내는 빈도를 낮춘다
            initial_prompt=None,       # 의성어 예시가 폭주를 부른다
            word_timestamps=self.word_timestamps,
            vad_filter=True,
            beam_size=self.beam_size,
            no_repeat_ngram_size=self.no_repeat_ngram_size,
            repetition_penalty=self.repetition_penalty,
            use_primer=False,
            # 무음에서 지어내는 것을 막는다. 망가진 구간이 대개 그렇게 생긴다
            hallucination_silence_threshold=2.0,
            # **여기는 안 묶는다.** 묶어 넣는 길은 `hallucination_silence_threshold`
            # 를 안 받아서 말없이 버린다 — 지어내는 것을 조이려고 다시 보는
            # 자리인데 그 조임쇠가 빠진다. 망가진 구간은 몇 초짜리라 묶어도
            # 별로 안 빨라진다. 느린 쪽이 아니라 맞는 쪽을 고른다
        )

    def for_rescan(self) -> "TranscribeOptions":
        """빈 구간을 다시 훑을 때 쓸 설정.

        VAD를 끈다. 애초에 VAD가 버려서 비어 있는 것일 수 있는데, 같은 VAD로
        다시 훑으면 또 버린다.

        **묶음도 같이 꺼진다**(`batch_size` 를 안 물려준다). 묶어 넣는 길은
        VAD 가 나눠 준 조각을 묶는 것이라, VAD 를 끄면 묶을 것이 없다.
        여기는 몇 초짜리 빈 구간만 보는 곳이라 느려도 티가 안 난다.
        """
        return TranscribeOptions(
            model=self.model,
            device=self.device,
            compute_type=self.compute_type,
            keep_nonverbal=self.keep_nonverbal,
            initial_prompt=self.initial_prompt,
            word_timestamps=self.word_timestamps,
            vad_filter=False,
            beam_size=self.beam_size,
            no_repeat_ngram_size=self.no_repeat_ngram_size,
            repetition_penalty=self.repetition_penalty,
            use_primer=self.use_primer,
        )


@dataclass
class TranscribeProgress:
    audio: Path
    position_sec: float
    duration_sec: float
    segment_count: int

    @property
    def ratio(self) -> float:
        return min(1.0, self.position_sec / self.duration_sec) if self.duration_sec else 0.0


@dataclass
class Transcript:
    audio: Path
    duration_sec: float
    model: str
    keep_nonverbal: bool
    segments: list[dict[str, Any]] = field(default_factory=list)
    # 사용자가 도중에 멈췄는가. 그러면 이것은 반쪽짜리라 담아 두면 안 된다
    stopped: bool = False
    # VAD 를 통과한 길이. 전체 길이와 견주면 VAD 가 얼마나 잘라 냈는지 보인다.
    # "2시간 14분 중 1시간 48분만 모델에 갔다" 는 한 줄이 추측을 숫자로 바꾼다
    duration_after_vad: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.audio.name,
            "source_path": str(self.audio),
            "duration_sec": round(self.duration_sec, 3),
            "model": self.model,
            "language": "ja",
            "keep_nonverbal": self.keep_nonverbal,
            "segment_count": len(self.segments),
            "segments": self.segments,
        }


class GpuUnavailable(RuntimeError):
    """GPU 라이브러리를 못 불러왔다. CPU로 다시 시도할 수 있다."""


def load_model(options: TranscribeOptions):
    """whisper 모델을 올린다. import 전에 CUDA DLL을 먼저 등록해야 한다."""
    gpu.register()
    from faster_whisper import WhisperModel

    try:
        return WhisperModel(options.model, device=options.device, compute_type=options.compute_type)
    except Exception as error:
        if options.device == "cuda" and gpu.looks_like_cuda_problem(str(error)):
            raise GpuUnavailable(str(error)) from error
        raise


def 묶어넣기가_되나() -> bool:
    """이 판의 faster-whisper 가 묶어 넣기를 아는가.

    `BatchedInferencePipeline` 은 faster-whisper 1.1 부터다. 우리가 적어 둔
    요구는 `>=1.0.3` 이라 그 아래를 쓰는 사람이 있을 수 있다. **없으면 없는
    대로 돌아야 한다** — 여기서 터지면 받아쓰기를 아예 못 한다.
    """
    try:
        from faster_whisper import BatchedInferencePipeline  # noqa: F401
    except Exception:
        return False
    return True


@lru_cache(maxsize=4)
def _묶음이_받는_인자() -> frozenset[str]:
    """묶어 넣는 길이 실제로 받아 주는 인자 이름들.

    순차로 넣을 때 쓰던 인자를 그대로 넘기면 판에 따라 `TypeError` 가 난다
    (`condition_on_previous_text` 처럼 묶음에서는 뜻이 없는 것이 있다).
    **판마다 다르므로 물어본다.** 못 물어보면 빈 것을 돌려주고, 부르는 쪽이
    묶기를 포기한다.
    """
    try:
        import inspect

        from faster_whisper import BatchedInferencePipeline

        재본것 = inspect.signature(BatchedInferencePipeline.transcribe).parameters
        return frozenset(재본것)
    except Exception:
        return frozenset()


def _묶어서(model, options: TranscribeOptions):
    """묶어 넣는 껍질을 씌운다. 못 씌우면 `None`.

    껍질은 모델을 붙잡고만 있어서 만드는 값이 싸다. 그래서 파일마다 새로
    만들어도 된다 — 모델 자체는 `_ensure_model` 이 한 번만 올린다.
    """
    if int(options.batch_size or 0) < 2:
        return None
    # 묶는 것은 VAD 가 나눠 준 조각이다. VAD 가 꺼져 있으면 묶을 것이 없다
    if not options.vad_filter:
        return None
    if not 묶어넣기가_되나():
        return None
    try:
        from faster_whisper import BatchedInferencePipeline

        return BatchedInferencePipeline(model=model)
    except Exception:
        return None


def transcribe(
    model,
    audio: Path,
    options: TranscribeOptions,
    *,
    on_progress: Callable[[TranscribeProgress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    audio_data: Any = None,
) -> Transcript:
    """파일 하나를 받아적는다. 원본은 읽기만 한다.

    `audio_data` 를 주면 그것을 넣는다. 소리를 키워서 넣을 때 쓴다. `audio` 는
    그때도 그대로 받는다 — 결과에 어느 파일인지 적어야 하기 때문이다.
    """
    넣을것 = str(audio) if audio_data is None else audio_data

    # **묶어서 넣어 본다.** 되면 몇 배 빠르다. 안 되면 조용히 하나씩 넣는다 —
    # 여기서 터지면 받아쓰기를 통째로 못 하므로, 어떤 실패도 순차로 되돌아간다
    #
    # **멈추기와 진행 알림이 성글어진다.** `_모으기` 는 조각이 나올 때마다
    # `should_stop` 을 보는데, 묶어 넣으면 조각이 한 묶음씩 몰려 나온다.
    # 여덟 칸이면 여덟 조각마다 한 번 보는 셈이라, 「멈추기」 를 눌러도 그
    # 묶음이 끝나야 선다. 몇 초 수준이라 여기서는 그대로 둔다 — 예전에는
    # 아예 못 멈췄고, 그때 고친 것은 **묶음 사이에서도 선다**는 점이다.
    # 이 컨테이너에는 faster-whisper 가 없어 실제 간격은 못 쟀다.
    묶음 = _묶어서(model, options)
    if 묶음 is not None:
        받는것 = _묶음이_받는_인자()
        인자 = {이름: 값 for 이름, 값 in options.to_whisper().items() if 이름 in 받는것}
        if "batch_size" in 받는것:
            인자["batch_size"] = int(options.batch_size)
        try:
            segments, info = 묶음.transcribe(넣을것, **인자)
            return _모으기(audio, segments, info, options,
                         on_progress=on_progress, should_stop=should_stop)
        except Exception:
            # 자리가 모자라거나 이 판이 안 받는 조합이다. 하나씩 넣는 길로 간다
            묶음 = None

    try:
        segments, info = model.transcribe(넣을것, **options.to_whisper())
    except (TypeError, ValueError) as error:
        # 판에 따라 없는 인자가 있다. 하나씩 빼면서 다시 해 본다.
        # 여기서 포기하면 받아쓰기 자체를 못 한다
        빼볼것 = ["no_repeat_ngram_size", "repetition_penalty", "word_timestamps"]
        인자 = options.to_whisper()
        마지막 = error
        for 이름 in 빼볼것:
            if 이름 not in 인자:
                continue
            인자.pop(이름)
            try:
                segments, info = model.transcribe(넣을것, **인자)
                break
            except (TypeError, ValueError) as 또:
                마지막 = 또
        else:
            raise 마지막

    return _모으기(audio, segments, info, options,
                 on_progress=on_progress, should_stop=should_stop)


def _모으기(
    audio: Path,
    segments: Any,
    info: Any,
    options: TranscribeOptions,
    *,
    on_progress: Callable[[TranscribeProgress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Transcript:
    """모델이 뱉는 조각을 받아 `Transcript` 로 모은다.

    묶어 넣든 하나씩 넣든 여기부터는 똑같다. 갈래마다 따로 두면 한쪽만 고치고
    다른 쪽은 놔두는 일이 생긴다 — 멈추기와 진행 알림이 그런 곳이다.
    """
    result = Transcript(
        audio=audio,
        duration_sec=float(info.duration),
        model=options.model,
        keep_nonverbal=options.keep_nonverbal,
        # 판에 따라 없을 수 있다. 없으면 0 으로 두고 넘어간다
        duration_after_vad=float(getattr(info, "duration_after_vad", 0.0) or 0.0),
    )

    멈춤 = False
    for segment in segments:
        if should_stop and should_stop():
            멈춤 = True
            break
        text = segment.text.strip()
        if not text:
            continue
        start, end = _tight_bounds(segment)
        result.segments.append(
            {
                "index": len(result.segments) + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "ja": text,
                "avg_logprob": round(segment.avg_logprob, 4),
                "no_speech_prob": round(segment.no_speech_prob, 4),
            }
        )
        if on_progress:
            on_progress(
                TranscribeProgress(
                    audio=audio,
                    position_sec=segment.end,
                    duration_sec=result.duration_sec,
                    segment_count=len(result.segments),
                )
            )

    if 멈춤:
        # 도중에 멈춘 것은 완성된 결과가 아니다. 부르는 쪽이 담아 두지 않게 알린다
        result.stopped = True
        return result

    if not result.segments:
        # 효과음만 있는 트랙, 숨소리만 있는 트랙은 흔하다. 이것은 고장이 아니다.
        # 부르는 쪽이 '실패'(빨강) 가 아니라 '건너뜀' 으로 다룰 수 있게 따로 알린다
        raise NoSpeech("받아적을 말이 없습니다. 효과음만 있는 트랙일 수 있습니다.")

    return result


def _tight_bounds(segment: Any) -> tuple[float, float]:
    """단어 시간이 있으면 그것으로 앞뒤를 좁힌다.

    문장 단위 시간은 앞뒤로 넉넉하게 잡혀 나온다. 그래서 말이 끝났는데도 자막이
    한참 남아 있고, 긴 파일에서는 조금씩 밀린다. 첫 단어와 마지막 단어의 시각을
    쓰면 실제로 말한 구간에 붙는다.
    """
    words = getattr(segment, "words", None)
    if not words:
        return float(segment.start), float(segment.end)

    starts = [float(w.start) for w in words if getattr(w, "start", None) is not None]
    ends = [float(w.end) for w in words if getattr(w, "end", None) is not None]
    if not starts or not ends:
        return float(segment.start), float(segment.end)

    start, end = min(starts), max(ends)
    if end <= start:
        return float(segment.start), float(segment.end)
    return start, end


def find_gaps(
    segments: list[dict[str, Any]],
    duration_sec: float,
    *,
    min_gap_sec: float = 8.0,
    edge_sec: float = 1.0,
) -> list[tuple[float, float]]:
    """말이 하나도 안 잡힌 긴 구간을 찾는다.

    VAD가 속삭임을 통째로 버리면 그 구간이 비어 있게 된다. 진짜 조용한 것인지
    버려진 것인지는 다시 훑어 봐야 안다.
    """
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for segment in segments:
        start = float(segment["start"])
        if start - cursor >= min_gap_sec:
            gaps.append((max(0.0, cursor - edge_sec), start + edge_sec))
        cursor = max(cursor, float(segment["end"]))

    if duration_sec - cursor >= min_gap_sec:
        gaps.append((max(0.0, cursor - edge_sec), duration_sec))
    return gaps


def _같은자리(start: float, end: float, 있는것: dict[str, Any], 틈: float) -> bool:
    """이미 있는 줄과 같은 말을 하고 있는가.

    예전에는 **시작 시각이 그 줄 안에 들어오는지만** 봤다. 그래서 다시 훑어
    찾은 줄이 빈 구간에서 시작해 **뒤에 있는 줄을 삼켜 버리는 경우**를 놓쳤다.

        있던 줄            [40.0 ~ 45.0]
        다시 훑어 찾은 것  [30.0 ~ 41.0]      ← 시작은 어디에도 안 걸린다

    다시 훑는 창은 앞뒤로 1초씩 넉넉히 잡으므로 이런 것이 실제로 나온다.
    그러면 40~41초 사이 말이 자막에 두 번 들어간다.
    """
    있는시작 = float(있는것["start"])
    있는끝 = float(있는것["end"])
    if 있는시작 - 틈 <= start <= 있는끝 + 틈:
        return True
    # 시작이 안 걸려도 실제로 겹쳐 있으면 같은 말이다.
    # 살짝 닿기만 한 것은 그냥 이어지는 다른 말일 수 있으니 틈만큼은 봐준다
    return min(end, 있는끝) - max(start, 있는시작) > 틈


# 같은 말을 두 번 넣지 않으려고 보는 값들.
#
# 시간만 봐서는 **못 거른다.** 두 모델이 파일 전체를 각각 받아쓰면 문장을
# 끊는 자리가 서로 다르다. 앞 침묵까지 물고 시작하거나 두 마디를 한 줄로
# 붙이면, 같은 말인데 시간이 하나도 안 겹친다.
#
#     첫 모델    52.0 ~ 56.0  だめ、そこは
#     둘째 모델  56.6 ~ 61.0  だめ、そこはだめだって   ← 겹침 0초
#
# 그래서 **바로 옆 줄과 글자를 견준다.** 옆 줄로만 좁히는 까닭은, 같은 대사가
# 작품 안에서 진짜로 여러 번 나오기 때문이다. 경계가 밀려 생긴 되풀이는 늘
# 붙어 있고, 진짜 되풀이는 사이에 다른 줄이 있거나 멀리 떨어져 있다.
겹말틈초 = 2.5

# 이보다 짧은 글은 견주지 않는다. 「あぁ」 같은 것은 몇 초 사이에 진짜로 두 번
# 나온다. 애매하면 **남긴다** — 빠뜨리는 것이 겹치는 것보다 나쁘다
겹말최소글자 = 4

# 글자가 이만큼 닮으면 같은 말로 본다
겹말닮음 = 0.8

# NFKC 가 「…」 를 「...」 로 바꿔 놓으므로 **아스키 마침표도 넣어야 한다.**
# 안 넣으면 「はぁ…もう」 가 「はぁ...もう」 로 남아 견주기가 어긋난다
_버릴글자 = re.compile(r"[\s、。，．・…‥「」『』（）()！？!?~〜ー―—–\-\'\".,;:]+")


def 글다듬기(글: Any) -> str:
    """견주기 전에 문장 부호와 사이 공백을 턴다.

    모델마다 「だめ、そこは」 와 「ダメそこは」 처럼 부호를 다르게 찍는다.
    부호를 남겨 두면 같은 말이 다른 말로 보인다.
    """
    return _버릴글자.sub("", unicodedata.normalize("NFKC", str(글 or "")))


def 같은말인가(가: Any, 나: Any) -> bool:
    """두 글이 같은 말인가. **애매하면 아니라고 한다.**

    - 다듬은 뒤 똑같으면 같은 말
    - 한쪽이 다른 쪽을 통째로 품고 있으면 같은 말 (경계가 밀려 길어진 경우)
    - 그 밖에는 닮은 정도로 본다

    어느 쪽이든 짧은 글은 견주지 않는다. 신음은 진짜로 되풀이된다.
    """
    가, 나 = 글다듬기(가), 글다듬기(나)
    if not 가 or not 나:
        return False
    짧은, 긴 = sorted((가, 나), key=len)
    if len(짧은) < 겹말최소글자:
        return False
    if 가 == 나 or 짧은 in 긴:
        return True
    return SequenceMatcher(None, 가, 나).ratio() >= 겹말닮음


def _붙은이웃(start: float, end: float, 있는것들: list[dict[str, Any]], 틈: float):
    """시간으로 **바로 앞·바로 뒤** 줄. 틈보다 멀면 이웃이 아니다."""
    앞 = 뒤 = None
    for 것 in 있는것들:
        것시작 = float(것["start"])
        것끝 = float(것["end"])
        if 것끝 <= start:
            if start - 것끝 <= 틈 and (앞 is None or 것끝 > float(앞["end"])):
                앞 = 것
        elif 것시작 >= end:
            if 것시작 - end <= 틈 and (뒤 is None or 것시작 < float(뒤["start"])):
                뒤 = 것
    return 앞, 뒤


def 줄의글(것: dict[str, Any]) -> str:
    """받아쓴 줄에서 일본어를 꺼낸다.

    **여기서 한 번 헛돌 뻔했다.** 진짜 세그먼트는 `ja` 로 담기는데 `text` 만
    보게 짜 놓으면, 시험은 통과하면서 앱에서는 아무것도 안 거른다.
    """
    return str(것.get("ja") or 것.get("text") or "")


def _이웃과같은말(후보: dict[str, Any], 있는것들: list[dict[str, Any]], 틈: float) -> bool:
    앞, 뒤 = _붙은이웃(float(후보["start"]), float(후보["end"]), 있는것들, 틈)
    글 = 줄의글(후보)
    return any(같은말인가(글, 줄의글(것)) for 것 in (앞, 뒤) if 것 is not None)


def merge_rescanned(
    segments: list[dict[str, Any]],
    found: list[dict[str, Any]],
    *,
    min_distance_sec: float = 0.5,
    겹말틈: float = 겹말틈초,
) -> list[dict[str, Any]]:
    """다시 훑어서 찾은 대사를 원래 결과에 끼워 넣는다.

    두 가지로 거른다. **둘 다 버리는 쪽으로만 움직인다** — 빠뜨리는 것이
    겹치는 것보다 나쁘므로, 애매하면 남긴다.

    1. 시간이 겹치면 버린다
    2. 시간은 안 겹쳐도 **바로 옆 줄과 같은 말이면** 버린다

    2번이 없으면 두 모델이 파일 전체를 각각 받아쓸 때 같은 대사가 두 번
    들어간다. 문장을 끊는 자리가 모델마다 달라서 시간이 안 겹친다.
    """
    merged = list(segments)
    for candidate in found:
        start = float(candidate["start"])
        end = float(candidate["end"])
        if any(_같은자리(start, end, s, min_distance_sec) for s in merged):
            continue
        if _이웃과같은말(candidate, merged, 겹말틈):
            continue
        merged.append(candidate)

    merged.sort(key=lambda s: float(s["start"]))
    for position, segment in enumerate(merged, start=1):
        segment["index"] = position
    return merged


def _to_absolute(
    found: list[dict[str, Any]], window_start: float, window_end: float
) -> list[dict[str, Any]]:
    """다시 훑은 결과의 시각을 원본 기준으로 맞춘다.

    구간만 잘라 훑으면 시각이 그 구간 기준으로 0부터 나올 수 있다. 판별은
    간단하다. 창이 0보다 뒤에서 시작하는데 결과가 전부 창보다 앞이면 상대 시각이다.
    """
    if not found or window_start <= 0:
        return found

    if all(float(item["start"]) < window_start for item in found):
        for item in found:
            item["start"] = round(float(item["start"]) + window_start, 3)
            item["end"] = round(float(item["end"]) + window_start, 3)

    # 창 밖으로 삐져나온 것은 창 안으로 붙인다
    for item in found:
        item["start"] = round(min(max(float(item["start"]), window_start), window_end), 3)
        item["end"] = round(min(max(float(item["end"]), item["start"]), window_end), 3)
    return [item for item in found if item["end"] > item["start"]]


def rescan_gaps(
    model,
    audio: Path,
    gaps: list[tuple[float, float]],
    options: TranscribeOptions,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    retune: bool = True,
) -> list[dict[str, Any]]:
    """구간을 골라 다시 훑는다.

    기본은 빈 구간용이라 VAD를 끈다. 비어 있는 이유가 VAD가 버려서일 수 있는데,
    같은 VAD로 다시 훑으면 또 버리기 때문이다.

    `retune=False` 면 넘어온 설정을 그대로 쓴다. 망가진 구간을 다시 볼 때는
    반대로 조여야 하므로, 여기서 다시 손대면 부르는 쪽이 정한 것이 지워진다.
    """
    rescan_options = options.for_rescan() if retune else options
    whisper_args = rescan_options.to_whisper()
    found: list[dict[str, Any]] = []

    for position, (start, end) in enumerate(gaps, start=1):
        if should_stop and should_stop():
            break
        if on_progress:
            on_progress(position, len(gaps))
        try:
            segments, _ = model.transcribe(
                str(audio) if isinstance(audio, Path) else audio,
                clip_timestamps=[start, end], **whisper_args,
            )
        except TypeError:
            # 구간 지정을 지원하지 않는 판이면 다시 훑기를 건너뛴다.
            # 통째로 다시 돌리는 것은 너무 비싸다.
            return []

        picked = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            seg_start, seg_end = _tight_bounds(segment)
            picked.append(
                {
                    "index": 0,
                    "start": round(seg_start, 3),
                    "end": round(seg_end, 3),
                    "ja": text,
                    "avg_logprob": round(segment.avg_logprob, 4),
                    "no_speech_prob": round(segment.no_speech_prob, 4),
                    "rescanned": True,
                }
            )
        found += _to_absolute(picked, start, end)

    return found


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SUFFIXES


def collect_audio(paths: list[Path]) -> list[Path]:
    """끌어다 놓은 것에서 음원만 추린다. 폴더를 놓으면 안을 훑는다."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            # 폴더 안은 이름 순으로. 그러지 않으면 트랙 차례가 뒤섞인다
            found += sorted(p for p in path.rglob("*") if p.is_file() and is_audio(p))
        elif path.is_file() and is_audio(path):
            # 낱개로 넘어온 것은 **준 차례를 그대로 지킨다.** 부르는 쪽이 정한
            # 차례가 있을 수 있다. 시험이 이 약속을 붙들고 있다
            found.append(path)

    # 같은 파일을 두 번 넣어도 한 번만 처리한다
    seen, unique = set(), []
    for path in found:
        try:
            resolved = path.resolve()
        except OSError:
            # 윈도우에서 경로가 260자를 넘거나 드라이브가 빠지면 여기가 터진다.
            # 여기서 터지면 **파일을 아예 못 넣는다.** 넣는 쪽과 묶는 쪽은 이미
            # 막아 두었는데 이 자리만 빠져 있었다
            resolved = str(path)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique
