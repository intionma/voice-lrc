"""세그먼트와 한국어 번역을 합쳐 .lrc 자막을 만든다.

LRC에는 "언제 지울지"가 없다. 한 줄이 뜨면 다음 줄이 나올 때까지 화면에 남는다.
그래서 대사 사이가 오래 비면 빈 줄을 하나 끼워 자막을 지운다. 안 그러면 아무도
말하지 않는 구간에 한참 전 문장이 계속 떠 있는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

# 다음 대사까지 이만큼 비면 빈 줄을 넣어 자막을 지운다.
#
# 예전에는 4초였는데, 3.9초를 쉬어도 그동안 자막이 그대로 떠 있어서 "말이 끝났는데
# 자막이 계속 남는다"는 소리를 들었다. 사람이 한 호흡 쉬는 정도(1.5초)를 넘기면
# 지우는 편이 실제 느낌에 맞다.
GAP_CLEAR_SEC = 1.5

# 자막을 지우기 전에 주는 여유.
# 받아적은 끝 시각은 실제로 말이 끝나는 것보다 조금 이르게 잡힐 때가 있다.
# 딱 맞춰 지우면 마지막 글자를 읽기 전에 사라진다.
HOLD_SEC = 0.35

# 한 줄이 아무리 길어도 이보다 오래 떠 있게 두지 않는다.
#
# 로그에 「자막 한 줄이 858초 동안 떠 있습니다」 가 찍혀 있었다. 받아쓰기가
# 무음 구간을 통째로 문 것인데, 그러면 14분 내내 한 문장이 화면에 붙어 있는다.
# 말은 벌써 끝났고 다음 대사는 한참 뒤다.
#
# 나눌 수 있는 것은 `segments.split_long` 이 나눈다. 여기서 다루는 것은
# **글자는 짧은데 시간만 긴 줄**이라 나눠 봐야 소용이 없는 경우다. 읽을 만큼
# 띄워 두고 지운다.
MAX_SHOW_SEC = 12.0


def format_timestamp(seconds: float) -> str:
    """LRC 시간 형식 `[mm:ss.cc]`. 1시간이 넘어도 분으로 계속 센다."""
    centiseconds = max(0, round(seconds * 100))
    minutes, rest = divmod(int(centiseconds), 6000)
    secs, cs = divmod(rest, 100)
    return f"[{minutes:02d}:{secs:02d}.{cs:02d}]"


def build_entries(
    segments: Iterable[dict[str, Any]],
    translation: dict[int, str],
    *,
    gap_clear_sec: float = GAP_CLEAR_SEC,
    hold_sec: float = HOLD_SEC,
    offset_sec: float = 0.0,
    max_show_sec: float = MAX_SHOW_SEC,
) -> list[tuple[float, str]]:
    """`(시각, 문장)` 목록을 만든다. 문장이 빈 항목은 자막을 지우라는 뜻이다."""
    items = list(segments)
    entries: list[tuple[float, str]] = []

    def 다음번역줄(position: int) -> dict[str, Any] | None:
        """다음으로 **화면에 뜰** 줄. 번역이 없는 줄은 자막이 되지 않는다.

        바로 다음 줄만 보면, 그 줄이 번역되지 않았을 때 자막이 그 자리에 그대로
        떠 있는다. AI 가 몇 줄을 빠뜨렸거나 건너뛴 묶음이 있으면 "말이 끝났는데
        자막이 계속 남는" 그 증상이 그대로 돌아온다.
        """
        for 뒤 in items[position + 1 :]:
            if translation.get(뒤["index"], "").strip():
                return 뒤
        return None

    for position, segment in enumerate(items):
        text = translation.get(segment["index"], "").strip()
        if not text:
            continue  # 번역이 없는 줄은 건너뛴다. 시간만 남기면 빈 자막이 뜬다

        entries.append((max(0.0, segment["start"] + offset_sec), text))

        following = 다음번역줄(position)
        # 이 줄이 언제까지 화면에 있어도 되는지. 받아쓰기가 무음을 통째로 물면
        # `end` 가 14분 뒤일 수 있다. 그때까지 붙들어 두면 안 된다
        늦어도 = min(float(segment["end"]), float(segment["start"]) + max_show_sec)
        너무김 = 늦어도 < float(segment["end"]) - 0.001

        if 너무김 or following is None or following["start"] - 늦어도 >= gap_clear_sec:
            # 다음 대사보다 먼저 지워야 한다. 여유를 주다 다음 줄을 덮으면 안 된다
            지울때 = 늦어도 + hold_sec
            if following is not None:
                지울때 = min(지울때, following["start"] - 0.01)
            지울때 = max(지울때, 늦어도)
            entries.append((max(0.0, 지울때 + offset_sec), ""))

    entries.sort(key=lambda item: item[0])
    return _drop_repeated_blanks(entries)


def _drop_repeated_blanks(entries: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """빈 줄이 연달아 나오면 첫 것만 남긴다. 두 번 지울 필요는 없다."""
    cleaned: list[tuple[float, str]] = []
    for at, text in entries:
        if not text and cleaned and not cleaned[-1][1]:
            continue
        cleaned.append((at, text))
    return cleaned


def _한줄로(글: str) -> str:
    """줄바꿈을 없앤다.

    LRC 는 한 줄에 `[시각]문장` 하나다. 문장 안에 줄바꿈이 들어가면 그 뒤는
    **시각이 없는 줄**이 되어 재생기가 통째로 뱉거나 자막이 밀린다.

    손으로 고치는 칸에 여러 줄을 붙여넣으면 실제로 이렇게 된다.
    """
    토막 = str(글).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return " ".join(t.strip() for t in 토막 if t.strip())


def _제목다듬기(title: str) -> str:
    """`[ti:...]` 안에 넣어도 안전하게.

    작품 제목에는 `[期間限定]` 처럼 대괄호가 흔하다. 그대로 넣으면 태그가
    거기서 닫힌 것처럼 읽힌다.
    """
    다듬은것 = _한줄로(title).replace("[", "(").replace("]", ")")
    return 다듬은것[:200]


def render(entries: Iterable[tuple[float, str]], *, title: str = "") -> str:
    lines = []
    깨끗한제목 = _제목다듬기(title)
    if 깨끗한제목:
        lines.append(f"[ti:{깨끗한제목}]")
    lines.append("[re:trans-text]")
    lines += [f"{format_timestamp(at)}{_한줄로(text)}" for at, text in entries]
    return "\n".join(lines) + "\n"


def write(path: Path, entries: Iterable[tuple[float, str]], *, title: str = "") -> Path:
    """자막을 저장한다. BOM 없는 UTF-8로 쓴다.

    임시 이름으로 다 쓴 뒤 한 번에 바꾼다. 12분을 받아쓰고 번역까지 한 결과인데,
    쓰다가 디스크가 차거나 전원이 나가면 **반쪽짜리 자막이 남는다.** 그러면
    사용자는 자막이 있는 줄 알고 열었다가 중간부터 없는 것을 보게 된다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    글 = render(entries, title=title)
    임시 = path.with_name(path.name + ".tmp")
    try:
        임시.write_text(글, encoding="utf-8")
        임시.replace(path)
    except OSError:
        임시.unlink(missing_ok=True)
        raise
    return path


def output_path_for(audio: Path) -> Path:
    """음원 옆에 같은 이름으로 놓는다. `RJ123456.mp3` → `RJ123456.lrc`"""
    return audio.with_suffix(".lrc")


def can_write_next_to(audio: Path) -> bool:
    """음원이 있는 폴더에 쓸 수 있는지 미리 본다.

    읽기 전용 드라이브나 NAS면 저장이 실패한다. 실패한 뒤에 알면 늦으므로
    미리 확인하고 다른 자리에 저장할 수 있게 한다.
    """
    folder = audio.parent
    probe = folder / f".trans-text-write-test-{audio.stem[:20]}"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False
