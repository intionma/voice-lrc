"""두 받아쓰기 결과를 견주어 본다.

지금까지 강도를 다섯 개 만들었는데, **어느 것이 실제로 나은지 잰 적이 한 번도
없다.** 만드는 쪽에는 GPU 도 음원도 없어서 잴 방법이 없었고, 사용자는 "잘 안
잡히는 것 같다" 는 느낌밖에 말할 수 없었다. 그러면 계속 추측으로 값을 만지게
된다. 같은 자리를 여러 번 왕복하게 만든 원인이 그것이다.

그래서 프로그램이 스스로 잰다. 같은 음원을 두 강도로 받아쓰고 이렇게 견준다.

    줄 수            많다고 좋은 것은 아니다. 헛소리도 줄이다
    말이 잡힌 시간    이쪽이 늘면 진짜로 더 잡은 것이다
    망가진 줄        늘면 헛소리가 는 것이다
    한쪽에만 있는 줄  어느 쪽이 무엇을 더 잡았는지

**여기서 "어느 쪽이 낫다" 고 판정하지 않는다.** 숫자를 늘어놓기만 한다.
헛소리를 잔뜩 지어내도 줄 수와 잡힌 시간은 늘기 때문이다. 마지막 판단은
사람이 들어 보고 한다.
"""

from __future__ import annotations

import bisect

from dataclasses import dataclass, field
from typing import Any

from app.core import garbage

# 두 줄이 이만큼 겹치면 "같은 자리" 로 본다.
#
# 이 값이 **정해만 놓고 쓰이지 않고 있었다.** 조금이라도 닿기만 하면 같은 자리로
# 봤다. VAD 가 앞뒤로 400ms 씩 여유를 붙이므로 옆줄끼리 으레 조금씩 겹친다.
# 그래서 새 강도가 사이에 새로 잡아낸 줄이 양옆 줄에 살짝 닿았다는 이유로
# "새로 잡은 곳" 에서 빠졌다. 견주기가 있으나 마나 해지는 자리다.
SAME_SEC = 0.8


def _span(줄: dict[str, Any]) -> tuple[float, float]:
    return float(줄["start"]), float(줄["end"])


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """같은 자리를 말하는 두 줄인가.

    짧은 쪽의 절반은 겹쳐야 한다. 다만 `SAME_SEC` 를 넘게 겹치면 그것으로 충분하다
    — 한쪽이 여러 줄을 한 줄로 합쳐 놓았을 때 길이만 보고 어긋나면 안 된다.
    신음처럼 0.3초짜리 줄도 견줄 수 있어야 하므로 고정값만 쓰지 않는다.
    """
    겹침 = min(a[1], b[1]) - max(a[0], b[0])
    if 겹침 <= 0:
        return False
    짧은쪽 = min(a[1] - a[0], b[1] - b[0])
    return 겹침 >= min(SAME_SEC, 짧은쪽 / 2)


def _spoken(segments: list[dict[str, Any]]) -> float:
    return sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)


@dataclass
class Side:
    """한쪽 결과의 요약."""

    label: str
    lines: int
    spoken_sec: float
    broken: int
    duration_sec: float = 0.0

    @property
    def coverage(self) -> float:
        return min(1.0, self.spoken_sec / self.duration_sec) if self.duration_sec else 0.0

    @property
    def broken_ratio(self) -> float:
        return self.broken / self.lines if self.lines else 0.0

    def to_view(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "lines": self.lines,
            "spoken_sec": round(self.spoken_sec, 1),
            "coverage": round(self.coverage, 3),
            "broken": self.broken,
            "broken_ratio": round(self.broken_ratio, 3),
        }


@dataclass
class Comparison:
    left: Side
    right: Side
    # 오른쪽에만 있는 자리. 새 설정이 더 잡은 것
    only_right: list[tuple[float, float]] = field(default_factory=list)
    # 왼쪽에만 있는 자리. 새 설정이 놓친 것
    only_left: list[tuple[float, float]] = field(default_factory=list)

    def summary(self) -> str:
        줄차이 = self.right.lines - self.left.lines
        비율차이 = (self.right.coverage - self.left.coverage) * 100
        망가짐차이 = self.right.broken - self.left.broken
        return (
            f"줄 {줄차이:+d}, 잡힌 시간 {비율차이:+.1f}%p, "
            f"망가진 줄 {망가짐차이:+d}. "
            f"새로 잡은 곳 {len(self.only_right)}군데, 놓친 곳 {len(self.only_left)}군데."
        )

    def to_view(self) -> dict[str, Any]:
        return {
            "left": self.left.to_view(),
            "right": self.right.to_view(),
            "only_right": [
                {"start": round(a, 2), "end": round(b, 2)} for a, b in self.only_right[:50]
            ],
            "only_left": [
                {"start": round(a, 2), "end": round(b, 2)} for a, b in self.only_left[:50]
            ],
            "only_right_count": len(self.only_right),
            "only_left_count": len(self.only_left),
            "summary": self.summary(),
        }


def side(label: str, segments: list[dict[str, Any]], duration_sec: float) -> Side:
    return Side(
        label=label,
        lines=len(segments),
        spoken_sec=_spoken(segments),
        broken=len(garbage.find_broken(segments)),
        duration_sec=duration_sec,
    )


def only_in(
    이쪽: list[dict[str, Any]], 저쪽: list[dict[str, Any]]
) -> list[tuple[float, float]]:
    """이쪽에만 있고 저쪽에는 겹치는 줄이 없는 자리.

    줄마다 저쪽 **전부**를 훑으면 2시간짜리에서 수천 × 수천이 되어 7초가 걸린다.
    저쪽을 시각 순으로 세워 두고 겹칠 수 있는 자리만 본다. 어느 줄도 가장 긴
    줄보다 더 앞에서 시작할 수는 없으므로, 그만큼만 뒤로 돌아보면 된다.
    """
    저쪽구간 = sorted(_span(s) for s in 저쪽)
    if not 저쪽구간:
        return [(round(_span(줄)[0], 2), round(_span(줄)[1], 2)) for 줄 in 이쪽]

    시작들 = [a for a, _ in 저쪽구간]
    가장긴줄 = max(b - a for a, b in 저쪽구간)

    남는것 = []
    for 줄 in 이쪽:
        내구간 = _span(줄)
        왼쪽 = bisect.bisect_left(시작들, 내구간[0] - 가장긴줄)
        오른쪽 = bisect.bisect_right(시작들, 내구간[1])
        if not any(_overlaps(내구간, 저쪽구간[i]) for i in range(왼쪽, 오른쪽)):
            남는것.append((round(내구간[0], 2), round(내구간[1], 2)))
    return 남는것


def compare(
    left_label: str,
    left: list[dict[str, Any]],
    right_label: str,
    right: list[dict[str, Any]],
    duration_sec: float,
) -> Comparison:
    """두 결과를 견준다. 판정하지 않고 숫자만 낸다."""
    return Comparison(
        left=side(left_label, left, duration_sec),
        right=side(right_label, right, duration_sec),
        only_right=only_in(right, left),
        only_left=only_in(left, right),
    )
