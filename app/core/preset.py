"""받아쓰기 강도.

"속삭이는 것도, 대놓고 말하는 것도 못 잡는다" 는 문제에 손댈 수 있는 곳이
네 군데 있다. 강도는 이 넷을 한꺼번에 정하는 손잡이다.

    1. 어떤 모델로 듣는가      ← 이것이 제일 크다
    2. 소리를 고르고 넣는가     ← 작은 소리가 VAD 에 걸리게
    3. 얼마나 넓게 찾는가       ← beam_size
    4. 몇 번 훑는가            ← 빈 곳 다시, 망가진 곳 다시, 두 모델로 두 번

## 모델 이야기

`large-v3` 는 세상 모든 말을 배운 모델이라 **신음·숨소리·속삭임을 "말이 아님"
으로 보고 버리는 버릇이 있다.** 유튜브 자막으로 배워서 그렇다.

`anime-whisper` 는 애니메이션과 게임 음성 5,300시간으로 다시 가르친 것이다.
만든 사람이 대놓고 "다른 모델이 건너뛰는 웃음·비명·한숨·숨소리를 그대로
받아적는다" 고 적어 두었다. 우리가 다루는 것이 정확히 그것이다.

    litagin/anime-whisper — kotoba-whisper-v2.0 기반, 애니/게임 음성 5,300시간
    flyfront/anime-whisper-faster — 위를 faster-whisper 로 쓸 수 있게 바꾼 것

**이 모델에는 `initial_prompt` 를 넣으면 안 된다.** 만든 사람이 "환각이 생기고
성능이 크게 나빠진다" 고 못 박아 두었다. 대신 같은 말을 되풀이하는 버릇이 있어서
`no_repeat_ngram_size` 와 `repetition_penalty` 로 잡는다.

## 여기서 확인하지 못한 것

이 컨테이너에는 GPU 도 음원도 없다. 아래 걸리는 시간은 **재어 본 값이 아니라
어림값**이다. 실제로 얼마나 걸리고 얼마나 나아지는지는 그 PC 에서만 알 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 신음·속삭임에 특화된 모델. **지금은 쓰지 않는다.**
#
# 낱말 사전(`tokenizer.json`)이 없어서 `whisper-tiny` 것을 빌려 쓰는데 한 칸이
# 어긋난다(`transcribe.사전_없는_모델`). 위스퍼는 **시각 토큰이 사전 맨 끝**에
# 있어서, 어긋나면 글자는 멀쩡한데 시각이 통째로 망가진다.
#
# 같은 트랙을 두 강도로 받아써서 재 본 값이다.
#
#                       anime-whisper   large-v3
#     받아쓴 줄               44           68
#     구간 가운데 길이       0.37초       4.72초
#     0.3초 미만 구간         22개          0개
#     트랙을 덮은 비율         4%          59%
#     말이 안 되는 빠르기     34곳          0곳
#
# 「0.21초에 22자」 같은 줄이 절반을 넘었다. 사람이 낼 수 없는 빠르기다.
#
# **줄 수(44 대 68)는 글자를 덜 딴 근거가 아니다.** 구간이 0.1초로 뭉개지면
# `split_long` 이 쪼갤 것이 없어서 줄이 안 늘어난다. 덮은 비율도 망가진
# 길이로 잰 값이라 뜻이 없다. 망가진 것은 **시각뿐**이고, 이 모델의 값어치인
# 「신음·속삭임을 글자로 잘 딴다」 는 그대로일 수 있다.
#
# 사전을 물려 줄 길을 만들어 뒀다 — 점검 탭의 「받아쓰기 모델 사전 맞추기」.
# 맞춘 뒤 다시 받아써서 시각이 멀쩡하면 여기로 되살린다. 그때까지는 강도 표에
# 넣지 않는다(`test_사전이_어긋나는_모델은_강도에_안_넣는다` 가 막는다).
ANIME_MODEL = "flyfront/anime-whisper-faster"

# 세상 모든 말을 배운 기본 모델
BASE_MODEL = "large-v3"

# 같은 모델의 빠른 판. 디코더를 줄여서 대여섯 배 빠르다
TURBO_MODEL = "large-v3-turbo"


@dataclass(frozen=True)
class Preset:
    """강도 하나. 화면에서 고르는 것이 이것이다."""

    id: str
    name: str
    note: str
    # 음원 1시간에 대략 몇 분 걸리는지. 재어 본 값이 아니라 어림값이다
    minutes_per_hour: float

    model: str
    beam_size: int
    # 작은 소리를 키워서 넣는가. VAD 가 속삭임을 버리는 것을 막는다
    normalize: bool
    # 말이 안 잡힌 긴 구간을 VAD 끄고 다시 보는가
    rescan_gaps: bool
    # 소리는 있는데 자막이 없는 곳을 찾는가
    check_coverage: bool
    # 통째로 망가진 구간을 조여서 다시 보는가
    retry_broken: bool
    # 두 번째로 훑을 모델. 비어 있으면 한 번만 훑는다
    second_model: str = ""
    # 앞 문맥 예시를 넣는가. anime-whisper 는 넣으면 오히려 나빠진다
    use_primer: bool = True
    # 같은 말 되풀이를 막는 값. anime-whisper 가 이 버릇이 있다
    no_repeat_ngram_size: int = 0
    repetition_penalty: float = 1.0

    @property
    def two_pass(self) -> bool:
        return bool(self.second_model)

    @property
    def 뒷일_몫(self) -> float:
        """1차로 훑는 일을 1 로 놓았을 때, 그 뒤에 남은 일이 얼마나 되는가.

        남은 시간을 어림하는 데 쓴다. 1차 훑기의 진행률만 보고 「곧 끝납니다」
        라고 말하면, 「극한」에서는 거기서부터 **한 번을 더 훑는다.** 다 됐다고
        해 놓고 그만큼 더 기다리게 만드는 것은 아예 안 알려 주는 것보다 나쁘다.

        재어 본 값이 아니라 어림값이다. 어차피 실제 속도는 그 PC 에서 재서
        쓰므로, 여기서는 「뒤에 얼마나 더 남았나」의 비율만 있으면 된다.
        """
        몫 = 0.0
        if self.rescan_gaps:
            몫 += 0.3      # 빈 곳만 다시 보므로 통째로 훑는 것보다 훨씬 짧다
        if self.retry_broken:
            몫 += 0.1
        if self.check_coverage:
            몫 += 0.05     # VAD 만 돌린다. 모델을 안 부르니 거의 안 걸린다
        if self.two_pass:
            몫 += 1.0      # 다른 모델로 처음부터 한 번 더. 이것이 제일 크다
        return 몫

    def minutes_for(self, duration_sec: float) -> int:
        return max(1, round(duration_sec / 3600 * self.minutes_per_hour))


PRESETS: tuple[Preset, ...] = (
    Preset(
        id="fast",
        name="빠르게",
        note="대충 어떤 말인지만 알면 될 때. 작은 소리는 많이 놓칩니다",
        minutes_per_hour=2.0,
        model=TURBO_MODEL,
        beam_size=1,
        normalize=False,
        rescan_gaps=False,
        check_coverage=False,
        retry_broken=False,
    ),
    Preset(
        id="normal",
        name="보통",
        note="흔한 선택. 또렷하게 말하는 것은 거의 다 잡습니다",
        minutes_per_hour=6.0,
        model=BASE_MODEL,
        beam_size=5,
        normalize=False,
        rescan_gaps=True,
        check_coverage=True,
        retry_broken=True,
    ),
    Preset(
        id="whisper",
        name="속삭임·신음까지 (추천)",
        note=(
            "숨소리·신음·속삭임을 버리지 않게 귀를 열어 둡니다. "
            "또렷한 대사만 필요하면 「보통」이 빠릅니다"
        ),
        minutes_per_hour=7.0,
        # **애니 특화 모델을 쓰다 되돌렸다.** 시각이 통째로 망가져서 자막을
        # 쓸 수가 없었다. 까닭은 `ANIME_MODEL` 에 적어 두었다.
        # 이 강도의 핵심은 모델이 아니라 **VAD 를 낮춰 귀를 열어 두는 것**
        # (`keep_nonverbal`)이고, 그것은 모델과 상관없이 그대로 산다
        model=BASE_MODEL,
        beam_size=5,
        normalize=True,
        rescan_gaps=True,
        check_coverage=True,
        retry_broken=True,
    ),
    Preset(
        id="accurate",
        name="아주 정확하게",
        note="기본 모델을 넓게 훑습니다. 또렷한 대사를 최대한 정확히",
        minutes_per_hour=14.0,
        model=BASE_MODEL,
        beam_size=10,
        normalize=True,
        rescan_gaps=True,
        check_coverage=True,
        retry_broken=True,
    ),
    Preset(
        id="max",
        name="극한 (두 번 훑기)",
        note=(
            "기본 모델로 한 번, 빠른 판으로 또 한 번 훑어서 합칩니다. "
            "한쪽이 놓친 것을 다른 쪽이 잡습니다. 트랙마다 모델을 두 번 "
            "갈아 끼우므로 트랙이 많으면 그만큼 더 걸립니다"
        ),
        minutes_per_hour=22.0,
        model=BASE_MODEL,
        beam_size=10,
        normalize=True,
        rescan_gaps=True,
        check_coverage=True,
        retry_broken=True,
        # 두 번째는 **다른 모델**이어야 뜻이 있다. 같은 것을 두 번 돌리면
        # 같은 것을 놓친다. `large-v3-turbo` 는 디코더가 달라서 놓치는
        # 자리가 다르고, 사전도 제 것을 들고 온다
        second_model=TURBO_MODEL,
    ),
)

DEFAULT = "whisper"

_BY_ID = {p.id: p for p in PRESETS}


def get(preset_id: str) -> Preset:
    """모르는 이름이 오면 기본값을 준다. 설정이 깨져도 돌아가야 한다."""
    return _BY_ID.get(str(preset_id or ""), _BY_ID[DEFAULT])


def to_view() -> list[dict[str, object]]:
    """화면에 그릴 목록."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "note": p.note,
            "minutes_per_hour": p.minutes_per_hour,
            "model": p.model,
            "two_pass": p.two_pass,
        }
        for p in PRESETS
    ]
