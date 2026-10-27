"""사람이 검수에서 **고친 일본어**를 모은다 — 받아쓰기가 실제로 틀린 자리다.

## 왜

받아쓰기가 어떤 말을 어떤 말로 잘못 듣는지는 머리로 지어낼 수 없다. 사용자가
검수 화면에서 일본어를 고칠 때마다 「고치기 전 → 고친 뒤」 가 생기는데, 그것이
**진짜 틀린 목록**이다. 여태 고치고 나면 사라졌다.

모아 두면 거절 단서에 「전에 「X」 를 「Y」 로 고친 적 있음」 을 띄울 수 있다.
같은 성우·같은 낱말에서 받아쓰기는 같은 실수를 되풀이한다.

## 줄째로 담지 않는다

달라진 **조각만** 담는다. 줄 전체를 쌓으면 대사가 통째로 설정 폴더에 남는다.
그리고 조각이어야 다른 줄에서 다시 찾을 수 있다.

## AI 로 안 나간다

거절 단서에만 쓴다. 묶음을 짜는 쪽은 이것을 모른다.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

# 조각이 이보다 짧으면 앞뒤를 붙여 늘린다. 한 글자로는 딴 줄에서 아무 데나 걸린다
최소길이 = 2
# 이보다 긴 조각은 고친 것이 아니라 다시 쓴 것이다. 담지 않는다
최대길이 = 12


def 길() -> Path:
    from app.core import settings as settings_store

    return settings_store.config_dir() / "받아쓰기고침.json"


def _읽기() -> dict[str, int]:
    try:
        것 = json.loads(길().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(것, dict):
        return {}
    return {str(k): int(v) for k, v in 것.items() if isinstance(v, int) and "\t" in str(k)}


def _쓰기(것: dict[str, int]) -> None:
    자리 = 길()
    자리.parent.mkdir(parents=True, exist_ok=True)
    임시 = 자리.with_suffix(".tmp")
    임시.write_text(json.dumps(것, ensure_ascii=False, indent=0), encoding="utf-8")
    임시.replace(자리)


def 조각들(전: str, 뒤: str) -> list[tuple[str, str]]:
    """두 글에서 **바뀐 조각**을 `(틀린 것, 고친 것)` 으로.

    짧으면 앞뒤 같은 글자를 붙여 `최소길이` 까지 늘린다 — `犯` → `起` 한
    글자보다 `犯し` → `起こし` 가 딴 줄에서 다시 찾을 값어치가 있다.
    """
    난것 = []
    재기 = difflib.SequenceMatcher(None, 전, 뒤, autojunk=False)
    for 일, i1, i2, j1, j2 in 재기.get_opcodes():
        if 일 != "replace":
            continue
        while i2 - i1 < 최소길이 and (i1 > 0 or i2 < len(전)):
            if i2 < len(전) and j2 < len(뒤) and 전[i2] == 뒤[j2]:
                i2 += 1
                j2 += 1
            elif i1 > 0 and j1 > 0 and 전[i1 - 1] == 뒤[j1 - 1]:
                i1 -= 1
                j1 -= 1
            else:
                break
        틀린, 고친 = 전[i1:i2].strip(), 뒤[j1:j2].strip()
        if 최소길이 <= len(틀린) <= 최대길이 and 고친 and len(고친) <= 최대길이:
            난것.append((틀린, 고친))
    return 난것


def 적기(전: str, 뒤: str) -> int:
    """고친 것 하나를 모은다. 담은 조각 수. **실패해도 고치기는 멈추지 않는다.**"""
    새것 = 조각들(전, 뒤)
    if not 새것:
        return 0
    try:
        모은것 = _읽기()
        for 틀린, 고친 in 새것:
            열쇠 = f"{틀린}\t{고친}"
            모은것[열쇠] = 모은것.get(열쇠, 0) + 1
        _쓰기(모은것)
    except OSError:
        return 0
    짝들.cache_clear()
    return len(새것)


def _짝들() -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (*열쇠.split("\t", 1), 수)
        for 열쇠, 수 in sorted(_읽기().items(), key=lambda x: -x[1])
    )


class _담개:
    """파일을 매번 읽지 않는다. 고칠 때(`적기`) 비운다.

    **자리도 같이 본다.** 설정 폴더가 바뀌면(시험마다 다르다) 옛 폴더의 것을
    그대로 내주면 안 된다.
    """

    def __init__(self) -> None:
        self._자리: Path | None = None
        self._것: tuple[tuple[str, str, int], ...] = ()

    def __call__(self) -> tuple[tuple[str, str, int], ...]:
        자리 = 길()
        if self._자리 != 자리:
            self._것 = _짝들()
            self._자리 = 자리
        return self._것

    def cache_clear(self) -> None:
        self._자리 = None


# `((틀린 것, 고친 것, 몇 번), ...)` — 많이 고친 것부터
짝들 = _담개()
