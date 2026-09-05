"""번역을 끝까지 밀고 가는 부분.

자동이든 복붙이든 UI에는 똑같이 "몇 번째 / 몇 개"로만 보인다.

자동으로 돌리다 거절당하거나 키가 없으면 그 묶음을 복붙 대기줄로 넘긴다.
야하지 않은 구간은 자동으로 지나가고, 걸리는 곳만 사용자 손이 간다.

빠진 줄이 있으면 그 번호만 다시 물어본다. 몇 번을 다시 물어도 안 오면 그 줄만
포기하고 넘어간다. 한 줄 때문에 전체가 멈추는 것이 더 나쁘다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.core import wordbook
from app.core.exchange import (
    BATCH_CHARS,
    BATCH_LINES,
    Batch,
    ParseResult,
    make_batches,
    make_retry_batch,
    make_review_batch,
    이어줄줄수,
    parse_response,
    번호줄,
    칸크기,
    remap_renumbered,
)
from app.core.providers import Provider, ProviderError, RateLimited, Refused

# 빠진 줄을 몇 번까지 다시 물어볼지. 이보다 많이 하면 사용자만 기다린다
MAX_RETRIES = 2


def _물어보기(
    provider: Provider, batch: Batch,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """공급자에게 이 묶음을 번역시킨다.

    내 컴퓨터에서 도는 모델에는 지시문과 알맹이를 **따로** 보낸다. 작은 모델은
    한 덩어리로 주면 둘을 구분하지 못하고, 우리 규칙 문장에 번호를 매겨서
    되돌려 준다. 밖의 AI 는 그러지 않으므로 한 덩어리로 그대로 준다.

    `translate_split` 이 있는 공급자만 나눠 받는다. 없으면 예전 그대로다 —
    시험에 끼우는 가짜 부품도 그대로 돌아간다.
    """
    나눠받기 = getattr(provider, "translate_split", None)
    if callable(나눠받기):
        if on_progress is not None:
            return 나눠받기(batch.rules, batch.data, on_progress)
        return 나눠받기(batch.rules, batch.data)
    return provider.translate(batch.prompt)


@dataclass
class Progress:
    """UI에 그대로 보여 줄 수 있는 상태."""

    done: int
    total: int
    message: str

    @property
    def ratio(self) -> float:
        return self.done / self.total if self.total else 1.0


@dataclass
class Handoff:
    """자동으로 못 해서 사람에게 넘기는 묶음."""

    batch: Batch
    reason: str


@dataclass
class TranslationSession:
    """묶음을 하나씩 처리하며 번역을 모은다.

    UI는 이것 하나만 들고 있으면 된다. 자동으로 얼마나 됐는지, 지금 붙여넣어야 할
    프롬프트가 무엇인지, 다 됐는지를 여기서 묻는다.
    """

    segments: list[dict[str, Any]]
    max_retries: int = MAX_RETRIES
    context: str = ""
    # 묶음 한도. 내 컴퓨터에서 도는 모델은 더 크게 받는다
    batch_lines: int = BATCH_LINES
    batch_chars: int = BATCH_CHARS
    # 적어 둔 낱말을 표로 가려서 내보낼까. 끄면 예전처럼 그대로 나간다
    가리기: bool = True
    # **번호를 1부터 다시 매겨 온 답을 되돌려 받을 것인가.**
    #
    # 세션 하나는 자기 트랙만 안다. 여러 트랙을 동시에 돌리는 중이면 1부터
    # 매겨 온 답이 어느 트랙 것인지 정할 근거가 없다 — 줄 수만 맞으면 딴 트랙
    # 답이 칸을 넘어 그대로 들어간다. 그래서 **부르는 쪽이 알려 준다.**
    되매김허용: bool = True
    # **내줄 때 그 줄이 무슨 말이었나.** `{번호: 원문}`.
    #
    # 사용자는 품질이 마음에 안 들면 다시 받아쓴다. 그런데 프롬프트를 이미
    # 복사해 둔 상태라면, 돌아온 답의 번호는 새 받아쓰기에도 그대로 있다.
    # 그대로 담으면 **다른 말의 번역이 그 줄에 붙는다** — 오류도 빠짐도 없다.
    #
    # 줄마다 따로 본다. 트랙 통째로 견주면 받아쓰기가 미세하게만 달라져도
    # 멀쩡한 줄의 답까지 다 버리게 된다.
    내준원문: dict[int, str] = field(default_factory=dict)
    # 방금 넣은 답에서 「그새 말이 바뀌어」 안 담은 줄
    낡은줄: list[int] = field(default_factory=list, repr=False)

    translations: dict[int, str] = field(default_factory=dict)
    # `{표: 일본어}`. 묶음이 여럿이어도 같은 낱말은 늘 같은 표다
    표맵: dict[str, str] = field(default_factory=dict, repr=False)
    가림사전: dict[str, str] = field(default_factory=dict, repr=False)
    # `{줄 번호: [못 되돌린 표]}`. 번역기가 우리가 준 적 없는 번호를 지어냈을 때
    못되돌린표: dict[int, list[str]] = field(default_factory=dict, repr=False)
    # `{줄 번호: [빼먹은 표]}`. **이쪽이 더 나쁘다** — 지어낸 번호는 자막에
    # `KW09` 로 남아서 눈에라도 띄지만, 빼먹은 것은 그 낱말이 통째로 사라진
    # 멀쩡해 보이는 문장이 되어 아무도 모른다
    빠진표: dict[int, list[str]] = field(default_factory=dict, repr=False)
    # 방금 넣은 답에서 어긋난 것만. 화면에 바로 띄우는 데 쓴다.
    # **쌓인 것을 그대로 띄우면 안 된다** — 세 묶음 전에 난 사고가 넣을 때마다
    # 다시 뜬다
    방금어긋난표: dict[int, list[str]] = field(default_factory=dict, repr=False)
    # `{줄 번호: 가려서 내보낸 일본어}`. 돌아온 줄과 견주려면 있어야 한다
    _가린줄: dict[int, str] = field(default_factory=dict, repr=False)
    # `{줄 번호: 표가 박힌 채로 돌아온 번역}`. 되돌리기 전 모습이다.
    #
    # **앞 묶음을 보여 줄 때와 검수할 때 이것을 내보낸다.** 되돌린 한국어를
    # 내보내면 두 가지가 한꺼번에 터진다 — 노골적인 한국어가 그대로 나가고,
    # 같은 줄에 `KW01` 과 「자지」 가 나란히 놓여서 **표의 정답을 알려 준다.**
    #
    # 한국어를 도로 가리는 수는 못 쓴다. `膣`→「질」 이 「질투」 를, `ちんちん`
    # →「고추」 가 고춧가루를 먹는다. 받은 그대로 들고 있는 것이 유일하게
    # 안전한 길이다
    가린번역: dict[int, str] = field(default_factory=dict, repr=False)
    handoffs: list[Handoff] = field(default_factory=list)
    _batches: list[Batch] = field(default_factory=list, repr=False)
    _auto_done: set[int] = field(default_factory=set, repr=False)
    # 사람이 손으로 끝낸(또는 지나친) 묶음 번호.
    #
    # 예전에는 "몇 번째까지 지났나" 하는 **자리 하나**였다. 그래서 1번을 넣기
    # 전에는 2번이 아예 안 보였다. 3시간짜리는 묶음이 열여덟 개인데, 사용자가
    # 채팅 세션 여러 개를 열어 한꺼번에 돌리고 싶어도 길이 없었다.
    #
    # 번호를 모아 두면 순서가 없어진다. 7번을 먼저 넣고 3번을 나중에 넣어도 된다
    _hand_done: set[int] = field(default_factory=set, repr=False)
    # 빠진 줄만 다시 물어보는 묶음. 원래 묶음 번호 → 다시 물어볼 묶음
    _retries: dict[int, Batch] = field(default_factory=dict, repr=False)
    # 사용자가 지금 화면에서 보고 있는 묶음 번호. 0 이면 "그냥 다음 것"
    _looking_at: int = 0

    def __post_init__(self) -> None:
        self._batches = make_batches(
            self._가린줄들(),
            context=self.context,
            batch_lines=self.batch_lines,
            batch_chars=self.batch_chars,
        )

    # ---- 민감 낱말 가리기 ----
    #
    # 구글 API 가 **내용 때문에 거절한다.** `BLOCK_NONE` 을 다 켜 두었는데도
    # 서버가 자체로 막는다(`providers.GeminiProvider`). 그러면 그 묶음이
    # 통째로 복붙 대기줄로 넘어간다.
    #
    # 노골적인 낱말만 표로 바꿔서 내보내면 나머지 문장은 멀쩡히 번역된다.
    # 돌아오면 우리가 되돌린다. **나가는 자리 하나, 들어오는 자리 하나**만
    # 손대면 자동 번역과 복붙이 같이 덮인다.

    def _내준것적기(self, batch: Any) -> None:
        """이 묶음으로 내보낸 줄이 무슨 말이었는지 적어 둔다."""
        for 줄 in getattr(batch, "segments", []):
            번호 = int(줄["index"])
            self.내준원문.setdefault(번호, self._지금원문.get(번호, ""))

    @property
    def _지금원문(self) -> dict[int, str]:
        return {int(s["index"]): str(s.get("ja", "")) for s in self.segments}

    def _바뀐줄인가(self, 번호: int) -> bool:
        """내줄 때와 지금 말이 다른가. 적어 둔 것이 없으면 아니라고 본다."""
        옛것 = self.내준원문.get(번호)
        if not 옛것:
            return False
        return 옛것 != self._지금원문.get(번호, 옛것)

    def _가린줄들(self) -> list[dict[str, Any]]:
        """묶음에 넣을 줄들. 민감 낱말이 표로 바뀌어 있다.

        **원본을 안 건드린다.** `self.segments` 는 화면에도 쓰이므로, 여기서
        고치면 사용자가 보는 일본어까지 `KW01` 이 된다.
        """
        if not self.가리기:
            return self.segments
        self.가림사전 = self.가림사전 or wordbook.쓸사전()
        난것 = []
        for 줄 in self.segments:
            가린것, self.표맵 = wordbook.가리기(
                str(줄.get("ja", "")), self.가림사전, self.표맵)
            self._가린줄[int(줄["index"])] = 가린것
            # 원문을 함께 들고 간다. 번역기로 가는 길(`Batch.plain`)은 표를
            # 풀어야 하는데, 묶음은 표맵을 모르기 때문이다
            난것.append({**줄, "ja": 가린것, "ja_raw": str(줄.get("ja", ""))})
        return 난것

    def _되돌리기(self, 옮긴것: dict[int, str]) -> dict[int, str]:
        """번역에 박힌 표를 한국어 낱말로 되돌린다.

        어긋난 것은 쌓아 둔다. **조용히 넘기면 `KW01` 이 박힌 자막이 나가거나,
        더 나쁘게는 그 낱말이 통째로 빠진 멀쩡해 보이는 자막이 나간다.**

        어긋나는 길이 둘이다.

            지어낸 번호   우리가 준 적 없는 표가 돌아왔다. 되돌릴 말이 없어
                          `KW09` 가 자막에 박힌다. 눈에는 띈다
            빼먹은 것     보낸 줄에 있던 표가 돌아온 줄에 없다. **아무 표시도
                          안 남는다.** 이쪽이 더 나쁘다
        """
        self.방금어긋난표 = {}
        if not self.표맵:
            return 옮긴것
        난것 = {}
        for 번호, 글 in 옮긴것.items():
            self.가린번역[번호] = 글
            난것[번호], 못한것 = wordbook.되돌리기(글, self.표맵, self.가림사전)
            for 표 in 못한것:
                self.못되돌린표.setdefault(번호, []).append(표)
                self.방금어긋난표.setdefault(번호, []).append(표)

            # 보낸 줄에 있던 표가 돌아온 줄에 없나. **줄마다 따로 센다** —
            # 묶음 전체로 세면 번역기가 A 줄의 표를 B 줄로 옮겨 놓아도 수가
            # 맞아서 그냥 지나간다
            보낸것 = wordbook.표들(self._가린줄.get(번호, ""))
            if not 보낸것:
                continue
            돌아온것 = wordbook.표들(글)
            for 표 in 보낸것:
                if 돌아온것.count(표) < 보낸것.count(표):
                    # **표가 없어도 뜻이 살아 있으면 잃은 것이 아니다.**
                    #
                    # 두 가지 길로 이렇게 된다. 번역기로 보낼 때는 표를 풀고
                    # 원문을 내보내므로 답에 표가 아예 없다. 그리고 AI 가 표를
                    # 무시하고 낱말을 그대로 옮겨 주는 일도 잦다. 둘 다 답은
                    # 멀쩡한데, 여기서 세기만 하면 「어긋났다」고 짚어서
                    # 사용자가 멀쩡한 줄을 다시 물어보게 된다.
                    옮긴말 = self.가림사전.get(self.표맵.get(표, ""), "")
                    if 옮긴말 and 옮긴말 in 글:
                        continue
                    if 표 in self.빠진표.setdefault(번호, []):
                        continue
                    self.빠진표[번호].append(표)
                    self.방금어긋난표.setdefault(번호, []).append(표)
        return 난것

    def 어긋난표말(self) -> str:
        """방금 넣은 답에서 어긋난 표를 한 마디로. 없으면 빈 글.

        **낱말을 그대로 적어 준다.** 「1줄에서 표가 어긋났습니다」 만으로는
        무엇이 사라졌는지 알 수가 없어서 다시 물어볼 수도 없다.
        """
        if not self.방금어긋난표:
            return ""
        줄들 = sorted(self.방금어긋난표)
        말 = []
        for 번호 in 줄들[:3]:
            낱말 = [self.표맵.get(표, 표) for 표 in self.방금어긋난표[번호]]
            낱말 = [self.가림사전.get(것) or 것 for 것 in 낱말]
            말.append(f"{번호}번({', '.join(낱말)})")
        더 = f" 외 {len(줄들) - 3}줄" if len(줄들) > 3 else ""
        return (f"⚠ 적어 둔 낱말이 {len(줄들)}줄에서 어긋났습니다: "
                f"{' · '.join(말)}{더}. 그 줄만 다시 물어보세요.")

    # ---- 상태 ----

    @property
    def total_batches(self) -> int:
        return len(self._batches)

    def _이어주기(self, batch: Batch | None) -> Batch | None:
        """앞 묶음을 어떻게 번역했는지 이 묶음에 붙여 준다.

        묶음끼리 서로를 몰라서, 앞에서 「오빠」였던 것이 뒤에서 「형」이 되고
        존댓말이 경계에서 튀었다. 2시간짜리는 그 경계가 네댓 번 있다.

        **아직 번역 안 된 앞 묶음은 붙이지 않는다.** 복붙은 순서가 없어서
        7번을 먼저 넣을 수 있다. 그때 6번 자리에 일본어만 있는 줄을 참고랍시고
        붙이면 도움이 아니라 방해다.
        """
        if batch is None:
            return None
        batch.앞선것 = self._앞꼬리(batch.number)
        return batch

    def _앞꼬리(self, 번호: int) -> list[tuple[int, str, str]]:
        앞 = next((b for b in self._batches if b.number == 번호 - 1), None)
        if 앞 is None:
            return []
        꼬리: list[tuple[int, str, str]] = []
        for s in 앞.segments[-이어줄줄수:]:
            # **표가 박힌 채로 내보낸다.** 되돌린 한국어를 붙이면 노골적인 말이
            # 그대로 나가고, `KW01` 옆에 그 뜻이 나란히 놓여 표가 들통난다
            옮긴것 = str(self._내보낼번역(s["index"])).strip()
            if 옮긴것:
                꼬리.append((s["index"], str(s.get("ja", "")), 옮긴것))
        return 꼬리

    def _내보낼번역(self, 번호: int) -> str:
        """이 줄의 번역을 밖에 보여 줄 때 쓸 모습. 표가 박힌 채다.

        가리기를 껐거나 그 줄에 가릴 낱말이 없었으면 되돌린 것과 같다.
        """
        return self.가린번역.get(번호) or self.translations.get(번호, "")

    def _settled(self, batch: Batch) -> bool:
        """이 묶음이 끝났는지. 자동으로 됐거나 사람이 손을 댔으면 끝난 것이다."""
        return batch.number in self._auto_done or batch.number in self._hand_done

    @property
    def finished_count(self) -> int:
        """끝난 묶음 수.

        자동으로 된 것과 사람이 지나친 것을 따로 더하면 안 된다. 1번이 자동으로
        되고 2번을 손으로 넘기면 `1 + 2 = 3` 이 되어, 3번이 남아 있는데도 다
        끝난 것으로 세어졌다. 그러면 그 묶음은 영영 안 물어보고 자막에서 빠진다.
        """
        return sum(1 for batch in self._batches if self._settled(batch))

    @property
    def done(self) -> bool:
        """모든 묶음이 자동으로 끝났거나 사람 손을 거쳤는지."""
        return not self._retries and self.finished_count >= self.total_batches

    @property
    def touched(self) -> bool:
        """사람이든 자동이든 이 묶음에 손을 댔는지.

        손대지 않은 것만 나중에 다른 트랙과 다시 합칠 수 있다. 손댄 뒤에 합치면
        번호가 바뀌어 이미 붙여넣은 번역이 어긋난다.
        """
        return bool(self.translations or self._auto_done or self._hand_done)

    def progress(self, message: str = "") -> Progress:
        return Progress(
            done=self.finished_count,
            total=self.total_batches,
            message=message,
        )

    # ---- 자동 ----

    def run_auto(
        self,
        provider: Provider,
        *,
        on_progress: Callable[[Progress], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        """공급자로 끝까지 돌려 본다. 못 한 묶음은 복붙 대기줄로 넘긴다."""
        for batch in self._batches:
            if should_stop and should_stop():
                break
            if batch.number in self._auto_done:
                continue

            _report(on_progress, self.progress(f"번역 중 {batch.number}/{batch.total}"))
            try:
                self._translate_one(provider, batch)
                self._auto_done.add(batch.number)
            except Refused as error:
                self._hand_off(batch, str(error) or "내용 때문에 거절당했습니다.")
            except RateLimited as error:
                # 한도는 기다리면 풀리지만 사용자를 세워 두는 것보다 넘기는 편이 낫다
                self._hand_off(batch, str(error))
            except ProviderError as error:
                self._hand_off(batch, str(error))

        _report(on_progress, self.progress("자동 번역 끝"))

    def run_one(self, provider: Provider) -> bool:
        """지금 물어볼 묶음 **하나만** 자동으로 번역한다. 했으면 True.

        `run_auto` 는 남은 것을 전부 돈다. 여기는 사용자가 화면에서 "이 묶음만
        내 컴퓨터 AI로" 를 눌렀을 때 쓴다.

        사용자가 쓰는 길은 복붙이다. 브라우저에서 거절당하는 것은 이 프로그램이
        볼 수가 없다 — 아예 다른 창에서 일어나는 일이다. 그래서 거절을
        알아채고 넘겨주는 것이 아니라, **사용자가 보고 누르면** 그 자리에서
        이어받는다.
        """
        batch = self.pending_batch()
        if batch is None:
            return False
        self._translate_one(provider, batch)
        self._auto_done.add(batch.number)
        self._retries.pop(batch.number, None)
        if self._looking_at == batch.number:
            self._looking_at = 0
        return True

    def _translate_one(self, provider: Provider, batch: Batch) -> None:
        # `run_auto` 는 `_batches` 를 그대로 돌기 때문에 여기서도 붙여야 한다.
        # 붙이는 자리를 하나라도 빠뜨리면 그 길로만 호칭이 튄다
        self._이어주기(batch)
        answer = _물어보기(provider, batch)
        result = self._recover(answer, batch.indices, self._absorb(answer, batch.indices))

        attempt = 0
        while result.missing and attempt < self.max_retries:
            attempt += 1
            retry = make_retry_batch(batch, result.missing)
            answer = _물어보기(provider, retry)
            result = self._recover(answer, retry.indices, self._absorb(answer, retry.indices))

        # 끝내 안 온 줄은 비워 둔다. 그 줄만 자막에서 빠지고 나머지는 멀쩡하다

    # ---- 복붙 ----
    #
    # 순서가 없다. 묶음 열여덟 개를 한꺼번에 꺼내서, 채팅 세션 열여덟 개에
    # 각각 붙여넣고, 답이 나오는 대로 아무 순서로나 넣을 수 있다.
    #
    # 예전에는 "몇 번째까지 지났나" 하는 자리 하나로 돌아가서, 1번을 넣기
    # 전에는 2번이 아예 안 보였다. 3시간짜리는 열여덟 번을 **줄 세워서**
    # 기다려야 했다.

    def batch_by_number(self, number: int) -> Batch | None:
        """번호로 묶음을 찾는다. 다시 물어볼 것이 걸려 있으면 그것을 준다."""
        if number in self._retries:
            return self._이어주기(self._retries[number])
        for batch in self._batches:
            if batch.number == number:
                return self._이어주기(batch)
        return None

    def pending_batch(self) -> Batch | None:
        """지금 화면에 띄울 묶음. 없으면 None.

        사용자가 고른 것이 있으면 그것, 없으면 안 끝난 것 중 가장 앞.
        """
        고른것 = self.batch_by_number(self._looking_at) if self._looking_at else None
        if 고른것 is not None:
            self._내준것적기(고른것)
            return 고른것
        for batch in self._batches:
            if self._settled(batch):
                continue
            낼것 = self._이어주기(self._retries.get(batch.number, batch))
            self._내준것적기(낼것)
            return 낼것
        # 남은 것이 없어도 다시 물어볼 것이 걸려 있으면 그것을 준다
        for 번호 in sorted(self._retries):
            return self._이어주기(self._retries[번호])
        return None

    def look_at(self, number: int) -> bool:
        """사용자가 이 묶음을 보겠다고 골랐다. 있으면 True."""
        if number and self.batch_by_number(number) is None:
            return False
        self._looking_at = number
        return True

    def batches_view(self) -> list[dict[str, Any]]:
        """묶음 전부와 각각의 상태. 화면이 목록을 그릴 때 쓴다.

        **끝난 것도 뺴지 않는다.** 열여덟 개 중 무엇을 했고 무엇이 남았는지
        한눈에 보여야, 여러 창에 나눠 돌리다 헷갈리지 않는다.
        """
        지금 = self.pending_batch()
        # 자동으로 못 해서 넘어온 묶음은 **왜** 넘어왔는지 같이 준다.
        # 여태 `handoffs` 에 사유를 쌓기만 하고 아무도 안 읽어서, 사용자는
        # 거절인지 한도인지 서버 오류인지 모른 채 복붙 화면만 마주했다.
        # 가리기가 거절을 실제로 줄였는지도 이것이 보여야 알 수 있다
        넘어온이유 = {h.batch.number: h.reason for h in self.handoffs}
        보임 = []
        for batch in self._batches:
            다시 = self._retries.get(batch.number)
            보임.append({
                "number": batch.number,
                "lines": len(batch.segments),
                "span": batch.span,
                "done": self._settled(batch),
                "auto": batch.number in self._auto_done,
                "is_retry": 다시 is not None,
                "missing": len(다시.segments) if 다시 is not None else 0,
                "now": 지금 is not None and 지금.number == batch.number,
                "reason": "" if self._settled(batch) else 넘어온이유.get(batch.number, ""),
            })
        return 보임

    def whose_answer(self, pasted: str) -> int:
        """붙여넣은 답이 **어느 묶음** 것인지 알아낸다. 모르면 0.

        답에는 줄 번호가 그대로 들어 있다. 어느 묶음이 어느 번호를 가졌는지는
        우리가 아니까, 겹치는 것이 가장 많은 묶음을 고르면 된다.

        이것이 있어야 사용자가 창을 일곱 개 열어 놓고 **아무 순서로나** 답을
        넣을 수 있다. 넣기 전에 "몇 번 것입니다" 를 고르게 하면 그것부터가 일이다.
        """
        번호들 = {n for n, _ in 번호줄(pasted)}
        if not 번호들:
            return 0

        가장맞는것, 가장많이 = 0, 0
        for batch in self._batches:
            겹침 = len(번호들 & set(batch.indices))
            if 겹침 > 가장많이:
                가장맞는것, 가장많이 = batch.number, 겹침
        return 가장맞는것

    def submit(self, pasted: str, number: int = 0) -> ParseResult:
        """붙여넣은 답을 받는다.

        `number` 를 주지 않으면 답에 적힌 줄 번호를 보고 **어느 묶음 것인지
        알아서 찾는다.** 그래서 순서가 없어도 된다.
        """
        번호 = number or self.whose_answer(pasted) or (
            self.pending_batch().number if self.pending_batch() else 0
        )
        batch = self.batch_by_number(번호)
        if batch is None:
            return ParseResult()

        result = self._recover(pasted, batch.indices, self._absorb(pasted, batch.indices))

        if result.not_korean:
            # 번역이 아니다. 끝난 것으로 치우면 일본어인 채로 자막이 나간다
            return result

        if result.missing and not result.refused:
            self._retries[batch.number] = make_retry_batch(batch, result.missing)
            return result

        self._retries.pop(batch.number, None)
        if not result.refused:
            self._hand_done.add(batch.number)
            if self._looking_at == batch.number:
                self._looking_at = 0   # 끝났으니 다음 안 끝난 것으로 옮겨 간다
        return result

    # ---- 검수 ----
    #
    # 한 번 번역한 것을 **원문과 나란히 다시 보여 주고** 고칠 줄만 받는다.
    # 전부 다시 쓰게 하면 멀쩡하던 줄까지 흔들려서, 좋아지는 만큼 나빠진다.
    # 고칠 줄만 집게 하면 나빠질 자리가 없다.

    def review_batch(self, number: int) -> Batch | None:
        """이 묶음의 검수 묶음. 볼 것이 없으면 None.

        **아직 안 끝난 묶음은 검수하지 않는다.** 번역이 절반만 붙은 것을
        검수해 봐야 나머지 절반이 「고칠 것」으로 잡혀서 새로 번역돼 온다.
        """
        batch = next((b for b in self._batches if b.number == number), None)
        if batch is None or not self._settled(batch):
            return None
        # 검수도 표가 박힌 채로 내보낸다. 여기가 제일 위험한 자리다 —
        # 한 줄에 `KW01` 과 그 뜻을 나란히 놓아 주는 꼴이 된다
        보여줄것 = {번호: self._내보낼번역(번호) for 번호 in self.translations}
        return make_review_batch(self._이어주기(batch), 보여줄것)

    def reviewable(self) -> list[int]:
        """검수할 수 있는 묶음 번호들. 화면이 단추를 그릴 때 쓴다."""
        return [
            b.number for b in self._batches
            if self._settled(b) and self.review_batch(b.number) is not None
        ]

    def submit_review(self, pasted: str, number: int) -> int:
        """검수 답을 받아 그 줄들만 갈아 끼운다. 몇 줄을 고쳤는지 돌려준다.

        **빠진 줄을 다시 물어보지 않는다.** 검수는 고칠 것만 내는 것이라
        받은 줄이 보낸 줄보다 적은 것이 정상이다. 여기서 빠진 줄을 쫓으면
        멀쩡한 줄을 계속 다시 물어보게 된다.
        """
        검수 = self.review_batch(number)
        if 검수 is None:
            return 0
        result = parse_response(pasted, 검수.indices)
        if result.not_korean or result.refused:
            return 0
        return self._고쳐넣기(result.translations)

    def run_review_one(self, provider: Provider, number: int) -> int:
        """공급자에게 이 묶음을 검수시킨다. 몇 줄을 고쳤는지."""
        검수 = self.review_batch(number)
        if 검수 is None:
            return 0
        답 = _물어보기(provider, 검수)
        result = parse_response(답, 검수.indices)
        if result.not_korean or result.refused:
            return 0
        return self._고쳐넣기(result.translations)

    def _고쳐넣기(self, 고친것: dict[int, str]) -> int:
        """정말 달라진 줄만 갈아 끼운다.

        똑같은 글을 다시 돌려주는 모델이 있다. 그것까지 「고쳤다」 고 세면
        사용자는 스무 줄이 고쳐진 줄 알고 다시 들여다보게 된다.

        **표를 되돌리고 넣는다.** 검수도 표가 박힌 글을 보고 하는 것이라
        고친 줄에도 표가 들어 있다. 그냥 넣으면 `KW01` 이 자막에 박힌다.
        """
        바뀐것 = 0
        for 번호, 글 in self._되돌리기(고친것).items():
            새것 = str(글).strip()
            if not 새것 or 새것 == str(self.translations.get(번호, "")).strip():
                continue
            self.translations[번호] = 새것
            바뀐것 += 1
        return 바뀐것

    def skip_current(self) -> None:
        """지금 묶음을 포기하고 넘어간다. 거절당해서 도저히 안 될 때 쓴다."""
        batch = self.pending_batch()
        if batch is None:
            return
        self._retries.pop(batch.number, None)
        self._hand_done.add(batch.number)
        if self._looking_at == batch.number:
            self._looking_at = 0

    # ---- 공통 ----

    def _recover(
        self, answer: str, expected: Iterable[int], result: ParseResult
    ) -> ParseResult:
        """번호를 1부터 다시 매겨 온 답을 원래 번호로 되돌린다.

        손으로 붙여넣는 쪽에만 이 복구가 있었다. **자동 번역에는 없었다.**
        그래서 공급자가 번호를 다시 매기면 300줄짜리 답을 통째로 버리고
        "빠졌다" 며 다시 물어봤다. 같은 공급자는 또 다시 매겨 오므로 두 번 더
        묻고 나서 그 묶음을 통째로 포기했다. 멀쩡한 번역을 버린 것이다.

        줄 수가 정확히 같을 때만 되돌린다. 하나라도 다르면 어디가 밀렸는지
        알 수 없어서 되돌리다 자막 전체를 망칠 수 있다.
        """
        if not result.renumbered:
            return result

        if not self.되매김허용:
            # 기다리는 트랙이 여럿이다. 주인을 정할 근거가 없으므로 받지 않고
            # 「다시 매겨 왔다」는 것만 알린다. 사람이 트랙을 고르면 그때 받는다
            return result

        # **옛 번호로 적힌 답.** 번호 칸을 떼기 전에는 트랙마다 1번부터
        # 매겼다. 그 번호로 프롬프트를 복사해 둔 채 판을 올리면, 돌아온 답은
        # 옛 번호이고 앱이 기다리는 것은 새 번호다.
        #
        # **딴 트랙 답과 생김새가 같다.** 둘 다 `1..N` 이라 번호만으로는 못
        # 가린다. 그래서 되매김과 같은 자리에서, 기다리는 트랙이 하나일 때만
        # 받는다. 한 판 지나 옛 번호가 다 사라지면 이 길은 지운다
        옛것 = self._옛번호로_읽기(answer, expected)
        if 옛것 is not None:
            self.translations.update(self._되돌리기(옛것))
            # **빠진 줄을 반드시 함께 알린다.** 안 그러면 절반만 온 답을
            # 다 온 것으로 보고 「자막을 만들었습니다」 라고 해 버린다
            return ParseResult(
                translations=옛것,
                missing=[n for n in expected if n not in 옛것],
            )

        번호들 = list(expected)
        되돌린것 = remap_renumbered(answer, 번호들)
        if not 되돌린것:
            return result
        self.translations.update(되돌린것)
        return ParseResult(translations=되돌린것)

    def _옛번호로_읽기(
        self, answer: str, expected: Iterable[int]
    ) -> dict[int, str] | None:
        """답이 「칸 시작을 뺀 옛 번호」로 적혀 있으면 제 번호로 옮겨 돌려준다.

        아니면 `None`. **어긋난 것이 하나라도 있으면 안 받는다** — 우연히
        겹치는 번호 때문에 딴 트랙 답을 받아들이면 안 된다.
        """
        번호들 = list(expected)
        if not 번호들:
            return None
        칸 = (min(번호들) // 칸크기) * 칸크기
        if 칸 <= 0:
            return None  # 옛 방식으로 매겨진 트랙이다. 옮길 것이 없다

        온것 = dict(번호줄(answer))
        if not 온것:
            return None
        옮긴것 = {}
        기다리는것 = set(번호들)
        for 번호, 글 in 온것.items():
            제자리 = 번호 + 칸
            if 제자리 not in 기다리는것:
                return None  # 하나라도 안 맞으면 이 답이 아니다
            옮긴것[제자리] = 글
        return 옮긴것 or None

    def _absorb(self, answer: str, expected: Iterable[int]) -> ParseResult:
        result = parse_response(answer, expected)
        if result.not_korean:
            # **담지 않는다.** 번호는 맞는데 알맹이가 일본어 원문이다.
            #
            # 실제로 겪었다. 내 컴퓨터 AI 가 원문을 번호까지 그대로 되돌려
            # 줬고, 그것이 멀쩡한 번역으로 담겨서 `.lrc` 두 개가 통째로
            # 일본어로 나왔다. 자막을 열어 보고서야 알았다.
            #
            # 안 담으면 그 묶음은 「빠진 것」으로 남는다. 다시 물어보거나
            # 사람이 붙여넣으면 된다. 일본어 자막이 나오는 것보다 낫다
            return result
        # **그새 말이 바뀐 줄은 안 담는다.** 다시 받아쓰면 번호는 그대로인데
        # 그 번호가 가리키는 말이 달라진다. 그대로 담으면 다른 말의 번역이
        # 그 줄에 붙고, 오류도 빠짐도 안 남는다
        받을것 = {n: 글 for n, 글 in result.translations.items()
                if not self._바뀐줄인가(n)}
        낡은것 = [n for n in result.translations if n not in 받을것]
        if 낡은것:
            self.낡은줄 = sorted(낡은것)
            result.missing = sorted(set(result.missing) | set(낡은것))

        # **되돌리는 자리는 여기 하나다.** 자동 번역도 복붙도 여기를 지난다
        self.translations.update(self._되돌리기(받을것))
        return result

    def _hand_off(self, batch: Batch, reason: str) -> None:
        if any(h.batch.number == batch.number for h in self.handoffs):
            return
        self.handoffs.append(Handoff(batch=batch, reason=reason))

    # ---- 결과 ----

    def missing_indices(self) -> list[int]:
        """끝내 번역이 안 붙은 줄. 자막에서 빠진다."""
        return [s["index"] for s in self.segments if s["index"] not in self.translations]


def _report(callback: Callable[[Progress], None] | None, progress: Progress) -> None:
    if callback:
        callback(progress)
