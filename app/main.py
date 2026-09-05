"""trans-text 시작점.

    START.bat  (윈도우)
    python app/main.py

exe로 묶은 것도 이 파일을 부른다.

## 창은 하나만 뜬다

예전에는 `START.bat` 이 콘솔 창에서 `python.exe` 를 부르고 끝날 때까지
기다렸다. `python.exe` 는 콘솔 프로그램이라 그 검은 창이 앱 도는 내내
남아 있었다. **늘 두 개가 떴다.**

지금은 `pythonw.exe` 로 띄우고 배치는 곧바로 끝난다. 그래서 검은 창이 없다.
대신 **오류가 조용히 묻히지 않게** 여기서 두 가지를 한다.

1. `pythonw` 로 켜면 `sys.stdout` 과 `sys.stderr` 가 **`None`** 이다.
   그대로 두면 `print` 한 줄에 프로그램이 죽는다. 파일로 돌려 둔다
2. 무엇이 터지든 **`_crash.log` 에 적고 창을 띄워 알린다.** 검은 창이
   없어졌다고 죽은 이유까지 없어지면 안 된다
"""

import os
import sys
import traceback
from pathlib import Path

def _뿌리찾기() -> Path:
    """프로그램이 놓인 곳.

    exe 로 묶으면 `__file__` 은 **임시로 풀린 폴더**를 가리킨다. 거기에
    `_crash.log` 를 적으면 프로그램이 끝나면서 통째로 지워진다 — 죽은 이유가
    같이 사라진다. 묶인 것은 exe 가 놓인 자리를 쓴다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# 이 파일이 `app/` 안에 있으므로 뿌리를 먼저 알려 준다. 그러지 않으면
# `import app` 이 안 된다
뿌리 = _뿌리찾기()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import settings as settings_store

터진것이름 = "_crash.log"
_한개자물쇠 = None


def 터진자리() -> Path:
    """터진 까닭을 적는 곳. 프로그램 폴더에 둔다 — 찾기 쉬워야 한다."""
    return 뿌리 / 터진것이름


def 콘솔없이_켜졌나() -> bool:
    """`pythonw.exe` 로 켜지면 붙어 있는 콘솔이 없다."""
    return sys.stdout is None or sys.stderr is None


def 나오는말_돌리기() -> None:
    """`sys.stdout` 이 `None` 이면 파일로 돌린다.

    `pythonw` 에서는 `print` 한 줄이 `AttributeError` 로 프로그램을 죽인다.
    창이 뜨지도 않고 아무 말도 없이 사라진다.
    """
    if not 콘솔없이_켜졌나():
        return
    try:
        새것 = open(뿌리 / "_start.log", "a", encoding="utf-8", errors="replace")
    except OSError:
        새것 = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = 새것
    if sys.stderr is None:
        sys.stderr = 새것


def 터진것_알리기(글: str) -> None:
    """적어 두고 창으로 알린다.

    검은 창이 없어졌으니 여기서 안 알리면 **아무 말 없이 사라진 것**이 된다.
    바로 그것을 없애려고 이 전부를 한 것이다.
    """
    자리 = 터진자리()
    try:
        자리.write_text(글, encoding="utf-8")
        어디 = str(자리)
    except OSError:
        어디 = "(적어 두지도 못했습니다)"

    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            f"프로그램이 시작하지 못했습니다.\n\n{글.strip().splitlines()[-1]}\n\n"
            f"자세한 내용:\n{어디}",
            "trans-text",
            0x10,      # MB_ICONERROR
        )
    except Exception:
        pass       # 알리다가 또 터지면 할 수 있는 것이 없다


# **한 번에 하나만.** 바탕화면 아이콘을 두 번 누르면 창이 둘 뜨고, 둘이 같은
# 설정 파일과 목록 파일을 번갈아 덮어쓴다. 어느 쪽이 살아남는지는 운이다.
#
# 잠금 파일 대신 **localhost 포트**를 잡는다. 잠금 파일은 앱이 죽으면 남아서
# 다음에 「이미 켜져 있습니다」 라고 거짓말한다. 포트는 프로세스가 죽는 순간
# 운영체제가 돌려준다 — 치울 것이 없다.
한개포트 = 47_321


def 이미_켜져_있나():
    """다른 판이 돌고 있으면 True. 아니면 포트를 쥔 채로 소켓을 돌려준다.

    소켓을 돌려주는 까닭: 함수가 끝나면서 닫히면 잠금이 풀린다. 부르는 쪽이
    프로그램이 끝날 때까지 들고 있어야 한다.
    """
    import socket

    자물쇠 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        자물쇠.bind(("127.0.0.1", 한개포트))
        자물쇠.listen(1)
        return False, 자물쇠
    except OSError:
        자물쇠.close()
        return True, None


def 이미_켜져_있다고_알리기() -> None:
    if sys.platform != "win32":
        print("trans-text 가 이미 켜져 있습니다.", file=sys.stderr)
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0, "trans-text 가 이미 켜져 있습니다.\n작업 표시줄에서 그 창을 찾아 주세요.",
            "trans-text", 0x40,      # MB_ICONINFORMATION
        )
    except Exception:
        pass


def main() -> int:
    나오는말_돌리기()
    켜져있나, 자물쇠 = 이미_켜져_있나()
    if 켜져있나:
        이미_켜져_있다고_알리기()
        return 0
    try:
        from app.core import log

        log.start()
        # 모델을 사용자 폴더 아래에 받게 만든다. faster_whisper 를 부르기 전에 해야 한다
        log.write("환경", "모델 자리", 곳=str(settings_store.use_our_model_dir()))

        from app.ui.window import run

        # 지난번에 터진 자국은 이제 지운다. 남겨 두면 다음에 켤 때
        # 옛날 것을 보고 지금 터진 줄 안다
        try:
            터진자리().unlink(missing_ok=True)
        except OSError:
            pass

        global _한개자물쇠
        _한개자물쇠 = 자물쇠        # 창이 도는 동안 붙들어 둔다
        return run()
    except BaseException:      # noqa: BLE001 — 조용히 사라지는 것보다 낫다
        터진것_알리기(traceback.format_exc())
        raise


if __name__ == "__main__":
    sys.exit(main())
