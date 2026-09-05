"""번역을 주고받는 부분.

AI에게 1500줄을 한 번에 던지면 몇 줄을 조용히 빠뜨린다. 그래서 300줄씩 나눠서
주고, 받은 결과를 세어 보고, 빠진 번호만 다시 물어본다. 이 과정은 전부 프로그램이
하고 사용자에게는 "몇 번째 / 몇 개"만 보인다.

번호로 맞추기 때문에 AI가 줄 하나를 빠뜨려도 그 줄만 비고 뒤가 밀리지 않는다.
정말 위험한 것은 AI가 번호를 **다시 매기는** 경우라, 그건 따로 잡아낸다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# 한 묶음에 담을 수 있는 최대 줄 수. 이보다 많으면 AI가 빠뜨리기 시작한다
# **트랙 하나에 떼어 주는 번호 칸의 크기.**
#
# 트랙마다 1번부터 세면 세 트랙의 답이 모두 「1, 2, 3…」 이라 구분이 안 된다.
# 칸을 떼면 번호만 보고 어느 트랙 답인지 알 수 있다.
#
# 60분짜리 트랙을 짧은 토막으로 쪼개면 1,000줄을 넘으므로 그보다 넉넉해야
# 한다. 칸을 넘치면 옆 트랙의 답을 제 것으로 받아들이게 된다.
칸크기 = 10_000

BATCH_LINES = 300

# 줄 수가 적어도 글자가 너무 많으면 나눈다
BATCH_CHARS = 5000

# 이보다 적으면 나누지 않는다. 20줄을 두 번 붙여넣게 하는 것은 낭비다
MIN_SPLIT_LINES = 120

# 내 컴퓨터에서 도는 모델로 번역할 때 쓰는 한도. 사람이 붙여넣지 않으므로
# 크게 보내도 손이 더 가지 않는다. 묶음 수가 절반이 되어 왕복이 줄고,
# 모델이 앞뒤 문맥을 두 배로 본다.
#
# 여기에 맞춰 `providers.필요한창()` 이 문맥 창을 넓힌다. 상한이 32,768 인데
# 9,000자면 머리말까지 약 27,000토큰이라 여유가 남는다. 10,000자로 잡았더니
# 상한에 딱 붙어서, 일본어가 예상보다 토큰을 더 먹으면 잘릴 자리였다.
# (`test_로컬_묶음이_창_상한에_들어간다` 가 지킨다)
LOCAL_BATCH_LINES = 600
LOCAL_BATCH_CHARS = 9000

# 경계를 이 범위 안에서 옮겨 대사가 끊기지 않는 자리를 찾는다
SNAP_WINDOW = 15

# 앞 묶음에서 몇 줄을 참고로 넘겨줄지.
#
# **묶음끼리 서로를 몰랐다.** 2시간짜리를 다섯 묶음으로 나누면 다섯 번을
# 처음부터 시작하는 셈이라, 앞에서 「오빠」였던 것이 뒤에서 「형」이 되고
# 존댓말이 경계에서 튀었다. 앞 묶음의 꼬리를 보여 주면 그 자리에서 맞춘다.
#
# 열 줄이면 호칭과 말투를 잡기에 넉넉하고, 프롬프트를 눈에 띄게 늘리지도
# 않는다. 더 붙이면 모델이 그것까지 번역해서 낼 위험만 커진다.
이어줄줄수 = 10

# 붙여넣기 대신 파일로 건네는 편이 나은 크기
FILE_HANDOFF_CHARS = 2000

# AI가 번역 대신 거절문을 뱉었는지 보는 신호
REFUSAL_SIGNS = (
    "죄송",
    "도와드릴 수 없",
    "할 수 없습니다",
    "부적절",
    "선정적",
    "정책",
    "i can't",
    "i cannot",
    "i'm sorry",
    "unable to",
    "against my",
    "申し訳",
)

# 내 컴퓨터에서 도는 모델에게 `system` 맨 앞에 주는 말.
#
# 작은 모델은 「지시」와 「번역할 것」을 구분하지 못한다. 이것을 못 박아 두지
# 않으면 우리 규칙 문장에 번호를 붙여서 되돌려 준다.
LOCAL_ROLE = """너는 일본어→한국어 자막 번역기다.

이 메시지에 적힌 것은 **전부 지시다. 번역할 내용이 아니다.**
번역할 것은 다음 메시지에 「번호<탭>일본어」 형식으로만 온다.
그 줄들만 번역해서 「번호<탭>한국어」로 낸다. 그 밖의 어떤 글자도 내지 마라."""

# 검수할 때의 `system` 맨 앞. `LOCAL_ROLE` 을 그대로 쓰면 안 된다 —
# 「번역할 것은 번호<탭>일본어 형식으로 온다」 고 못 박혀 있어서, 세 칸짜리
# 검수 입력을 받으면 형식이 틀린 줄 알고 전부 다시 번역해 버린다.
LOCAL_REVIEW_ROLE = """너는 일본어→한국어 자막 검수기다.

