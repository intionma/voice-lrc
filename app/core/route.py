"""번역을 어디로 보내는가 — 길 셋과 손잡이 넷.

여태 설정에는 **보내는 곳 여섯 개**(직접 복붙 · Ollama · LM Studio ·
Gemini · Groq · OpenRouter)가 한 줄로 늘어서 있었다. 고르는 사람 입장에서
이 여섯은 같은 종류가 아니다. 「직접 복붙」과 「Gemini」는 **손이 얼마나
가는가**가 다르고, 「Ollama」와 「Gemini」는 **어디서 도는가**만 다르다.
섞어 놓으니 무엇을 고르는 자리인지가 흐려졌다.

그래서 두 층으로 나눈다.

    길 (셋)      무엇을 하겠다는 것인가
    손잡이 (넷)   그 길에서 어떻게 하겠다는 것인가

길을 고르면 손잡이 넷이 그 길에 맞게 정해진다. 그대로 두면 그만이고,
하나만 다르게 하고 싶으면 그 손잡이만 건드린다 — 건드린 것만 기억한다.

## 길 셋

    chat         쓰던 AI 채팅에 복사해 붙여넣는다. 구독을 그대로 쓴다
    translator   DeepL · 파파고 · 구글에 넣는다. **지시문도 가림표도 못 받는다**
    endpoint     주소로 보내고 답을 받아 온다. 내 컴퓨터든 남의 API 든 같다

**`endpoint` 가 API 와 내 컴퓨터 AI 를 하나로 묶는다.** 둘 다 「주소에
보내고 답을 받는다」는 똑같은 일이고, 다른 것은 주소뿐이다. 나눠 놓으면
「API 로 할까 로컬로 할까」 라는 있지도 않은 물음을 만들어 낸다.

**유료·무료를 적지 않는다.** 남의 요금제는 우리가 모르는 사이에 바뀌고,
틀린 값을 적어 두면 그걸 믿고 고른 사람이 손해를 본다. 단위는 트랙 하나다.

## 손잡이 넷

    지시문     번역 지시를 붙여 보내는가. 번역기는 지시문까지 번역해 돌려준다
               (`endpoint` 에서 「직접 복붙」은 보낼 곳이 아니라 목록에 없다)
    가리기     민감한 낱말을 `KW01` 같은 표로 바꿔 보내는가. 거절을 줄인다.
               **번역기에는 못 쓴다** — 번역기는 표를 그대로 두지 않는다
    묶음       한 번에 몇 줄을 보내는가. 사람이 붙여넣는 길은 작게,
               기계가 받는 길은 크게
    보내는길   `endpoint` 에서만 뜻이 있다. 어느 주소로 보내는가
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import exchange


@dataclass(frozen=True)
class Route:
    """길 하나. 화면에서 고르는 것이 이것이다."""

    id: str
    name: str
    note: str
    # 이 길을 고르면 손잡이가 이렇게 놓인다
    지시문: bool
    가리기: bool
    묶음: int
    보내는길: str
    # 손으로 바꿀 수 없는 손잡이. 바꾸면 그 길이 아니게 되는 것들이다
    잠근것: tuple[str, ...] = ()


ROUTES: tuple[Route, ...] = (
    Route(
        id="chat",
        name="쓰는 AI에 복사해서 붙여넣기",
        note="ChatGPT · Claude · Gemini · Grok — 쓰던 구독을 그대로 씁니다",
        지시문=True,
        가리기=True,
        묶음=exchange.BATCH_LINES,
        보내는길="manual",
    ),
    Route(
        id="translator",
        name="번역기",
        note="DeepL · 파파고 · 구글 — 지시문을 뗀 원문만 보냅니다",
        지시문=False,
        가리기=False,
        묶음=exchange.BATCH_LINES,
        보내는길="manual",
        # **이 둘은 잠근다.** 번역기에 지시문을 보내면 지시문까지 번역해서
        # 돌려주고, 가림표를 보내면 표가 뭉개져 되돌릴 수 없다. 켤 수 있게
        # 두면 켜 보고 나서야 안다 — 그때는 이미 자막이 망가진 뒤다
        잠근것=("지시문", "가리기"),
    ),
    Route(
        id="endpoint",
        name="내 컴퓨터 AI나 API로 자동",
        note="손이 안 갑니다. 대신 준비할 것이 있습니다",
        지시문=True,
        가리기=True,
        # 사람이 붙여넣지 않으므로 크게 보낸다. 왕복이 절반으로 줄고
        # 모델이 앞뒤 문맥을 두 배로 본다
        묶음=exchange.LOCAL_BATCH_LINES,
        보내는길="ollama",
    ),
)

DEFAULT = "chat"

_BY_ID = {r.id: r for r in ROUTES}

# 손으로 건드릴 수 있는 손잡이 이름. 설정에 이 이름으로 남는다
손잡이 = ("지시문", "가리기", "묶음", "보내는길")


def get(route_id: str) -> Route:
    """모르는 이름이 오면 기본 길을 준다. 설정이 깨져도 돌아가야 한다."""
    return _BY_ID.get(str(route_id or ""), _BY_ID[DEFAULT])


def 정해진값(settings: dict[str, Any]) -> dict[str, Any]:
    """지금 설정에서 손잡이 넷이 실제로 어떤 값인가.

    길이 정한 값 위에 **사용자가 건드린 것만** 얹는다. 길을 바꾸면 안
    건드린 손잡이는 새 길을 따라가고, 건드린 것은 그대로 남는다.

    잠근 손잡이는 얹지 않는다. 옛 설정 파일에 값이 남아 있어도 마찬가지다 —
    **설정만 지우고 뒷단을 남기면** 화면에는 안 보이는데 동작은 그대로인,
    제일 찾기 어려운 종류의 버그가 된다.
    """
    쪽 = settings.get("translation", {}) or {}
    길 = get(쪽.get("route", ""))
    값 = {
        "route": 길.id,
        "지시문": 길.지시문,
        "가리기": 길.가리기,
        "묶음": 길.묶음,
        "보내는길": 길.보내는길,
    }
    고친것 = 쪽.get("고친것", {}) or {}
    for 이름 in 손잡이:
        if 이름 in 길.잠근것 or 이름 not in 고친것:
            continue
        낸것 = 고친것[이름]
        if 이름 == "묶음":
            try:
                낸것 = max(20, min(exchange.LOCAL_BATCH_LINES, int(낸것)))
            except (TypeError, ValueError):
                continue
        elif 이름 == "보내는길":
            낸것 = str(낸것 or "")
            if not 낸것:
                continue
            # **「자동」 길에서 「직접 복붙」 은 보낼 곳이 아니다.**
            #
            # 고르면 자동 번역이 조용히 아무것도 안 한다 — 「자동으로
            # 보낸다」 고 골라 놓고 아무 데도 안 보내는 셈이라, 왜 안 도는지
            # 알아낼 길이 없다. 옛 설정에 남아 있어도 마찬가지다
            if 길.id == "endpoint" and 낸것 == "manual":
                continue
        else:
            낸것 = bool(낸것)
        값[이름] = 낸것
    return 값


def copy_style(settings: dict[str, Any]) -> str:
    """복사하기가 무엇을 담을지. 지시문 손잡이가 그대로 이 값이다.

    예전에는 `output.copy_style` 이라는 **딴 이름의 같은 값**이 따로 있어서,
    둘이 어긋나면 화면에는 「지시문 붙임」인데 나가는 것은 맨 원문이었다.
    """
    return "ai" if 정해진값(settings)["지시문"] else "plain"


def to_view(settings: dict[str, Any]) -> dict[str, Any]:
    """화면에 그릴 것. 길 목록과 지금 값, 그리고 잠긴 손잡이."""
    지금 = 정해진값(settings)
    return {
        "routes": [
            {"id": r.id, "name": r.name, "note": r.note,
             "잠근것": list(r.잠근것)}
            for r in ROUTES
        ],
        "지금": 지금,
        "잠근것": list(get(지금["route"]).잠근것),
    }
