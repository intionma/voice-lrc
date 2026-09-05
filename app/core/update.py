"""새 판이 나왔는지 본다.

**공개 저장소가 곧 업데이트 통로다.** 받아 간 사람은 우리가 고친 것을 알 길이
없다. 설정 깊은 곳에 「업데이트하고 다시 켜기」 를 두고 눌러 보라고 하면,
누르기 전에는 새 판이 있는지조차 모른다 — 대개 안 누른다.

그래서 앱이 먼저 본다. 켤 때 한 번, 뒤에서 조용히.

**틀리면 조용히 입을 다문다.** 인터넷이 없거나, git 으로 받은 것이 아니거나,
가지가 갈라졌을 수 있다. 그럴 때 "확인 실패" 같은 것을 띄우면 고칠 수도 없는
경고만 늘어난다. 새 판이 **확실히 있을 때만** 말한다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Sequence

# 저장소 뿌리. `app/core/update.py` 에서 두 층 위
뿌리 = Path(__file__).resolve().parents[2]

돌리개 = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _돌리기(명령: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        list(명령), cwd=str(뿌리), capture_output=True, text=True, timeout=25,
        # 윈도우에서 검은 창이 깜빡이지 않게
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def 몇_판_뒤처졌나(돌리기: 돌리개 | None = None) -> int:
    """원격보다 몇 판 뒤인가. 모르면 0.

    0 은 「최신」이 아니라 **「모르거나 최신」**이다. 이 값으로 경고를 띄우지
    않는 까닭이다 — 1 이상일 때만 말한다.
    """
    달리다 = 돌리기 or _돌리기
    try:
        가져오기 = 달리다(["git", "fetch", "--quiet"])
        if 가져오기.returncode != 0:
            return 0
        센것 = 달리다(["git", "rev-list", "--count", "HEAD..@{u}"])
        if 센것.returncode != 0:
            return 0
        return int((센것.stdout or "0").strip() or 0)
    except Exception:
        # 인터넷이 없거나 git 이 없거나 시간이 다 됐다. 조용히 넘어간다
        return 0
