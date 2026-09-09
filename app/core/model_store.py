"""받아쓰기 모델을 **얼마나 받아야 하고, 자리는 되는가.**

## 왜 따로 뒀나

강도마다 모델이 다르다. 「빠르게」는 `large-v3-turbo`, 「보통」·「속삭임」·
「정확」은 `large-v3`, 「극한」은 **둘 다** 쓴다. 그래서 강도를 올리면 다음
받아쓰기에서 몇 GB 를 조용히 더 받는다.

두 자리에서 같은 질문을 한다.

    화면 — 이 강도, 이미 받아 뒀나? 안 받았으면 몇 GB 를 더 받나
    받기 전 — 디스크에 그만한 자리가 있나

셈이 갈리면 화면은 「받아 둠」 이라는데 받기는 다시 받는 꼴이 난다. 한 곳에
둔다.

## 여기서 확인하지 못한 것

이 컨테이너에는 모델도 윈도우도 없다. **아래 용량은 재어 본 값이 아니라
어림값이다.** 「모자란가」 를 볼 때만 쓰므로 넉넉하게 잡아 두었다 — 실제보다
크게 잡으면 「자리가 모자랍니다」 를 조금 일찍 말할 뿐이지만, 작게 잡으면
받다가 디스크가 꽉 찬다. 어느 쪽으로 틀릴지를 고른 것이다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.core import settings as settings_store

# 받아 두면 이만큼 먹는다(GB, 어림값).
#
# faster-whisper 가 쓰는 ct2 float16 판 기준이다. 저장소 이름이 아니라
# **우리가 강도 표에 적는 짧은 이름**을 열쇠로 쓴다 — 저장소 이름은
# faster-whisper 판마다 달라진다.
대략크기GB: dict[str, float] = {
    "large-v3": 3.1,
    "large-v3-turbo": 1.7,
}

# 표에 없는 모델. **모르면 크게 잡는다** — 작게 잡았다가 받다가 꽉 차는 쪽이
# 훨씬 나쁘다
모르는것GB = 3.5

# 받는 동안 임시 파일이 함께 있는다. 다 받은 뒤에도 디스크가 정확히 0 이면
# 다음에 무엇도 못 쓴다
여윳돈GB = 1.0


def 크기GB(이름: str) -> float:
    """이 모델 하나가 먹는 자리(GB, 어림값)."""
    꼬리 = _꼬리(이름)
    return 대략크기GB.get(꼬리, 모르는것GB)


def 받아두는곳() -> Path:
    """지금 이 실행에서 모델이 놓이는 자리.

    `settings.use_our_model_dir()` 이 이미 골라서 환경변수에 박아 둔다. 그것을
    그대로 읽는다 — 여기서 따로 고르면 재는 자리와 받는 자리가 갈린다.
    """
    자리 = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.environ.get("HF_HUB_CACHE")
    return Path(자리) if 자리 else settings_store.models_dir() / "hub"


def _꼬리(이름: str) -> str:
    """`Systran/faster-whisper-large-v3` → `large-v3`.

    강도 표에는 짧은 이름을 적는데 faster-whisper 는 제 저장소로 바꿔서 받는다.
    양쪽을 같은 열쇠로 맞춘다.
    """
    마지막 = str(이름 or "").replace("\\", "/").split("/")[-1].lower()
    for 앞 in ("faster-whisper-", "faster-distil-whisper-", "whisper-"):
        if 마지막.startswith(앞):
            마지막 = 마지막[len(앞):]
    return 마지막


def _들어맞나(폴더이름: str, 꼬리: str) -> bool:
    """받아 둔 폴더 이름이 이 모델의 것인가.

    **`in` 으로 보면 안 된다.** `large-v3` 는 `large-v3-turbo` 의 부분
    문자열이라, 터보만 받아 둔 사람에게 「large-v3 도 받아 뒀다」 고 말하게
    된다. 그러면 화면은 「받아 둠」 인데 누르면 3GB 를 새로 받는다.
    """
    이름 = 폴더이름.lower()
    return 이름 == 꼬리 or 이름.endswith("-" + 꼬리) or 이름.endswith("--" + 꼬리)


def 받아둔곳(이름: str) -> Path | None:
    """다 받아 둔 자리. 없으면 `None`.

    **`model.bin` 이 있어야 받은 것으로 친다.** 받다 만 것은 폴더만 남는다 —
    그것을 「받아 둠」 으로 세면 자리가 모자란 사람이 영원히 같은 곳에서
    터진다.
    """
    꼬리 = _꼬리(이름)
    if not 꼬리:
        return None
    곳 = 받아두는곳()
    try:
        후보 = [p for p in 곳.glob("models--*")
                if _들어맞나(p.name.split("--")[-1], 꼬리)]
    except OSError:
        return None
    for 폴더 in 후보:
        찍은것들 = 폴더 / "snapshots"
        if not 찍은것들.is_dir():
            continue
        try:
            for 하나 in sorted(찍은것들.iterdir()):
                if (하나 / "model.bin").is_file():
                    return 하나
        except OSError:
            continue
    return None


def 받아뒀나(이름: str) -> bool:
    return 받아둔곳(이름) is not None


def 더받을GB(이름들: list[str]) -> float:
    """이 모델들을 쓰려면 앞으로 몇 GB 를 더 받아야 하나.

    이미 받아 둔 것은 안 센다. 같은 모델을 두 번 적어도 한 번만 센다 —
    「극한」이 1차와 2차에 같은 모델을 쓰면 두 배로 셀 뻔했다.
    """
    본것: set[str] = set()
    합 = 0.0
    for 이름 in 이름들:
        꼬리 = _꼬리(이름)
        if not 꼬리 or 꼬리 in 본것:
            continue
        본것.add(꼬리)
        if not 받아뒀나(이름):
            합 += 크기GB(이름)
    return round(합, 1)


def 여유GB() -> float | None:
    """모델을 받을 드라이브에 남은 자리. 못 재면 `None`.

    폴더가 아직 없을 수 있다. 그러면 있는 윗자리까지 올라가서 잰다 — 같은
    드라이브라 답은 같다.
    """
    자리 = 받아두는곳()
    for _ in range(6):
        try:
            return round(shutil.disk_usage(자리).free / (1024 ** 3), 1)
        except OSError:
            윗자리 = 자리.parent
            if 윗자리 == 자리:
                return None
            자리 = 윗자리
    return None


class 자리부족(RuntimeError):
    """디스크가 모자라 모델을 못 받는다.

    `_work` 가 `str(error)` 를 그대로 `job.error` 에 넣으므로, 이 예외의 글이
    곧 화면에 뜨는 글이다. 영어 스택이 아니라 **무엇을 얼마나 비우면 되는지**
    가 보여야 한다.
    """


def 자리가모자라나(이름들: list[str]) -> str:
    """모자라면 **사람이 읽을 한 줄**, 되면 빈 문자열.

    받기 **전에** 부른다. 받다가 꽉 차면 반쯤 받은 파일이 남고, 다음에 눌러도
    같은 자리에서 또 터진다. 그때 화면에 뜨는 것은 huggingface 가 던진 영어
    한 줄이라, 디스크가 꽉 찼다는 것을 아무도 못 알아본다.

    **못 재면 막지 않는다.** 재는 데 실패했다고 받아쓰기를 세우면, 멀쩡한
    기기에서 앱이 통째로 안 도는 쪽이 된다.
    """
    더받을것 = 더받을GB(이름들)
    if 더받을것 <= 0:
        return ""
    남은것 = 여유GB()
    if 남은것 is None:
        return ""
    필요 = round(더받을것 + 여윳돈GB, 1)
    if 남은것 >= 필요:
        return ""
    곳 = 받아두는곳()
    where = 곳.drive or str(곳)
    return (
        f"받아쓰기 모델 {더받을것}GB 를 받아야 하는데 {where} 에 "
        f"{남은것}GB 밖에 없습니다. "
        f"{필요}GB 는 있어야 합니다(받는 동안 쓸 자리 {여윳돈GB}GB 포함). "
        f"자리를 비우고 다시 눌러 주세요."
    )
