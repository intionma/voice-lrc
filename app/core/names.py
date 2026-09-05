"""작품마다의 호칭 표.

## 왜 이것만 따로 받나

번역이 틀리는 방식은 여러 가지인데, **호칭은 그중 유독 티가 나고 유독
고치기 쉽다.** 실제로 이런 줄이 나왔다:

    お兄さん、よかったね…  →  언니 좋았겠네...

`お兄さん` 은 오빠다. 한 번 틀리면 그 작품 내내 틀린다. AI 는 앞뒤 문맥으로
남녀를 짐작해야 하는데, 동인음성은 화자가 한 명이고 상대는 소리로만
나오니까 짐작할 거리가 거의 없다. **사람이 한 줄 적어 주면 끝나는 일이다.**

## 용어집이 아니다

「용어집을 만들자」 는 끝이 없다. 고유명사·말버릇·기술 용어까지 손대기
시작하면 작품마다 한나절이다. 그래서 **호칭만** 받는다. 이름을 그렇게
지어 두면 범위가 안 늘어난다.

보통 세 줄이면 끝난다:

    お兄さん = 오빠
    お姉ちゃん = 누나
    先輩 = 선배

## 어떻게 쓰이나

작품 정보(`dlsite.Work.context()`)에 붙어서 프롬프트 맨 앞쪽으로 들어간다.
번역할 때도, 검수할 때도 같은 것이 들어간다 — 검수에서 다시 틀리면 고치는
의미가 없다.
"""

from __future__ import annotations

import json
from pathlib import Path
from app.core import log
from app.core import settings as settings_store

# 한 작품에서 받는 줄 수 한도.
#
# 넘으면 그것은 호칭 표가 아니라 용어집이다. 프롬프트만 길어지고 모델이
# 앞쪽 지시를 덜 본다. 막아 두면 범위가 안 늘어난다.
줄한도 = 20

# 한 줄의 글자 한도. 문장을 적기 시작하면 그것도 용어집이다
글자한도 = 60


def 파일() -> Path:
    return settings_store.config_dir() / "names.json"


