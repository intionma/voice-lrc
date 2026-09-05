"""자막이 제대로 나왔는지 스스로 검사한다.

이 컨테이너에는 GPU도 음원도 없어서, 받아쓰기가 잘 됐는지 만든 사람이 확인할
방법이 없다. 그래서 프로그램이 자기 결과를 재서 사용자에게 보여 준다.

"대사가 누락됐다"는 말을 들었을 때 어디가 비었는지 추측하지 않고 볼 수 있어야
한다. 예전에 같은 오류로 여러 번 왕복한 원인이 이것이었다.

여기서는 재기만 하고 고치지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core import minor_terms

# 이 이상 말이 안 잡히면 짚어 준다. 진짜 조용한 구간일 수도 있다
SUSPICIOUS_GAP_SEC = 20.0

# 자막 한 줄이 이보다 오래 떠 있으면 뭔가 잘못됐을 가능성이 높다
LONG_LINE_SEC = 12.0

# 이보다 잡힌 시간이 적으면 VAD가 버렸을 수 있다
LOW_COVERAGE = 0.35

# 같은 문장이 이만큼 연달아 나오면 환각이다
REPEAT_RUN = 4

# 이보다 자신 없는 줄은 잘못 받아적었을 가능성이 크다
LOW_CONFIDENCE = -1.0

# 같은 종류를 이만큼까지만 늘어놓는다. 나머지는 몇 개인지만 알려 준다
MAX_SAME_KIND = 20

# 등장인물이 미성년으로 설정된 작품을 짚는 자리.
#
# 낱말 목록은 `minor_terms.py` 로 뺐다. **사용자가 끄고 더할 수 있어야 한다** —
# `少年` 처럼 헛걸림이 잦은 말이 매번 뜨면 사람이 ⚠ 를 아예 안 보게 되고,
# 그러면 진짜 걸려야 할 때 못 잡는다.
#
# 일본어를 모르면 상품 설명만 보고는 알 수 없어서, 모르고 넣는 일이 생긴다.
# 받아쓰기가 끝난 시점은 아직 아무 데도 보내기 전이라 여기서 잡는 것이 맞다.
# 이 상태로 번역을 맡기면 거절당하는 데서 끝나지 않고 키나 계정이 정지된다.
#
# 이것은 판정이 아니라 **확인하라는 신호**다.

# 약한말은 이만큼 겹쳐야 짚는다. 하나로는 아무 뜻이 없다
약한말겹침 = 2

# 나이를 세는 말. `歲` 는 `歳` 의 옛 글자고, `ｻｲ` 는 반각이다. 받아쓰기가 어느
# 글자로 뱉을지 우리가 못 정하니 다 받는다
_나이꼬리 = r"(?:歳|歲|才|さい|サイ|ｻｲ)"

# **앞에 숫자가 더 있으면 안 된다.** `\d{1,2}` 만 두면 `100歳` 에서 뒤의 `00`
# 만 떼어다 「0살」로 읽었다. 이 바닥에는 수백 살 먹은 인물이 흔해서, 나이를
# 크게 말하는 성인물이 오히려 미성년으로 걸렸다
_AGE = re.compile(rf"(?<!\d)(\d{{1,2}})\s*{_나이꼬리}")

# 한자로 쓴 나이. 낱글자 값이다
_한자숫자 = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}

# 한자 나이. **앞에 다른 숫자 한자가 붙어 있으면 안 된다.**
#
# 예전에는 "八歳" 가 글 안에 있는지만 봤다. 그래서 `十八歳`(18살) 안의 `八歳` 가
# 걸려서 **어른이 미성년으로 잡혔다.** `二十歳`(20살) 는 `十歳` 가 걸렸고,
# `十九才`(19살) 는 `九歳` 가 걸렸다. 성인물을 다루는 도구인데 나이를 한자로
# 말하는 작품마다 빨간 경고가 떴다.
_한자나이 = re.compile(
    rf"(?<![一二三四五六七八九十])([一二三四五六七八九十]{{1,3}})\s*{_나이꼬리}")

# 숫자까지 가나로 말한 나이 (`じゅうななさい`). 표는 `minor_terms` 에 있다.
_가나나이표 = minor_terms.가나나이표()

# `じゅうはっさい`(18살) 안의 `はっさい`(8살) 가 안 걸리는 것은 **표에 18살이
# 있어서**다. 정규식은 왼쪽부터 훑으면서 맞은 만큼 건너뛰므로, 첫 자리에서
# 18살이 통째로 물리면 안쪽 8살은 볼 일이 없다. 늘어놓는 차례는 상관없다 —
# 어떤 읽기도 딴 읽기의 앞머리가 아니라서 한 자리에서 둘이 맞는 일이 없다
# (`test_어떤_읽기도_딴_읽기의_앞머리가_아니다` 가 지킨다).
#
# **그러니 표에서 어른 나이를 빼면 안 된다.** 한자에서 `十八歳` 안의 `八歳` 가
# 걸리던 것과 똑같은 사고가 난다
_가나나이 = re.compile("|".join(re.escape(말) for 말 in sorted(_가나나이표)))

# 이 중 몇은 흔한 딴 말 속에 묻어 나온다 (`にさい` ← 「〜に最高」). 빼기에는
# 아깝고 혼자 짚기에는 위험해서 약한말로 센다
_헷갈리는나이말 = minor_terms.헷갈리는나이말()


def _한자를수로(글: str) -> int | None:
    """`十七` → 17. `二十` → 20. 못 읽으면 None."""
    if "十" not in 글:
        return _한자숫자.get(글) if len(글) == 1 else None
    앞, _, 뒤 = 글.partition("十")
    십의자리 = 1 if not 앞 else _한자숫자.get(앞)
    일의자리 = 0 if not 뒤 else _한자숫자.get(뒤)
    if 십의자리 is None or 일의자리 is None:
        return None
    return 십의자리 * 10 + 일의자리


@dataclass
class Finding:
    """짚어 줄 것 하나."""

    kind: str
    message: str
    at_sec: float = 0.0
    severity: str = "확인"  # 확인 / 주의

    @property
    def at(self) -> str:
        total = int(max(0.0, self.at_sec))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"


@dataclass
class Report:
    duration_sec: float
    line_count: int
    spoken_sec: float
    translated_count: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """전체 길이 중 말이 잡힌 비율."""
        return min(1.0, self.spoken_sec / self.duration_sec) if self.duration_sec else 0.0

    @property
    def translated_ratio(self) -> float:
        return self.translated_count / self.line_count if self.line_count else 1.0

    @property
    def ok(self) -> bool:
        return not any(f.severity == "주의" for f in self.findings)

    @property
    def minor_suspected(self) -> bool:
        """미성년 설정으로 의심되는가.

        이러면 자동으로 번역을 맡기지 않는다. 사람이 보고 정할 일이다.
        """
        return any(f.kind == "미성년의심" for f in self.findings)

    def summary(self) -> str:
        """한 줄로 요약. 화면에 그대로 띄운다."""
        if self.ok:
            return f"자막 {self.line_count}줄. 말이 잡힌 시간 {self.coverage:.0%}."
        주의 = sum(1 for f in self.findings if f.severity == "주의")
        return (
            f"자막 {self.line_count}줄, 말이 잡힌 시간 {self.coverage:.0%}. "
            f"확인할 곳 {주의}군데."
        )


def inspect(
    segments: list[dict[str, Any]],
    duration_sec: float,
    *,
    translation: dict[int, str] | None = None,
    work: Any = None,
    uncovered: list[tuple[float, float]] | None = None,
) -> Report:
    """받아쓰기와 번역 결과를 재서 짚어 줄 것을 모은다."""
    translation = translation or {}
    report = Report(
        duration_sec=duration_sec,
        line_count=len(segments),
        spoken_sec=sum(float(s["end"]) - float(s["start"]) for s in segments),
        translated_count=sum(1 for s in segments if translation.get(s["index"], "").strip()),
    )

    if not segments:
        report.findings.append(
            Finding("빈결과", "말을 하나도 잡지 못했습니다.", severity="주의")
        )
        return report

    _check_uncovered(report, uncovered or [])
    _check_broken(report, segments)
    _check_minor_setting(report, segments, work)
    _check_coverage(report)
    _check_gaps(report, segments)
    _check_long_lines(report, segments)
    _check_repeats(report, segments)
    _check_confidence(report, segments)
    _check_translation(report, segments, translation)
    report.findings.sort(key=lambda f: f.at_sec)
    return report


def find_minor_hints(
    segments: list[dict[str, Any]], 목록: dict | None = None,
) -> list[tuple[float, str, str]]:
    """미성년 설정이 의심되는 자리를 모은다.

    `(시각, 걸린 말, 그 줄 글)` 목록. 판정이 아니라 사람이 확인할 자리다.

    **줄 글을 같이 준다.** 낱말만 보여 주면 판단할 근거가 없다 —
    「少年」 하나로는 성인 남성을 그렇게 부른 것인지 알 수가 없고, 사용자는
    일본어를 몰라서 되짚어 볼 수도 없다.
    """
    목록 = 목록 or minor_terms.목록읽기()
    강한말, 약한말 = minor_terms.쓸말(목록)
    나이한도 = int(목록.get("나이") or minor_terms.기본나이)
    볼말 = [*강한말, *약한말]

    걸린것: list[tuple[float, str, str]] = []
    for segment in segments:
        text = str(segment.get("ja", ""))
        at = float(segment.get("start", 0.0))

        for term in 볼말:
            if term in text:
                걸린것.append((at, term, text))

        for 숫자 in _AGE.findall(text):
            if int(숫자) < 나이한도:
                걸린것.append((at, f"{숫자}歳", text))

        for 한자 in _한자나이.findall(text):
            나이 = _한자를수로(한자)
            if 나이 is not None and 나이 < 나이한도:
                걸린것.append((at, f"{한자}歳", text))

        for 읽기 in _가나나이.findall(text):
            if _가나나이표[읽기] < 나이한도:
                # 헷갈리는 것은 읽은 그대로 둔다. `짚을까` 가 그걸 보고
                # 약한말로 센다 — 딴 것과 겹쳐야 뜬다
                걸린것.append((
                    at,
                    읽기 if 읽기 in _헷갈리는나이말 else f"{_가나나이표[읽기]}歳",
                    text,
                ))

    return 걸린것


def 짚을까(걸린말: set[str], 태그: list[str], 목록: dict) -> bool:
    """이만큼 걸렸으면 짚을 값어치가 있나.

    **같은 무게로 두면 낱말을 늘릴수록 헛걸림만 는다.** `ランドセル` 은 혼자서도
    거의 확정이지만 `少年` 은 성인 남성한테도 쓴다. 그런 것이 매번 뜨면 사람이
    ⚠ 를 아예 안 보게 되고, 그러면 진짜 걸려야 할 때 못 잡는다.

    나이는 말한 그대로라 **혼자서도 짚는다.**
    """
    if 태그:
        return True
    강한말, 약한말 = minor_terms.쓸말(목록)
    if 걸린말 & set(강한말):
        return True
    if any(말.endswith(("歳", "才")) for 말 in 걸린말):
        return True
    # 가나로만 말한 나이 중 딴 말과 헷갈리는 것은 여기서 약한말로 센다
    return len(걸린말 & (set(약한말) | _헷갈리는나이말)) >= 약한말겹침


def _check_minor_setting(
    report: Report, segments: list[dict[str, Any]], work: Any = None
) -> None:
    """미성년 설정으로 보이면 번역을 맡기기 전에 짚는다.

    일본어를 모르면 상품 설명만 보고는 알 수 없다. 이 상태로 번역을 맡기면
    거절당하는 데서 끝나지 않고 API 키나 계정이 정지된다. 지금은 아직 아무 데도
    보내기 전이라 여기서 잡는 것이 맞다.
    """
    목록 = minor_terms.목록읽기()
    걸린것 = find_minor_hints(segments, 목록)

    # 상품 태그가 대사보다 정확하다. 나이를 말로 안 하는 작품이 태반이고,
    # 받아쓰기가 틀리면 대사 쪽은 아예 못 본다
    태그 = list(getattr(work, "minor_genres", []) or [])
    걸린말 = {term for _, term, _ in 걸린것}
    if not 짚을까(걸린말, 태그, 목록):
        return

    말 = sorted(걸린말 | set(태그))
    처음 = min((at for at, _, _ in 걸린것), default=0.0)
    보기 = next((줄글 for _, _, 줄글 in 걸린것), "")
    report.findings.append(
        Finding(
            "미성년의심",
            f"등장인물이 미성년으로 설정된 작품일 수 있습니다 ({', '.join(말[:5])})."
            + (f" 예: 「{보기[:40]}」" if 보기 else "")
            + " 맞다면 번역을 맡기지 마세요. 거절당하는 데서 끝나지 않고 "
            "API 키나 계정이 정지될 수 있습니다.",
            at_sec=처음,
            severity="주의",
        )
    )


def _check_uncovered(
    report: Report, uncovered: list[tuple[float, float]]
) -> None:
    """소리는 잡혔는데 받아쓴 줄이 없는 자리.

    이것은 "자막이 좀 이상하다" 가 아니라 **대사가 통째로 없다** 는 뜻이라
    가장 위에 보여 준다. 다만 판정이 아니라 확인하라는 신호다. 관대한 VAD 는
    BGM 과 숨소리도 소리로 잡는다.
    """
    if not uncovered:
        return
    for 시작, 끝 in uncovered[:MAX_SAME_KIND]:
        report.findings.append(
            Finding(
                "안잡힘",
                f"말소리는 있는데 받아쓴 줄이 없습니다 ({끝 - 시작:.1f}초). 들어 보세요.",
                at_sec=시작,
                severity="주의",
            )
        )
    if len(uncovered) > MAX_SAME_KIND:
        report.findings.append(
            Finding(
                "안잡힘",
                f"이런 곳이 {len(uncovered) - MAX_SAME_KIND}군데 더 있습니다.",
                at_sec=uncovered[MAX_SAME_KIND][0],
                severity="주의",
            )
        )


def _check_broken(report: Report, segments: list[dict[str, Any]]) -> None:
    """받아쓰기가 실패한 줄을 짚는다.

    번역으로 넘어가면 AI 가 헛소리를 그럴듯한 한국어로 바꿔 준다. 자막을 켜면
    대사와 전혀 맞지 않는다. 재료가 썩으면 누가 요리해도 안 된다.
    """
    from app.core import garbage

    망가진것 = garbage.find_broken(segments)
    # 통째로 망가진 파일이면 수백 줄이 나온다. 그대로 쌓으면 검사표가 도배되고
    # 진단 파일도 그것만으로 가득 찬다. 앞의 것만 보여 주고 나머지는 세어 준다
    for 줄 in 망가진것[:MAX_SAME_KIND]:
        report.findings.append(
            Finding(
                "망가짐",
                f"받아쓰기가 실패한 것 같습니다 ({줄['why']}).",
                at_sec=줄["start"],
                severity="주의",
            )
        )
    if len(망가진것) > MAX_SAME_KIND:
        report.findings.append(
            Finding(
                "망가짐",
                f"이런 줄이 {len(망가진것) - MAX_SAME_KIND}줄 더 있습니다. "
                "받아쓰기를 다시 하는 편이 낫습니다.",
                at_sec=망가진것[MAX_SAME_KIND]["start"],
                severity="주의",
            )
        )

    for 시작, 끝 in garbage.find_broken_spans(segments):
        report.findings.append(
            Finding(
                "망가진구간",
                f"{_hms(시작)}부터 {_hms(끝)}까지가 통째로 망가졌습니다. "
                "그 구간만 다시 받아쓰는 편이 낫습니다.",
                at_sec=시작,
                severity="주의",
            )
        )


def _check_coverage(report: Report) -> None:
    if report.duration_sec and report.coverage < LOW_COVERAGE:
        report.findings.append(
            Finding(
                "적게잡힘",
                f"전체 {_hms(report.duration_sec)} 중 말이 잡힌 시간이 "
                f"{report.coverage:.0%}뿐입니다. 속삭임이 버려졌을 수 있습니다.",
                severity="주의",
            )
        )


def _check_gaps(report: Report, segments: list[dict[str, Any]]) -> None:
    cursor = 0.0
    for segment in segments:
        start = float(segment["start"])
        if start - cursor >= SUSPICIOUS_GAP_SEC:
            report.findings.append(
                Finding(
                    "빈구간",
                    f"{_hms(cursor)}부터 {_hms(start)}까지 "
                    f"{int(start - cursor)}초 동안 말이 없습니다.",
                    at_sec=cursor,
                    severity="주의",
                )
            )
        cursor = max(cursor, float(segment["end"]))

    if report.duration_sec - cursor >= SUSPICIOUS_GAP_SEC:
        report.findings.append(
            Finding(
                "빈구간",
                f"마지막 {int(report.duration_sec - cursor)}초 동안 말이 없습니다.",
                at_sec=cursor,
                severity="주의",
            )
        )


def _check_long_lines(report: Report, segments: list[dict[str, Any]]) -> None:
    긴것 = [s for s in segments if float(s["end"]) - float(s["start"]) >= LONG_LINE_SEC]
    for segment in 긴것[:MAX_SAME_KIND]:
        report.findings.append(
            Finding(
                "긴줄",
                f"자막 한 줄이 {int(float(segment['end']) - float(segment['start']))}초 "
                "동안 떠 있습니다.",
                at_sec=float(segment["start"]),
            )
        )
    if len(긴것) > MAX_SAME_KIND:
        report.findings.append(
            Finding(
                "긴줄",
                f"이런 줄이 {len(긴것) - MAX_SAME_KIND}줄 더 있습니다.",
                at_sec=float(긴것[MAX_SAME_KIND]["start"]),
            )
        )


def _check_repeats(report: Report, segments: list[dict[str, Any]]) -> None:
    """같은 문장이 연달아 나오면 환각이다."""
    run_start, run_len = 0, 1
    for position in range(1, len(segments) + 1):
        같음 = (
            position < len(segments)
            and segments[position]["ja"].strip() == segments[position - 1]["ja"].strip()
        )
        if 같음:
            run_len += 1
            continue
        if run_len >= REPEAT_RUN:
            report.findings.append(
                Finding(
                    "반복",
                    f"같은 말이 {run_len}번 연달아 나옵니다. 없는 말을 지어냈을 수 있습니다.",
                    at_sec=float(segments[run_start]["start"]),
                    severity="주의",
                )
            )
        run_start, run_len = position, 1


def _check_confidence(report: Report, segments: list[dict[str, Any]]) -> None:
    낮은줄 = [s for s in segments if float(s.get("avg_logprob", 0.0)) < LOW_CONFIDENCE]
    if not 낮은줄:
        return
    비율 = len(낮은줄) / len(segments)
    if 비율 >= 0.2:
        report.findings.append(
            Finding(
                "자신없음",
                f"{len(낮은줄)}줄({비율:.0%})을 자신 없게 받아적었습니다. "
                "잘못 들었을 수 있으니 번역할 때 문맥으로 고쳐야 합니다.",
                at_sec=float(낮은줄[0]["start"]),
            )
        )


def _check_translation(
    report: Report, segments: list[dict[str, Any]], translation: dict[int, str]
) -> None:
    if not translation:
        return
    빠진줄 = [s for s in segments if not translation.get(s["index"], "").strip()]
    if 빠진줄:
        report.findings.append(
            Finding(
                "번역없음",
                f"{len(빠진줄)}줄이 번역되지 않아 자막에서 빠집니다.",
                at_sec=float(빠진줄[0]["start"]),
                severity="주의",
            )
        )


def _hms(seconds: float) -> str:
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"