이 메시지에 적힌 것은 **전부 지시다. 검수할 내용이 아니다.**
검수할 것은 다음 메시지에 「번호<탭>일본어<탭>지금번역」 형식으로만 온다.
그중 **고칠 줄만** 골라 「번호<탭>고친한국어」로 낸다.
고칠 것이 없으면 `없음` 한 마디만 낸다. 그 밖의 어떤 글자도 내지 마라."""

PROMPT_HEADER = """일본어 음성 전사를 한국어 자막으로 번역해줘.

【형식 — 이게 제일 중요함】
- 입력은 "번호<탭>일본어" 형식이다
- 출력도 똑같이 "번호<탭>한국어"로만 낸다
- **번호는 1부터 시작하지 않을 수 있다.** 긴 작품을 나눠서 주기 때문이다.
  받은 번호를 그대로 돌려줘라. 1부터 다시 매기면 자막이 통째로 어긋난다
- 줄을 합치거나 나누지 마라. 입력 줄 수와 출력 줄 수가 정확히 같아야 한다
- 설명이나 머리말은 붙이지 마라. 번역 줄만 낸다
- **결과 전체를 코드블록 하나에 담아라.** 그래야 복사 단추가 생겨서 한 번에
  옮길 수 있다. 여러 덩어리로 쪼개지 말고 하나로 묶어라

【번역 규칙】
- 한국어만 쓴다. 일본어 병기 안 한다
- 화자 이름표는 붙이지 않는다
- 한 줄씩 화면에 뜨는 자막이라 짧고 자연스럽게
- 원문에 있는 것을 그대로 옮긴다. 빼거나 바꾸지 않는다
- 의성어와 비언어음은 한국어 느낌으로 옮긴다
- 음성 인식 오류로 뜻이 안 통하는 줄이 있다. 발음이 비슷한 다른 단어로 잘못
  들은 경우가 많으니, 앞뒤 문맥으로 원래 뜻을 추정해서 자연스럽게 번역해라
"""

REVIEW_HEADER = """이미 번역해 놓은 자막을 검수해라. **고칠 줄만** 내라.

【형식】
- 입력은 "번호<탭>일본어<탭>지금번역" 이다
- 출력은 "번호<탭>고친한국어" 로만 낸다
- **고칠 것이 있는 줄만 낸다.** 괜찮은 줄은 내지 마라
- 고칠 것이 하나도 없으면 `없음` 한 마디만 낸다
- 받은 번호를 그대로 쓴다. 다시 매기지 마라
- 설명은 붙이지 마라
- **결과 전체를 코드블록 하나에 담아라**

【무엇을 고치나 — 이 순서로 본다】
1. **뜻이 틀린 줄.** 원문에 없는 말이 들어갔거나, 있는 말이 빠졌다
2. **호칭이 틀린 줄.** お兄さん·お姉ちゃん·先輩 같은 것을 엉뚱하게 옮겼다
3. **앞뒤와 어긋나는 줄.** 같은 사람을 부르는 말이나 말투가 중간에 바뀌었다
4. **한국어가 어색한 줄.** 뜻은 맞는데 사람이 안 쓰는 말이다
5. **일본어가 남은 줄**

【고치지 않는 것】
- 수위. 원문이 노골적이면 번역도 노골적인 것이 맞다. **순화하지 마라**
- 취향 차이. 뜻이 통하는데 표현이 다를 뿐이면 그냥 둬라. 고칠 줄이
  많을수록 좋은 것이 아니다
"""

# 가려 둔 낱말을 그대로 두라는 대목.
#
# **표가 실제로 들어 있는 묶음에만 붙인다.** 없는데 붙이면 모델이 없는 토막을
# 찾다가 엉뚱한 자리에 `KW01` 을 지어 넣는다.
#
# 무엇을 가렸는지는 **적지 않는다.** 그것을 적으면 가린 뜻이 없다 — 거절당하지
# 않으려고 뺀 낱말을 지시문에 도로 적어 보내는 꼴이다.
#
# 말할 것은 `wordbook.되돌리기` 가 **못 고치는 두 가지**다. 대소문자·공백·
# 조사·`KW1` 은 읽는 쪽이 알아서 견딘다. 못 견디는 것은
#
#   빼먹은 것    아무 소리 없이 그 낱말이 사라진다. 제일 나쁘다
#   지어낸 번호  우리가 준 적 없는 번호는 되돌릴 말이 없다
MASK_RULES = """【KW 토막 — 손대지 마라】
- 원문에 `KW01` `KW02` 처럼 **KW 뒤에 숫자**가 붙은 토막이 섞여 있다
- 번역이 이미 정해진 낱말이라 자리만 잡아 둔 것이다. **번역하지 마라**
- **빼먹지 마라.** 받은 줄에 있으면 낸 줄에도 그대로 있어야 한다
- **없던 번호를 지어내지 마라.** 받은 번호만 쓴다
- 원문에서 있던 그 자리에 그대로 둔다
- 뜻을 짐작해서 딴 말로 바꾸지 마라
- 조사는 한국어에 맞게 붙여도 된다 (`KW01을`, `KW01이`)
"""

RETRY_HEADER = """아까 번역에서 아래 번호들이 빠졌다. 이 번호들만 다시 번역해줘.

