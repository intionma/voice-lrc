"""작품과 트랙 제목을 한국어로 옮기고, 음원 파일 이름도 바꾼다.

설계는 [docs/18_TITLE_TRANSLATION.md](../../docs/18_TITLE_TRANSLATION.md) 에
있다. 여기서는 그중 **틀리면 사용자 파일이 망가지는 것들**을 지킨다.

## 번호를 9000번대로 띄운다

자막 묶음은 「번호<탭>일본어」 로 줄 번호가 자막 줄과 1:1 이다. 거기에 제목을
끼워 넣으면 번호가 밀려서 자막이 통째로 어긋난다.

그래서 제목은 **9001번부터, 작품마다 100씩 띄운다.**

    작품 1번째   9001(작품 제목) · 9002~9099(트랙)
    작품 2번째   9101(작품 제목) · 9102~9199(트랙)

작품 전부를 한 덩어리로 뽑아 밖의 AI 에 던지고, 답이 섞여 돌아와도 각 줄이
제 작품을 찾아간다. 사용자가 「몇 번 작품 것입니다」 를 고를 일이 없다.

트랙이 99개를 넘으면 번호대가 겹친다. 실제로 그럴 일은 없다고 보지만,
**소리 없이 겹치게 두지 않는다.** 넘는 작품은 빼고 그렇다고 말한다.

## 파일 이름은 누를 때만 바꾼다

번역을 넣어도 파일은 그대로다. 이 앱이 사용자의 파일 이름을 바꾸는 자리는
여기 하나뿐이고, 단추를 눌러야만 움직인다.

## 절반만 바뀌는 것이 제일 나쁘다

다른 앱이 파일을 열어 두고 있으면 이름 바꾸기가 실패한다. 일곱 개 중 셋만
바뀌면 자막과 음원의 짝이 어긋난 채로 남는다. **전부 되는지 먼저 보고, 되면
한꺼번에 한다.**

## 받아쓴 것을 잃지 않는다

받아쓰기 캐시의 열쇠가 **파일 경로**다. 이름만 바꾸고 열쇠를 두면 3시간짜리를
처음부터 다시 받아쓰게 된다. 사용자가 시간을 가장 많이 쓴 부분이라 여기서
잃으면 안 된다. `.lrc` 도 이름이 같아야 짝이 맞으므로 같이 따라간다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.core import log
from app.core import settings as settings_store

# 제목이 쓰는 번호대. 자막 줄 번호와 절대 겹치지 않을 만큼 위로 띄운다
첫번호 = 9001

# 작품 하나가 쓰는 번호 폭. 작품 제목 하나 + 트랙 99개
작품폭 = 100

# 한 작품에서 다룰 수 있는 트랙 수. 넘으면 번호대가 겹친다
트랙한도 = 작품폭 - 1

# 윈도우가 파일 이름에 못 쓰는 글자
못쓰는글자 = '\\/:*?"<>|'

# 윈도우 예약어. 확장자를 붙여도 못 쓴다
예약어 = {"CON", "PRN", "AUX", "NUL"} | {
    f"{앞}{n}" for 앞 in ("COM", "LPT") for n in range(1, 10)
}

# 이름 하나의 글자 한도. 경로 260자 제한에 걸리지 않게 넉넉히 줄인다
이름한도 = 120


@dataclass
class 제목묶음:
    """한 작품의 제목들. 번호를 매겨 내보내고 답을 받아 담는다."""

    열쇠: str
    작품이름: str
    트랙이름들: list[str] = field(default_factory=list)
    첫번호: int = 첫번호

    @property
    def 작품번호(self) -> int:
        return self.첫번호

    def 트랙번호(self, 몇번째: int) -> int:
        return self.첫번호 + 1 + 몇번째

    @property
    def 너무많나(self) -> bool:
        return len(self.트랙이름들) > 트랙한도

    def 줄들(self) -> list[tuple[int, str]]:
        """`(번호, 일본어)`. 작품 제목이 먼저, 그다음 트랙."""
        나온것 = [(self.작품번호, self.작품이름)]
        for 몇번째, 이름 in enumerate(self.트랙이름들):
            나온것.append((self.트랙번호(몇번째), 이름))
        return 나온것

    def 어느것(self, 번호: int) -> tuple[str, str] | None:
        """이 번호가 무엇을 가리키나. `("work", "")` 또는 `("track", 파일이름)`."""
        if 번호 == self.작품번호:
            return ("work", "")
        자리 = 번호 - self.첫번호 - 1
        if 0 <= 자리 < len(self.트랙이름들):
            return ("track", self.트랙이름들[자리])
        return None


def 번호매기기(작품들: Iterable[tuple[str, str, list[str]]]) -> list[제목묶음]:
    """`(열쇠, 작품이름, 트랙이름들)` 을 받아 번호를 매긴다.

    **트랙이 99개를 넘는 작품은 빼지 않고 담되 `너무많나` 로 표시한다.**
    소리 없이 빼면 사용자는 그 작품이 왜 안 나왔는지 모른다.
    """
    묶음들 = []
    for 몇번째, (열쇠, 이름, 트랙들) in enumerate(작품들):
        묶음들.append(제목묶음(
            열쇠=str(열쇠),
            작품이름=str(이름 or ""),
            트랙이름들=[str(t) for t in 트랙들],
            첫번호=첫번호 + 몇번째 * 작품폭,
        ))
    return 묶음들


def 내보낼글(묶음들: Iterable[제목묶음]) -> str:
    """밖의 AI 에 던질 「번호<탭>일본어」. 너무 많은 작품은 빼고 만든다."""
    줄들 = []
    for 묶음 in 묶음들:
        if 묶음.너무많나:
            continue
        for 번호, 글 in 묶음.줄들():
            if str(글).strip():
                줄들.append(f"{번호}\t{글}")
    return "\n".join(줄들)


def 나눠담기(
    받은것: dict[int, str], 묶음들: Iterable[제목묶음]
) -> dict[str, dict[str, str]]:
    """번호가 섞여 온 답을 작품별로 갈라 놓는다.

    `{열쇠: {"work": 한국어, "tracks": {파일이름: 한국어}}}`.

    번호대를 100씩 띄워 뒀으므로, 답이 아무 순서로 와도 각 줄이 제 작품을
    찾아간다. 사용자가 「몇 번 작품 것입니다」 를 고를 일이 없다.
    """
    나온것: dict[str, dict[str, Any]] = {}
    for 묶음 in 묶음들:
        for 번호, 글 in 받은것.items():
            무엇 = 묶음.어느것(int(번호))
            if 무엇 is None:
                continue
            다듬은것 = str(글).strip()
            if not 다듬은것:
                continue
            자리 = 나온것.setdefault(묶음.열쇠, {"work": "", "tracks": {}})
            종류, 파일이름 = 무엇
            if 종류 == "work":
                자리["work"] = 다듬은것
            else:
                자리["tracks"][파일이름] = 다듬은것
    return 나온것


def 일본어그대로인가(글들: Iterable[str]) -> bool:
    """번역이 아니라 원문이 그대로 돌아왔는가.

    제목은 작품 하나에 한 줄, 트랙 몇 줄이라 **거의 늘 여섯 줄이 안 된다.**
    자막 쪽 기준(`exchange.번역이_아닌가`)을 그대로 쓰면 한 번도 안 걸린다.
    """
    from app.core.exchange import 한국어가_아닌가

    return 한국어가_아닌가(글들)


# ---- 담아 두기 ----


def 담는곳() -> Path:
    return settings_store.config_dir() / "titles"


def _길(열쇠: str) -> Path:
    """작품 열쇠로 파일 자리를 만든다.

    열쇠가 품번이 아니라 **폴더 경로**일 수 있다. 그대로 파일 이름에 쓰면
    윈도우에서 만들지도 못한다.
    """
    안전 = re.sub(r"[^0-9A-Za-z가-힣_.-]", "_", str(열쇠))[:80] or "_"
    return 담는곳() / f"{안전}.json"


def 가져오기(열쇠: str) -> dict[str, Any]:
    """담아 둔 것. 없으면 빈 것."""
    빈것: dict[str, Any] = {"work": "", "tracks": {}, "renamed": False, "original": {}}
    길 = _길(열쇠)
    if not 길.is_file():
        return 빈것
    try:
        데이터 = json.loads(길.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 망가진 파일 때문에 제목 화면이 안 열리면 안 된다
        return 빈것
    if not isinstance(데이터, dict):
        return 빈것
    빈것.update({
        "work": str(데이터.get("work") or ""),
        "tracks": {
            str(k): str(v) for k, v in (데이터.get("tracks") or {}).items()
            if isinstance(v, str)
        },
        "renamed": bool(데이터.get("renamed")),
        "original": {
            str(k): str(v) for k, v in (데이터.get("original") or {}).items()
            if isinstance(v, str)
        },
    })
    return 빈것


def 담기(열쇠: str, 값: dict[str, Any]) -> None:
    길 = _길(열쇠)
    try:
        길.parent.mkdir(parents=True, exist_ok=True)
        임시 = 길.with_suffix(".json.tmp")
        임시.write_text(json.dumps(값, ensure_ascii=False, indent=2), encoding="utf-8")
        임시.replace(길)
    except OSError as error:
        log.error("제목을 담아 두지 못함", error, 작품=열쇠)


def 번호표() -> dict[str, int]:
    """작품 열쇠 → 그 작품에 내준 제목 첫 번호.

    **담아 둬야 한다.** 복사해서 AI 에 붙여넣고 답을 나중에 넣는 사이에
    앱을 껐다 켜거나 작품을 하나 빼면, 자리 순서로만 매긴 번호는 밀린다.
    그 답을 넣으면 **엉뚱한 작품에 제목이 들어간다.** 되돌릴 수 없다.
    """
    길 = 담는곳() / "_번호.json"
    if not 길.is_file():
        return {}
    try:
        데이터 = json.loads(길.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(데이터, dict):
        return {}
    나온것 = {}
    for k, v in 데이터.items():
        try:
            나온것[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return 나온것


def 번호표담기(표: dict[str, int]) -> None:
    길 = 담는곳() / "_번호.json"
    try:
        길.parent.mkdir(parents=True, exist_ok=True)
        임시 = 길.with_suffix(".json.tmp")
        임시.write_text(json.dumps(표, ensure_ascii=False, indent=2), encoding="utf-8")
        임시.replace(길)
    except OSError as error:
        log.error("제목 번호를 담아 두지 못함", error)


def 제목넣기(열쇠: str, 작품: str, 트랙들: dict[str, str]) -> dict[str, Any]:
    """번역한 제목을 담는다. 이름은 **바꾸지 않는다.**

    빈 것으로 덮어쓰지 않는다. 한 작품만 다시 번역해서 넣었을 때 나머지가
    지워지면 안 된다.
    """
    담긴것 = 가져오기(열쇠)
    if str(작품).strip():
        담긴것["work"] = str(작품).strip()
    for 파일이름, 글 in (트랙들 or {}).items():
        if str(글).strip():
            담긴것["tracks"][str(파일이름)] = str(글).strip()
    담기(열쇠, 담긴것)
    return 담긴것


# ---- 윈도우가 받아 주는 이름 ----


def 원래이름(열쇠: str, 지금이름: str) -> str:
    """이 파일이 원래 무슨 이름이었나. 안 바꿨으면 지금 이름 그대로.

    `.lrc` 안의 `[ti:]` 는 **일본어를 그대로 써야** 다른 앱이 음원과 짝을
    찾는다. 품번을 못 찾은 작품은 파일 이름을 제목으로 쓰는데, 이름을 바꾸고
    나면 그것이 한국어가 되어 버린다.
    """
    if not 열쇠:
        return 지금이름
    return 가져오기(열쇠).get("original", {}).get(지금이름, 지금이름)


def 안전한이름(글: str) -> tuple[str, list[str]]:
    """파일 이름으로 쓸 수 있게 다듬는다. `(이름, 고친 것들)`.

    **말없이 고쳐 두면 나중에 왜 이름이 다른지 알 길이 없다.** 무엇을
    고쳤는지 함께 돌려주고, 화면이 그것을 보여 준다.
    """
    원래 = str(글 or "")
    고친것: list[str] = []

    바뀐것 = 원래
    걸린글자 = sorted({c for c in 바뀐것 if c in 못쓰는글자 or ord(c) < 32})
    if 걸린글자:
        for c in 걸린글자:
            바뀐것 = 바뀐것.replace(c, "_")
        바뀐것 = re.sub(r"[\x00-\x1f]", "_", 바뀐것)
        고친것.append(f"윈도우가 못 쓰는 글자를 _ 로 바꿈: {' '.join(걸린글자)}")

    다듬은것 = 바뀐것.rstrip(" .")
    if 다듬은것 != 바뀐것:
        고친것.append("끝의 점과 공백을 뗌")
        바뀐것 = 다듬은것
    바뀐것 = 바뀐것.strip()

    if len(바뀐것) > 이름한도:
        바뀐것 = 바뀐것[:이름한도].rstrip(" .")
        고친것.append(f"{이름한도}자로 줄임")

    if 바뀐것.split(".")[0].upper() in 예약어:
        바뀐것 = f"_{바뀐것}"
        고친것.append("윈도우 예약어라 앞에 _ 를 붙임")

    if not 바뀐것:
        바뀐것 = "_"
        고친것.append("쓸 수 있는 글자가 없어 _ 로 둠")

    return 바뀐것, 고친것


def 새파일이름(원래파일: Path, 한국어: str) -> tuple[str, list[str]]:
    """`01_プロローグ.mp3` + 「프롤로그」 → `01_프롤로그.mp3`.

    **트랙 번호 접두사는 그대로 둔다.** 떼면 순서가 깨진다. 번역문 앞에
    번호가 이미 붙어 있으면 두 번 붙이지 않는다.
    """
    앞 = re.match(r"^\d+[_\-. ]+", 원래파일.stem)
    번호 = 앞.group(0) if 앞 else ""
    알맹이 = str(한국어 or "").strip()
    if 번호 and re.match(r"^\d+[_\-. ]+", 알맹이):
        번호 = ""   # 번역문이 이미 번호를 들고 있다
    안전, 고친것 = 안전한이름(f"{번호}{알맹이}")
    return f"{안전}{원래파일.suffix}", 고친것


# ---- 파일 이름 바꾸기 ----


@dataclass
class 바꿀것:
    """파일 하나를 어떻게 바꿀지."""

    지금: Path
    새이름: str
    고친것: list[str] = field(default_factory=list)
    # 이름이 같아야 짝이 맞는 파일들(`.lrc` 등). 미리 잡아 둔다 —
    # 바꾼 뒤에 찾으면 이미 옛 이름이 없어서 못 찾는다
    짝들: list[Path] = field(default_factory=list)

    @property
    def 새길(self) -> Path:
        return self.지금.with_name(self.새이름)

    @property
    def 그대로인가(self) -> bool:
        return self.지금.name == self.새이름


@dataclass
class 살펴본것:
    """바꾸기 전에 전부 되는지 본 결과."""

    바꿀것들: list[바꿀것] = field(default_factory=list)
    막힌것: list[str] = field(default_factory=list)

    @property
    def 할수있나(self) -> bool:
        return not self.막힌것 and any(not b.그대로인가 for b in self.바꿀것들)


def 살펴보기(파일들: Iterable[Path], 트랙제목: dict[str, str]) -> 살펴본것:
    """바꿀 수 있는지 **먼저** 본다. 디스크는 건드리지 않는다.

    일곱 개 중 셋만 바뀌면 자막과 음원의 짝이 어긋난 채로 남는다. 그래서
    전부 되는지 보고, 하나라도 안 되면 아무것도 안 한다.
    """
    본것 = 살펴본것()
    쓸이름: dict[str, Path] = {}

    for 길 in 파일들:
        한국어 = str(트랙제목.get(길.name, "") or "").strip()
        if not 한국어:
            continue          # 번역이 없는 트랙은 그대로 둔다
        새이름, 고친것 = 새파일이름(길, 한국어)
        것 = 바꿀것(지금=길, 새이름=새이름, 고친것=고친것)
        if 것.그대로인가:
            continue

        if not 길.is_file():
            본것.막힌것.append(f"{길.name} — 그 자리에 없습니다")
            continue
        이미 = 쓸이름.get(새이름.lower())
        if 이미 is not None:
            본것.막힌것.append(f"{길.name} — {이미.name} 과 같은 이름이 됩니다")
            continue
        if 것.새길.exists():
            본것.막힌것.append(f"{길.name} — {새이름} 이 이미 있습니다")
            continue
        if len(str(것.새길)) > 250:
            본것.막힌것.append(f"{길.name} — 경로가 너무 깁니다")
            continue

        # **짝지은 파일도 본다.** 음원만 보고 넘어가면 `.lrc` 를 옮길 때
        # 남의 자막을 말없이 덮어쓴다. 리눅스는 `rename` 이 조용히 덮고,
        # 윈도우는 그 자리에서 실패해서 반쪽만 바뀐 채로 남는다.
        # 실제로 덮어썼다 — 시험이 잡았다
        것.짝들 = _짝지은것(길)
        막혔나 = False
        for 짝 in 것.짝들:
            갈곳 = 짝.with_name(Path(새이름).stem + 짝.name[len(길.stem):])
            if 갈곳.exists() and 갈곳 != 짝:
                본것.막힌것.append(f"{짝.name} — {갈곳.name} 이 이미 있습니다")
                막혔나 = True
        if 막혔나:
            continue

        쓸이름[새이름.lower()] = 길
        본것.바꿀것들.append(것)

    return 본것


def _짝지은것(음원: Path) -> list[Path]:
    """이 음원과 이름이 같아야 짝이 맞는 파일들.

    `.lrc` 는 음원과 이름이 같아야 다른 앱이 짝을 찾는다. 두고 오면 자막이
    통째로 안 붙는다.
    """
    짝 = []
    for 뒤 in (".lrc", ".ko.txt", ".ja.txt"):
        옆 = 음원.with_suffix("").with_name(음원.stem + 뒤)
        if 옆.is_file():
            짝.append(옆)
    return 짝


def _캐시열쇠(길: Path) -> str:
    """**이름을 바꾸기 전에** 불러야 한다.

    열쇠는 경로와 크기와 고친 시각으로 만든다. 파일이 이미 없으면 크기와
    시각을 못 읽어서 **다른 열쇠가 나온다.** 그러면 옛 캐시를 못 찾고,
    3시간짜리를 처음부터 다시 받아쓰게 된다. 실제로 그렇게 짰다가 시험에
    걸렸다.
    """
    from app.core.job import cache_key

    return cache_key(길)


def _캐시옮기기(옛열쇠: str, 새길: Path) -> None:
    """받아쓴 것을 새 이름 쪽으로 옮긴다.

    캐시 열쇠가 **파일 경로**라서, 이름만 바꾸면 3시간짜리를 처음부터 다시
    받아쓰게 된다. 사용자가 시간을 가장 많이 쓴 부분이라 여기서 잃으면 안 된다.
    """
    from app.core.job import cache_dir, cache_key

    옛것 = cache_dir() / f"{옛열쇠}.json"
    if not 옛것.is_file():
        return
    try:
        데이터 = json.loads(옛것.read_text(encoding="utf-8"))
        데이터["source_file"] = 새길.name
        데이터["source_path"] = str(새길)
        새것 = cache_dir() / f"{cache_key(새길)}.json"
        새것.write_text(json.dumps(데이터, ensure_ascii=False, indent=2), encoding="utf-8")
        옛것.unlink()
    except (OSError, json.JSONDecodeError, KeyError) as error:
        # 캐시를 못 옮겨도 이름은 바뀌어야 한다. 다시 받아쓰면 되는 일이다
        log.error("받아쓴 것을 옮기지 못함", error, 파일=새길.name)


def 이름바꾸기(
    열쇠: str, 파일들: Iterable[Path], 트랙제목: dict[str, str]
) -> dict[str, Any]:
    """실제로 이름을 바꾼다. **전부 되는지 보고 나서 한꺼번에.**

    되돌릴 수 있게 원래 이름을 담아 둔다. 원래 이름은 **최초 것만** 지킨다 —
    두 번 세 번 바꿔도 덮어쓰지 않는다. 덮어쓰면 되돌려도 이미 망가진
    이름으로 간다.
    """
    파일목록 = list(파일들)
    본것 = 살펴보기(파일목록, 트랙제목)
    if 본것.막힌것:
        return {"ok": False, "blocked": 본것.막힌것, "renamed": 0}
    if not 본것.바꿀것들:
        return {"ok": False, "blocked": [], "renamed": 0, "nothing": True}

    담긴것 = 가져오기(열쇠)
    원래 = dict(담긴것.get("original") or {})
    바뀐수 = 0
    고친것: list[str] = []

    for 것 in 본것.바꿀것들:
        짝들 = 것.짝들                   # 살펴볼 때 잡아 둔 것
        옛열쇠 = _캐시열쇠(것.지금)      # **바꾸기 전에** 잡아야 한다
        try:
            것.지금.rename(것.새길)
        except OSError as error:
            # 여기까지 왔는데 실패하면 앞의 것은 이미 바뀌었다. 멈추고
            # 어디까지 됐는지 말해 준다 — 말없이 계속하는 것이 더 나쁘다
            log.error("이름을 바꾸지 못함", error, 파일=것.지금.name)
            담긴것["original"] = 원래
            담긴것["renamed"] = 바뀐수 > 0
            담기(열쇠, 담긴것)
            return {
                "ok": False, "renamed": 바뀐수,
                "blocked": [f"{것.지금.name} — {error}"],
                "partial": True,
            }

        # 원래 이름은 **최초 것만** 지킨다
        원래.setdefault(것.새이름, 원래.pop(것.지금.name, 것.지금.name))
        # 번역해 둔 것도 새 이름 쪽으로 옮긴다.
        #
        # **트랙을 파일 이름으로 맞추기 때문이다.** 안 옮기면 이름을 바꾼
        # 순간 제목 화면에서 번역이 통째로 빈칸이 된다 — 번역해 놓고
        # 바꿨는데 사라진 것처럼 보인다. 나무의 한국어 제목도 같이 사라진다
        옮긴제목 = 담긴것["tracks"].pop(것.지금.name, None)
        if 옮긴제목:
            담긴것["tracks"][것.새이름] = 옮긴제목
        _캐시옮기기(옛열쇠, 것.새길)
        for 짝 in 짝들:
            뒤 = 짝.name[len(것.지금.stem):]
            try:
                짝.rename(짝.with_name(Path(것.새이름).stem + 뒤))
            except OSError as error:
                log.error("짝지은 파일을 옮기지 못함", error, 파일=짝.name)
        바뀐수 += 1
        고친것.extend(f"{것.새이름}: {말}" for 말 in 것.고친것)

    담긴것["original"] = 원래
    담긴것["renamed"] = True
    담기(열쇠, 담긴것)
    return {"ok": True, "renamed": 바뀐수, "blocked": [], "fixed": 고친것}


def 되돌리기(열쇠: str, 폴더: Path) -> dict[str, Any]:
    """이름을 원래대로 되돌린다.

    **파일 이름만 되돌린다.** 번역해 둔 한국어는 남는다. 고쳐서 다시 바꾸면
    된다. 번역 자체를 지우는 것은 따로다.
    """
    담긴것 = 가져오기(열쇠)
    원래 = dict(담긴것.get("original") or {})
    if not 원래:
        return {"ok": False, "renamed": 0, "blocked": ["되돌릴 것이 없습니다."]}

    막힌것: list[str] = []
    할것: list[tuple[Path, Path]] = []
    for 지금이름, 옛이름 in 원래.items():
        지금 = 폴더 / 지금이름
        옛것 = 폴더 / 옛이름
        if not 지금.is_file():
            막힌것.append(f"{지금이름} — 그 자리에 없습니다")
            continue
        if 옛것.exists():
            막힌것.append(f"{지금이름} — {옛이름} 이 이미 있습니다")
            continue
        막혔나 = False
        for 짝 in _짝지은것(지금):
            갈곳 = 짝.with_name(옛것.stem + 짝.name[len(지금.stem):])
            if 갈곳.exists() and 갈곳 != 짝:
                막힌것.append(f"{짝.name} — {갈곳.name} 이 이미 있습니다")
                막혔나 = True
        if 막혔나:
            continue
        할것.append((지금, 옛것))

    if 막힌것:
        return {"ok": False, "renamed": 0, "blocked": 막힌것}

    바뀐수 = 0
    for 지금, 옛것 in 할것:
        짝들 = _짝지은것(지금)
        옛열쇠 = _캐시열쇠(지금)
        try:
            지금.rename(옛것)
        except OSError as error:
            log.error("되돌리지 못함", error, 파일=지금.name)
            담기(열쇠, 담긴것)
            return {"ok": False, "renamed": 바뀐수,
                    "blocked": [f"{지금.name} — {error}"], "partial": True}
        _캐시옮기기(옛열쇠, 옛것)
        for 짝 in 짝들:
            뒤 = 짝.name[len(지금.stem):]
            try:
                짝.rename(짝.with_name(옛것.stem + 뒤))
            except OSError as error:
                log.error("짝지은 파일을 되돌리지 못함", error, 파일=짝.name)
        # 번역도 원래 이름 쪽으로 되돌린다. 되돌린 뒤에도 번역해 둔
        # 한국어는 남아 있어야 한다 — 고쳐서 다시 바꾸면 되기 때문이다
        옮긴제목 = 담긴것["tracks"].pop(지금.name, None)
        if 옮긴제목:
            담긴것["tracks"][옛것.name] = 옮긴제목
        원래.pop(지금.name, None)
        바뀐수 += 1

    담긴것["original"] = 원래
    담긴것["renamed"] = bool(원래)
    담기(열쇠, 담긴것)
    return {"ok": True, "renamed": 바뀐수, "blocked": []}