def _읽기() -> dict[str, str]:
    길 = 파일()
    if not 길.is_file():
        return {}
    try:
        데이터 = json.loads(길.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 망가진 파일 때문에 번역을 못 하게 되면 안 된다. 없는 셈 친다
        return {}
    if not isinstance(데이터, dict):
        return {}
    return {str(k): str(v) for k, v in 데이터.items() if isinstance(v, str)}


def 다듬기(글: str) -> str:
    """사람이 적은 것을 쓸 수 있는 꼴로 만든다.

    빈 줄을 버리고, 줄 수와 글자 수를 한도로 자른다. **막지 않고 자른다** —
    스무 줄을 적었다고 저장을 거절하면 적은 것이 통째로 날아간다.
    """
    줄들 = []
    for 줄 in str(글 or "").splitlines():
        다듬은것 = 줄.strip()[:글자한도]
        if 다듬은것:
            줄들.append(다듬은것)
        if len(줄들) >= 줄한도:
            break
    return "\n".join(줄들)


def 가져오기(작품열쇠: str) -> str:
    if not 작품열쇠:
        return ""
    return _읽기().get(str(작품열쇠), "")


def 담기(작품열쇠: str, 글: str) -> str:
    """적은 것을 담는다. 담긴 것을 돌려준다.

    비우면 그 작품 자리를 지운다. 빈 글이 남아 있으면 나중에 「적어 뒀나?」
    를 볼 때 있는 것으로 세어진다.
    """
    열쇠 = str(작품열쇠 or "")
    if not 열쇠:
        return ""
    담을것 = 다듬기(글)
    모두 = _읽기()
    if 담을것:
        모두[열쇠] = 담을것
    else:
        모두.pop(열쇠, None)

    길 = 파일()
    try:
        길.parent.mkdir(parents=True, exist_ok=True)
        임시 = 길.with_suffix(".json.tmp")
        임시.write_text(json.dumps(모두, ensure_ascii=False, indent=2), encoding="utf-8")
        임시.replace(길)
    except OSError as error:
        # 담지 못해도 이번 번역은 이어져야 한다
        log.error("호칭을 담아 두지 못함", error, 작품=열쇠)
    return 담을것


def 짝들(글: str) -> dict[str, str]:
    """적어 둔 호칭을 `{일본어: 한국어}` 로 읽는다.

    `お兄さん = 오빠` · `お姉ちゃん → 누나` 둘 다 받는다. 사람이 적는 것이라
    구분자를 하나로 못 박지 않는다.

    **짝과 짝 사이도 마찬가지다.** 호칭을 적는 칸은 한 줄짜리 `<input>` 이라
    줄바꿈을 칠 수가 없다. 그런데 여기서는 줄바꿈으로만 갈랐다. 칸에 적힌
    안내는 「여러 개는 세미콜론으로」 인데, 그대로 치면

        お兄さん = 오빠; お姉ちゃん = 누나
        → {"お兄さん": "오빠; お姉ちゃん = 누나"}

    가 됐다. **둘째 짝이 첫째 짝의 한국어 이름 속으로 통째로 빨려 들어간다.**
    그 이름이 그대로 AI 지시문에 실려서, 「お兄さん 은 '오빠; お姉ちゃん =
    누나' 라고 불러라」 가 된다. 오류는 안 난다.

    안내한 세미콜론과, 안내 글에 실제로 쓰인 가운뎃점(`·`)까지 받는다.
    """
    글 = str(글 or "")
    for 사이 in (";", "·", "；"):
        글 = 글.replace(사이, "\n")
    난것: dict[str, str] = {}
    for 줄 in 글.splitlines():
        줄 = 줄.strip()
        if not 줄 or 줄.startswith("#"):
            continue
        for 구분 in ("=", "→", "->", ":"):
            if 구분 in 줄:
                왼, 오른 = 줄.split(구분, 1)
                왼, 오른 = 왼.strip(), 오른.strip()
                if 왼 and 오른:
                    난것[왼] = 오른
                break
    return 난것


def 부딪히나(작품열쇠들: list[str]) -> list[dict[str, object]]:
    """이 작품들을 한 프롬프트에 같이 넣으면 호칭이 부딪히는가.

    **조용히 틀리는 종류라 미리 말해 줘야 한다.** 작품 A 가
    「お兄さん = 오빠」, 작품 B 가 「お兄さん = 형」 이면, 구획을 나눠 줘도
    AI 가 가까운 쪽을 못 보고 섞을 수 있다. 오류도 안 나고 줄도 안 빠진다.

    부딪히는 것마다 `{말, 옮긴것: {작품열쇠: 한국어}}` 를 돌려준다.
    """
    모음: dict[str, dict[str, str]] = {}
    for 열쇠 in 작품열쇠들:
        for 일본어, 한국어 in 짝들(가져오기(열쇠)).items():
            모음.setdefault(일본어, {})[열쇠] = 한국어

    난것 = []
    for 일본어, 작품별 in 모음.items():
        if len(set(작품별.values())) > 1:
            난것.append({"말": 일본어, "옮긴것": dict(작품별)})
    return 난것


def 붙이기(작품정보: str, 호칭: str) -> str:
    """작품 정보에 호칭 표를 얹는다.

    호칭을 **작품 정보보다 뒤에** 둔다. 성우 이름은 참고고 호칭은 규칙이라,
    뒤에 있는 편이 모델이 더 무겁게 본다.
    """
    다듬은것 = 다듬기(호칭)
    if not 다듬은것:
        return str(작품정보 or "")
    표 = f"호칭은 반드시 이대로 옮긴다:\n{다듬은것}"
    앞 = str(작품정보 or "").strip()
    return f"{앞}\n{표}" if 앞 else 표


def 사전() -> dict[str, str]:
    """담아 둔 것 전부. 화면이 「적어 둔 작품」을 표시할 때 쓴다."""
    return _읽기()