- 출력은 "번호<탭>한국어" 형식
- **받은 번호를 그대로 쓴다.** 1부터 다시 매기지 마라. 번호가 띄엄띄엄해도 그대로다
- 설명 없이 번역 줄만
- **결과 전체를 코드블록 하나에 담아라.** 복사 단추로 한 번에 옮기게
"""


def _hms(seconds: float) -> str:
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


@dataclass
class Batch:
    """AI에게 한 번에 건넬 묶음."""

    number: int
    total: int
    segments: list[dict[str, Any]]
    is_retry: bool = False
    # 작품 정보. 성우 이름과 분위기를 알려 주면 번역이 나아진다
    context: str = ""
    # 앞 묶음의 끝부분. `(번호, 일본어, 한국어)`.
    #
    # **참고용이라 번역할 것이 아니다.** 줄 수 계산에도 넣지 않는다. 넣으면
    # 모델이 그것까지 세어서 낼 줄 수를 잘못 잡는다.
    앞선것: list[tuple[int, str, str]] = field(default_factory=list)
    # 검수 묶음인가. 번역하는 것이 아니라 **해 놓은 것을 다시 보는** 것이다
    is_review: bool = False
    # 검수할 때 지금 붙어 있는 번역. `번호 → 한국어`
    옮긴것: dict[int, str] = field(default_factory=dict)

    @property
    def indices(self) -> list[int]:
        return [s["index"] for s in self.segments]

    @property
    def start_sec(self) -> float:
        return float(self.segments[0]["start"]) if self.segments else 0.0

    @property
    def end_sec(self) -> float:
        return float(self.segments[-1]["end"]) if self.segments else 0.0

    @property
    def span(self) -> str:
        """`0:24:30 ~ 0:48:10`. 몇 번째 묶음인지보다 이쪽이 더 와닿는다."""
        return f"{_hms(self.start_sec)} ~ {_hms(self.end_sec)}"

    @property
    def body(self) -> str:
        if self.is_review:
            # 검수는 지금 번역이 무엇인지 봐야 한다. 원문만 주면 그냥 다시
            # 번역해 버려서, 고친 줄과 안 고친 줄을 가릴 수 없다
            return "\n".join(
                f"{s['index']}\t{s['ja']}\t{self.옮긴것.get(s['index'], '')}"
                for s in self.segments
            )
        return "\n".join(f"{s['index']}\t{s['ja']}" for s in self.segments)

    @property
    def 가린말있나(self) -> bool:
        """이 묶음에 가려 둔 낱말이 들어 있나.

        **묶음 알맹이를 보고 정한다.** 밖에서 「가리기를 켰나」를 받아 오면,
        켜 놓고 걸린 낱말이 하나도 없는 묶음까지 지시문이 붙는다.
        """
        from app.core import wordbook

        return bool(wordbook.표들(self.body))

    @property
    def rules(self) -> str:
        """지시문만. 번역할 알맹이는 들어 있지 않다.

        내 컴퓨터에서 도는 모델에는 이것을 `system` 으로 따로 보낸다.
        **7B 급은 지시문과 데이터를 한 덩어리로 주면 구분하지 못한다.**

        실제로 겪었다. 지시문을 번역할 내용으로 알고 「1. 형식 — 이게 제일
        중요함 / 2. みもりあいの, 涼花みなせ / 3. 1번부터 30번까지…」 처럼
        **우리 규칙에 번호를 매겨서** 되돌려 줬다. 번호가 1부터라 읽는 쪽은
        멀쩡한 답으로 알고 담았고, 정작 일본어 대사는 손도 안 댄 채였다.
        """
        맡은일 = LOCAL_REVIEW_ROLE if self.is_review else LOCAL_ROLE
        return "\n\n".join([맡은일, *self._규칙조각()])

    def _규칙조각(self, 작품정보빼기: bool = False) -> list[str]:
        """지시문을 이루는 조각들. 로컬 전용 잔소리(`LOCAL_ROLE`)는 빼고.

        글자를 잘라 내서 빼면 안 된다. `LOCAL_ROLE` 안에도 빈 줄이 있어서,
        「첫 문단만 떼기」 로는 절반만 떨어져 나간다. 실제로 그렇게 틀렸다.
        """
        머리말 = PROMPT_HEADER
        if self.is_review:
            머리말 = REVIEW_HEADER
        elif self.is_retry:
            머리말 = RETRY_HEADER
        조각 = [머리말]
        if self.가린말있나:
            조각.append(MASK_RULES)
        # **여러 작품을 한꺼번에 보낼 때는 여기에 안 넣는다.**
        # 작품 정보에는 호칭이 들어 있는데, 작품이 둘이면 여기에 두 벌이
        # 쌓여서 어느 것이 어느 작품 것인지 알 수가 없다. 그때는 각 작품의
        # 줄 **바로 위**에 붙인다 (`합친묶음글`)
        if self.context and not 작품정보빼기:
            조각.append(f"【작품 정보】\n{self.context}")
        이어받기 = self._이어받기조각()
        if 이어받기:
            조각.append(이어받기)

        번호들 = self.indices
        범위 = (
            f"{번호들[0]}번부터 {번호들[-1]}번까지"
            if 번호들 and not self.is_retry else "아래 적힌 번호들"
        )
        if self.is_review:
            # **여기서 줄 수를 세라고 하면 안 된다.** 검수는 고칠 것만 내는
            # 것이라 낸 줄이 받은 줄보다 적은 것이 정상이다. 세라고 하면
            # 멀쩡한 줄까지 억지로 고쳐서 수를 맞춘다
            조각.append(
                f"【분량】\n{범위}, 모두 {len(번호들)}줄을 검토해라. "
                "고칠 줄만 내라. 다 괜찮으면 `없음` 이라고만 내라."
            )
            return 조각
        조각.append(
            f"【분량】\n{범위}, 모두 {len(번호들)}줄이다. "
            f"낸 줄 수가 {len(번호들)}줄인지 세어 보고 내라."
        )
        return 조각

    def _이어받기조각(self) -> str:
        """앞 묶음을 어떻게 번역했는지 보여 주는 대목. 없으면 빈 글.

        **「참고만 해라, 다시 내지 마라」를 못 박는다.** 이것 없이 앞 줄을
        붙이면 모델이 그 줄까지 답에 담아서, 이미 끝난 번호가 다시 와서
        덮어쓴다. 줄 수도 안 맞는다.
        """
        if not self.앞선것:
            return ""
        줄들 = "\n".join(f"{n}\t{ja}\t→\t{ko}" for n, ja, ko in self.앞선것)
        return (
            "【앞 묶음 끝부분 — 참고만 해라. 절대 다시 내지 마라】\n"
            "바로 앞에서 이렇게 번역했다. 호칭·말투·상황을 여기에 맞춰서 이어라.\n"
            "이 번호들은 이미 끝났다. 답에 넣지 마라.\n"
            f"{줄들}"
        )

    @property
    def data(self) -> str:
        """번역할 알맹이만. `system` 과 짝지어 `user` 로 보낸다."""
        return self.body

    @property
    def prompt(self) -> str:
        """사람이 복사해서 밖의 AI 에 붙여넣는 것. 지시문과 알맹이가 한 덩어리다.

        밖의 AI(클로드·제미나이)는 이 정도로 헷갈리지 않는다. 붙여넣는 쪽은
        한 번에 복사되는 편이 낫다.
        """
        # `LOCAL_ROLE` 은 빼고 준다. 복붙 쪽에는 필요 없는 잔소리다
        규칙 = "\n\n".join(self._규칙조각())
        머리 = "아래가 검수할 것이다." if self.is_review else "아래가 원문이다."
        return f"{규칙}\n\n{머리}\n\n{self.data}"

    @property
    def plain(self) -> str:
        """지시문 없이 **번호와 원문만.**

        구글·파파고 같은 기계 번역기에 넣을 때 쓴다. 지시문을 같이 넣으면
        그것까지 번역해서 돌려주고, 줄 수가 안 맞아 답을 통째로 못 쓴다.

        사용자가 어제 이것을 **손으로** 했다 — 프롬프트를 복사해서 지시문
        부분만 지우고 번역기에 넣었다. 단추 하나면 될 일이었다.

        **가린 표도 푼다.** 가리기는 상용 AI 의 거절을 피하려고 넣은 것인데
        번역기에는 거절이 없다. 표만 남으면 번역기가 `KW01` 을 그대로
        흘려보내거나 엉뚱하게 옮겨서 그 줄을 못 쓴다.
        """
        return "\n".join(
            f"{s['index']}\t{s.get('ja_raw') or s['ja']}" for s in self.segments
        )

    @property
    def prefers_file(self) -> bool:
        """붙여넣기보다 파일로 건네는 편이 편한 크기인지."""
        return len(self.body) >= FILE_HANDOFF_CHARS


@dataclass
class ParseResult:
    """붙여넣은 답을 읽은 결과."""

    translations: dict[int, str] = field(default_factory=dict)
    missing: list[int] = field(default_factory=list)
    unexpected: list[int] = field(default_factory=list)
    renumbered: bool = False
    refused: bool = False
    # 번호는 맞는데 알맹이가 한국어가 아니다. 원문을 그대로 되돌려 준 것이다
    not_korean: bool = False

    @property
    def ok(self) -> bool:
        return not (self.missing or self.renumbered or self.refused or self.not_korean)


def _line_size(segment: dict[str, Any]) -> int:
    return len(str(segment["ja"])) + 8  # 번호와 탭 몫


def _even_counts(total: int, parts: int) -> list[int]:
    """전체를 고르게 나눈다.

    1250줄을 300씩 자르면 300·300·300·300·50이 되어 마지막이 짜투리가 된다.
    250씩 다섯 번이 사용자에게도 AI에게도 낫다.
    """
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def _snap(
    boundary: int,
    items: list[dict[str, Any]],
    taken: set[int],
    *,
    window: int,
) -> int:
    """경계를 근처에서 가장 오래 쉬는 자리로 옮긴다.

    말이 이어지는 한가운데를 자르면 AI가 앞뒤를 못 보고 어색하게 옮긴다.
    잠깐 조용해지는 곳에서 끊으면 각 묶음이 온전한 덩어리가 된다.
    """
    low = max(1, boundary - window)
    high = min(len(items) - 1, boundary + window)
    if low > high:
        return boundary

    best, best_gap = boundary, -1.0
    for candidate in range(low, high + 1):
        if candidate in taken:
            continue
        gap = float(items[candidate]["start"]) - float(items[candidate - 1]["end"])
        # 같은 크기면 원래 자리에 가까운 쪽을 고른다
        if gap > best_gap or (gap == best_gap and abs(candidate - boundary) < abs(best - boundary)):
            best, best_gap = candidate, gap
    return best


def plan_batch_count(
    items: list[dict[str, Any]],
    *,
    batch_lines: int,
    batch_chars: int,
) -> int:
    """몇 번에 나눠 보낼지 정한다.

    줄 수와 글자 수 둘 다 본다. 짧은 대사가 많은 작품과 긴 문장이 적은 작품은
    같은 줄 수라도 분량이 다르다.
    """
    total = len(items)
    by_lines = -(-total // batch_lines)  # 올림
    by_chars = -(-sum(_line_size(s) for s in items) // batch_chars)

    # 짧으면 나누지 않는다. 다만 줄이 적어도 한 줄 한 줄이 길면 나눠야 하고,
    # 한도가 이보다 낮게 잡혀 있으면 한도가 이긴다
    dont_bother = min(MIN_SPLIT_LINES, batch_lines)
    if total <= dont_bother and by_chars <= 1:
        return 1

    return max(1, by_lines, by_chars)


# ── 여러 트랙을 한 프롬프트로 ──────────────────────────
#
# 트랙마다 들어가서 복사하고 붙여넣기를 되풀이하는 것이 제일 힘들다는 말을
# 들었다. 트랙이 넷이면 여덟 번을 오간다.
#
# **번호가 이미 트랙마다 갈라져 있어서** 합쳐 보내도 답이 제자리로 돌아온다
# (`queue.칸시작`). 그래서 합치는 쪽만 만들면 된다.


@dataclass(frozen=True)
class 낱장:
    """합칠 것 하나. 어느 작품 어느 트랙의 어느 묶음인가."""

    묶음: Batch
    트랙이름: str
    작품이름: str
    작품열쇠: str


def 합친묶음글(낱장들: list[낱장]) -> str:
    """여러 트랙의 묶음을 한 프롬프트로 만든다.

    ## 작품 정보를 맨 위에 안 둔다

    작품 정보에는 **호칭이 들어 있다.** 「お兄さん = 오빠」 처럼 그 작품에서만
    맞는 규칙이다. 작품이 둘이면 맨 위에 두 벌이 쌓이는데, 그러면 어느 것이
    어느 작품 것인지 알 수가 없다. 「오빠」 로 옮겨야 할 작품이 「형」 이 되고
    **아무 오류도 안 난다.**

    그래서 각 작품의 줄 **바로 위**에 붙인다. 가까운 것을 본다.

    ## 번호는 한 자리도 안 건드린다

    번호가 곧 주인이다. 여기서 다시 매기면 답이 어디로 갈지 알 수 없어진다.
    """
    if not 낱장들:
        return ""

    # 지시문은 맨 앞 한 번만. 작품 정보는 빼고 가져온다
    첫장 = 낱장들[0].묶음
    맡은일 = LOCAL_REVIEW_ROLE if 첫장.is_review else LOCAL_ROLE
    공통 = [조각 for 조각 in 첫장._규칙조각(작품정보빼기=True)
            if not 조각.startswith("【분량】")]

    총줄 = sum(len(장.묶음.indices) for 장 in 낱장들)
    작품수 = len({장.작품열쇠 for 장 in 낱장들})
    공통.append(
        f"【분량】\n트랙 {len(낱장들)}개, 모두 {총줄}줄이다. "
        f"낸 줄 수가 {총줄}줄인지 세어 보고 내라.\n"
        "**번호를 다시 매기지 마라.** 받은 번호를 그대로 써라. "
        "번호가 어느 트랙 것인지를 가리므로, 1번부터 다시 매기면 "
        "답을 통째로 못 쓴다."
    )
    if 작품수 > 1:
        공통.append(
            f"【작품이 {작품수}개다】\n"
            "작품마다 규칙이 다르다. 각 작품의 규칙은 그 작품 줄 **바로 위**에 "
            "적어 두었다. 다른 작품 규칙을 끌어다 쓰지 마라."
        )

    덩이 = ["\n\n".join([맡은일, *공통]), "아래가 원문이다."]

    지난작품 = None
    for 장 in 낱장들:
        머리 = [f"━━ {장.트랙이름} ━━"]
        # 작품이 바뀔 때만 작품 정보를 다시 적는다. 트랙마다 적으면
        # 같은 말이 네 번 쌓여서 프롬프트만 길어진다
        if 장.작품열쇠 != 지난작품:
            머리[0] = f"━━ {장.작품이름} · {장.트랙이름} ━━"
            if 장.묶음.context:
                머리.append(f"【이 작품 규칙】\n{장.묶음.context}")
            지난작품 = 장.작품열쇠
        덩이.append("\n".join(머리) + "\n" + 장.묶음.data)

    return "\n\n".join(덩이)


def 합친원문글(낱장들: list[낱장]) -> str:
    """지시문 없이 번호와 원문만. 번역기에 넣을 때 쓴다."""
    return "\n\n".join(
        f"━━ {장.트랙이름} ━━\n{장.묶음.plain}" for 장 in 낱장들
    )


def make_batches(
    segments: Iterable[dict[str, Any]],
    *,
    batch_lines: int = BATCH_LINES,
    batch_chars: int = BATCH_CHARS,
    snap: bool = True,
    context: str = "",
) -> list[Batch]:
    """번역할 세그먼트를 묶음으로 나눈다.

    묶음 수는 분량에 따라 달라진다. 5분짜리는 한 번, 2시간짜리는 네댓 번이다.
    나눌 때는 고르게 나누고, 경계는 대사가 끊기는 자리로 옮긴다.
    """
    items = [s for s in segments if str(s.get("ja", "")).strip()]
    if not items:
        return []

    parts = plan_batch_count(items, batch_lines=batch_lines, batch_chars=batch_chars)
    groups = _split(items, parts, snap=snap)

    # 경계를 옮기다 한 묶음이 한도를 넘으면 한 번 더 잘게 나눈다
    guard = 0
    while guard < 5 and any(len(g) > batch_lines for g in groups):
        guard += 1
        parts += 1
        groups = _split(items, parts, snap=snap)

    return [
        Batch(number=i, total=len(groups), segments=g, context=context)
        for i, g in enumerate(groups, start=1)
    ]


def _split(items: list[dict[str, Any]], parts: int, *, snap: bool) -> list[list[dict[str, Any]]]:
    if parts <= 1:
        return [items]

    boundaries: list[int] = []
    taken: set[int] = set()
    position = 0
    for count in _even_counts(len(items), parts)[:-1]:
        position += count
        cut = _snap(position, items, taken, window=SNAP_WINDOW) if snap else position
        # 경계는 반드시 앞으로만 간다. 한 묶음이 15줄보다 짧으면 조용한 자리를
        # 찾다가 앞 경계보다 뒤로 넘어갈 수 있는데, 그러면 잘린 자리가 겹쳐서
        # 같은 줄이 두 묶음에 들어간다. 사용자는 같은 대사를 두 번 번역하게 된다
        앞경계 = boundaries[-1] if boundaries else 0
        cut = min(max(cut, 앞경계 + 1), len(items) - 1)
        if cut <= 앞경계:
            break  # 더 나눌 자리가 없다
        boundaries.append(cut)
        taken.add(cut)

    groups, start = [], 0
    for cut in boundaries:
        groups.append(items[start:cut])
        start = cut
    groups.append(items[start:])
    return [g for g in groups if g]


def make_review_batch(batch: Batch, translations: dict[int, str]) -> Batch | None:
    """이미 번역해 놓은 묶음을 다시 보는 묶음. 볼 것이 없으면 None.

    **번역이 붙은 줄만 담는다.** 빈 줄을 섞어 주면 모델이 그 줄을 「고칠
    것」으로 잡아서 새로 번역해 낸다. 그것은 검수가 아니라 번역이고,
    빠진 줄을 다시 물어보는 길이 따로 있다.
    """
    있는것 = [s for s in batch.segments if str(translations.get(s["index"], "")).strip()]
    if not 있는것:
        return None
    return Batch(
        number=batch.number,
        total=batch.total,
        segments=있는것,
        context=batch.context,
        앞선것=list(batch.앞선것),
        is_review=True,
        옮긴것={s["index"]: translations[s["index"]] for s in 있는것},
    )


def make_retry_batch(batch: Batch, missing: list[int]) -> Batch:
    """빠진 번호만 담은 다시 물어보기용 묶음."""
    wanted = set(missing)
    segments = [s for s in batch.segments if s["index"] in wanted]
    return Batch(
        number=batch.number,
        total=batch.total,
        segments=segments,
        is_retry=True,
        context=batch.context,
        # 다시 물어볼 때도 앞 묶음을 그대로 들고 간다. 빠진 줄만 다시
        # 번역하는 것인데 거기서 호칭이 튀면 고치는 의미가 없다
        앞선것=list(batch.앞선것),
    )


_LINE = re.compile(
    r"""^\s*
    \[?                       # [12] 처럼 감싸는 경우
    # **자릿수를 좁게 잡으면 그 줄이 조용히 사라진다.** 트랙마다 번호 칸을
    # 떼어 주면 번호가 커지는데, 예전 `\d{1,6}` 은 100만 넘는 줄을 아예 번호로
    # 안 봤다. 그 줄은 「빠졌다」로 남고, 다시 물어도 같은 번호로 와서 또
    # 사라진다 — 영원히 안 채워진다
    (?P<num>\d{1,9})
    \]?
    # 탭, 마침표, 괄호, 콜론 무엇으로 구분하든 받는다.
    # 빈칸도 받는다 — 답을 코드블록에 안 담으면 탭이 빈칸으로 뭉개져서 오는데,
    # 그러면 멀쩡한 답인데도 "번역을 읽지 못했습니다" 만 되풀이하게 된다.
    #
    # **전각도 받는다.** 일본어 원문을 보고 답하는 모델은 구분자까지 전각으로
    # 낸다 — `1．` `1）` `1：` `1、`. 반각만 받으면 그 줄은 번호로 안 보여서
    # 통째로 「빠졌다」 가 되고, 다시 물어도 같은 꼴로 와서 영원히 안 채워진다
    \s*[\t.):\-–—\ 　．）：、，]\s*
    (?P<text>.*)$
    """,
    re.VERBOSE,
)


# 마크다운 표의 칸막이 줄. `|---|:---:|` 같은 것
_표칸막이 = re.compile(r"^\|?[\s:|-]+\|?$")


def 다듬기(line: str) -> str:
    """복사되어 온 줄에서 **꾸밈만** 걷어낸다.

    **여기가 이 설계에서 제일 먼저 무너질 곳이다.** 번호 체계도 지문도 전부
    「모양 검사를 통과한 뒤」 의 이야기인데, 모양은 벤더마다 다르고 다음 달에
    또 바뀐다. 화면에서 표로 보이는 답을 복사하면 표 그대로 오고, 번호를
    굵게 쓰는 모델도 있다. 그때 답을 통째로 버리면 사용자는 「번역을 읽지
    못했습니다」 만 되풀이해 보고 고칠 방법도 모른다.
    """
    글 = line.strip()

    # `| 10001 | 오늘도 고마워 |` → `10001\t오늘도 고마워`
    if 글.startswith("|"):
        if _표칸막이.match(글):
            return ""
        칸들 = [칸.strip() for 칸 in 글.strip("|").split("|")]
        칸들 = [칸 for 칸 in 칸들 if 칸]
        if len(칸들) >= 2:
            글 = 칸들[0] + "\t" + " ".join(칸들[1:])

    # `**10001.**` · `__10001__` 처럼 굵게 쓴 번호
    글 = 글.replace("**", "").replace("__", "")
    return 글.strip()


def 번호줄(text: str) -> list[tuple[int, str]]:
    """답에서 `번호<탭>글` 을 뽑는다. `(번호, 글)` 목록.

    누가 봐도 번역 줄인 것만 남긴다. 답이 어느 묶음 것인지 알아낼 때도 쓴다.
    """
    나온것 = []
    for raw in text.splitlines():
        line = 다듬기(raw)
        if not line or _is_noise(line):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        글 = match.group("text").strip()
        if 글:
            나온것.append((int(match.group("num")), 글))
    return 나온것


def parse_response(text: str, expected: Iterable[int]) -> ParseResult:
    """AI가 준 답을 읽어 번호별 번역으로 만든다.

    AI마다 구분자를 제멋대로 쓴다. 탭이 사라지고 `1. `이 되거나 `1) `이 되는
    일이 흔해서 웬만한 형태를 다 받아들인다.
    """
    wanted = list(expected)
    wanted_set = set(wanted)
    result = ParseResult()

    for raw in text.splitlines():
        line = 다듬기(raw)
        if not line or _is_noise(line):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        number = int(match.group("num"))
        body = match.group("text").strip()
        if not body:
            continue
        if number in wanted_set:
            result.translations[number] = body
        else:
            result.unexpected.append(number)

    result.missing = [n for n in wanted if n not in result.translations]

    # 번호를 1부터 다시 매겨서 돌려준 경우. 개수는 맞는데 번호가 전부 어긋난다.
    # 이대로 쓰면 자막이 통째로 밀리므로 반드시 잡아야 한다.
    if result.unexpected and len(result.unexpected) >= len(result.translations):
        result.renumbered = True

    if not result.translations:
        result.refused = _looks_like_refusal(text)

    # 번호는 맞는데 알맹이가 일본어 원문인 경우. 담으면 일본어 자막이 나온다
    result.not_korean = 번역이_아닌가(result.translations)

    return result


# 한글이 이 비율보다 적으면 번역이 아니라고 본다.
#
# **실제로 겪었다.** 내 컴퓨터 AI 가 일본어 원문을 번호까지 그대로 되돌려 줬고,
# 읽는 쪽은 그것을 멀쩡한 번역으로 받아 담았다. `.lrc` 두 개가 통째로 일본어로
# 나왔다. 자막을 만들고 나서야 알았다.
#
# 완전히 0 으로 잡을 수는 없다. 「♪」 「んっ…」 같은 의성어 줄은 한국어로 옮겨도
# 한글이 한 글자도 없는 것이 정상이다. 줄 하나가 아니라 **묶음 전체**를 보고,
# 그중 한글이 든 줄이 얼마나 되는지로 가린다.
KOREAN_MIN_RATIO = 0.35

# 이보다 적은 줄로는 비율을 믿을 수 없다. 세 줄짜리 묶음이 전부 의성어일 수 있다
KOREAN_MIN_LINES = 6

_한글 = re.compile(r"[가-힣]")


def 한글비율(줄들: Iterable[str]) -> float:
    """한글이 든 줄이 몇 할인가."""
    글들 = [줄 for 줄 in 줄들 if str(줄).strip()]
    if not 글들:
        return 0.0
    return sum(1 for 줄 in 글들 if _한글.search(str(줄))) / len(글들)


def 한국어가_아닌가(글들: Iterable[str]) -> bool:
    """줄 수를 안 보고 **비율만** 본다.

    `번역이_아닌가` 는 여섯 줄 이상일 때만 본다. 세 줄이 전부 의성어일 수
    있어서 비율을 못 믿기 때문이다. 그 조심이 맞는 자리가 있고 아닌 자리가
    있다.

    - **묶음 하나**는 긴 트랙의 일부라 여섯 줄이 넘는다. 조심하는 편이 낫다
    - **제목**과 **트랙 하나를 통째로 넣는 것**은 세 줄짜리도 흔하다.
      거기서 여섯 줄을 기다리면 **한 번도 안 걸린다**

    그래서 두 가지를 따로 둔다.
    """
    있는것 = [str(g) for g in 글들 if str(g).strip()]
    if not 있는것:
        return False
    return 한글비율(있는것) < KOREAN_MIN_RATIO


def 번역이_아닌가(번역: dict[int, str]) -> bool:
    """받아 놓고 보니 번역이 아닌 것 같은가.

    일본어 원문을 그대로 되돌려 주는 모델이 있다. 번호까지 맞춰서 오므로
    읽는 쪽에서는 멀쩡한 답과 구별되지 않는다. 담고 나서 자막을 만들면
    일본어 자막이 나온다.
    """
    if len(번역) < KOREAN_MIN_LINES:
        return False
    return 한글비율(번역.values()) < KOREAN_MIN_RATIO


def _is_noise(line: str) -> bool:
    """코드블록 표시나 머리말처럼 번역이 아닌 줄."""
    return line.startswith("```") or line.startswith("---")


def _looks_like_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(sign.lower() in lowered for sign in REFUSAL_SIGNS)


def remap_renumbered(text: str, expected: list[int]) -> dict[int, str] | None:
    """1부터 다시 매겨진 답을 원래 번호로 되돌린다.

    줄 수가 정확히 같을 때만 한다. 하나라도 다르면 어디가 밀렸는지 알 수 없어서
    되돌리다가 자막 전체를 망칠 수 있다. 그럴 땐 다시 받는 편이 안전하다.
    """
    bodies = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _is_noise(line):
            continue
        match = _LINE.match(line)
        if match and match.group("text").strip():
            bodies.append(match.group("text").strip())

    if len(bodies) != len(expected):
        return None
    return dict(zip(expected, bodies))
