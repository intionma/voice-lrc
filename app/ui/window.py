"""창을 띄운다.

화면은 HTML이고, pywebview가 그것을 담은 창을 만든다. 윈도우에서는 이미 깔려
있는 Edge WebView2를 쓰므로 브라우저를 따로 설치할 필요가 없다.

여기서는 창을 만들고 창구를 이어 주기만 한다. 실제 일은 `api.Controller`가 한다.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from app.core import settings as settings_store
from app.core.transcribe import AUDIO_SUFFIXES
from app.ui.api import Controller

TITLE = "trans-text"

# 작업 표시줄에서 이 앱을 무엇으로 볼 것인가.
#
# 안 정해 두면 윈도우가 **`pythonw.exe` 의 것으로 묶는다.** 아이콘도
# 파이썬 것이 뜨고, 창을 여러 개 띄우면 파이썬끼리 한 무더기가 된다.
앱이름 = "intion.trans-text"


def icon_path() -> Path:
    """앱 아이콘. 없으면 None — 아이콘이 없다고 창을 못 띄우면 안 된다."""
    자리 = web_dir() / "icon.ico"
    return 자리 if 자리.is_file() else None


def _화면크기(webview):
    """지금 화면의 크기. 못 알아내면 `None` — 그러면 자리를 안 밀어 넣는다.

    판마다 `screens` 가 있기도 없기도 하다. 없다고 창을 못 띄우면 안 된다.
    """
    try:
        첫판 = (getattr(webview, "screens", None) or [None])[0]
        폭, 높 = int(첫판.width), int(첫판.height)
    except Exception:
        return None
    return (폭, 높) if 폭 > 0 and 높 > 0 else None


# 끌어서 크기를 바꾸는 동안 이벤트가 계속 온다. 그때마다 파일을 쓰면
# 한 번 끄는 데 수백 번이다. 손이 멎고 이만큼 지나면 담는다
창담기지연초 = 1.0


def _창자리기억하기(window) -> None:
    """창을 키우거나 옮기면 담아 둔다. 다음에 켤 때 그 자리에서 뜬다.

    **판에 따라 이벤트 이름이 다르다.** 없으면 그 갈래만 포기하고 넘어간다
    — 자리를 못 담는다고 앱이 안 켜지면 안 된다. 아이콘·끌어다 놓기와
    같은 규칙이다.
    """
    시계: list[threading.Timer] = []

    def 담기() -> None:
        try:
            값 = settings_store.창자리다듬기(
                {
                    "width": getattr(window, "width", 0),
                    "height": getattr(window, "height", 0),
                    "x": getattr(window, "x", None),
                    "y": getattr(window, "y", None),
                }
            )
        except Exception:
            return
        if 값:
            try:
                settings_store.save({"window": 값})
            except Exception:
                pass      # 못 담아도 이번 판만 못 남는다

    def 곧담기(*_args, **_kwargs) -> None:
        for 옛것 in 시계:
            옛것.cancel()
        시계.clear()
        시계병 = threading.Timer(창담기지연초, 담기)
        시계병.daemon = True     # 이것 때문에 앱이 안 꺼지면 안 된다
        시계.append(시계병)
        시계병.start()

    이벤트 = getattr(window, "events", None)
    for 이름 in ("resized", "moved", "maximized", "restored"):
        갈래 = getattr(이벤트, 이름, None)
        if 갈래 is None:
            continue
        try:
            갈래 += 곧담기
        except Exception:
            pass

    # **닫을 때 한 번 더.** 마지막 1초 안에 옮긴 것은 시계가 못 담고 죽는다
    닫힘 = getattr(이벤트, "closing", None) or getattr(이벤트, "closed", None)
    if 닫힘 is not None:
        try:
            닫힘 += lambda *a, **k: 담기()
        except Exception:
            pass


def _작업표시줄이름붙이기() -> None:
    """윈도우에게 「이건 파이썬이 아니라 이 앱이다」 라고 말해 둔다.

    **창을 만들기 전에 해야 한다.** 만든 뒤에 부르면 이미 파이썬으로
    묶인 뒤라 안 먹는다.

    윈도우가 아니거나 실패해도 넘어간다. 아이콘 묶기 하나 때문에 앱이
    안 켜지면 안 된다.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(앱이름)
    except Exception:
        pass


def web_dir() -> Path:
    """화면 파일이 있는 곳.

    exe로 묶으면 임시 폴더에 풀리므로 그쪽을 먼저 본다.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "web"
    return Path(__file__).resolve().parent / "web"


def _file_filter() -> str:
    """파일 고르기 창에 넘길 거르개.

    확장자는 **세미콜론**으로 잇는다. 띄어쓰기로 이으면 윈도우가 못 읽어서 창이
    아예 안 뜬다. 눌러도 아무 반응이 없는 것처럼 보인다.
    """
    patterns = ";".join(f"*{suffix}" for suffix in sorted(AUDIO_SUFFIXES))
    return f"음성 파일 ({patterns})"


def run() -> int:
    try:
        import webview
    except ImportError:
        print(
            "창을 띄우는 부품이 없습니다.\n"
            "  pip install pywebview\n"
            "을 실행한 뒤 다시 켜 주세요.",
            file=sys.stderr,
        )
        return 1

    # **창을 만들기 전에.** 만든 뒤에는 안 먹는다
    _작업표시줄이름붙이기()

    controller = Controller()
    # **새 판이 나왔는지 뒤에서 한 번 본다.** 공개 저장소가 곧 업데이트
    # 통로라, 받아 간 사람은 우리가 고친 것을 알 길이 없다. 실패하면
    # 조용히 넘어간다 — 화면을 붙잡지 않는다
    controller.새판보기()
    크기 = settings_store.창자리다듬기(
        settings_store.load().get("window", {}), 화면=_화면크기(webview)
    ) or {"width": 1100, "height": 760}

    자리 = {}
    if 크기.get("x") is not None and 크기.get("y") is not None:
        자리 = {"x": int(크기["x"]), "y": int(크기["y"])}

    window = webview.create_window(
        TITLE,
        str(web_dir() / "index.html"),
        js_api=controller,
        width=int(크기.get("width", 1100)),
        height=int(크기.get("height", 760)),
        min_size=settings_store.창최소,
        background_color="#14161a",
        **자리,
    )
    _창자리기억하기(window)

    def 고르기() -> list[str]:
        """파일 고르기 창을 연다.

        거르개 모양이 판마다 달라서 실패할 수 있다. 실패하면 거르개 없이 한 번 더
        해 본다. 그래도 안 되면 왜 안 됐는지 화면에 띄운다. 눌러도 아무 일이
        없는 것이 제일 나쁘다.
        """
        for 거르개 in ((_file_filter(), "모든 파일 (*.*)"), None):
            try:
                고름 = window.create_file_dialog(
                    _open_dialog(webview),
                    allow_multiple=True,
                    **({"file_types": 거르개} if 거르개 else {}),
                )
                return list(고름 or [])
            except Exception as error:
                마지막오류 = error

        controller.notice = (
            "파일 고르기 창을 열지 못했습니다.\n"
            f"({마지막오류})\n"
            "음원을 창 안으로 끌어다 놓아 보세요."
        )
        return []

    controller.file_picker = 고르기

    def 창닫기() -> None:
        """업데이트 같은 것이 부른다. 이 창이 죽어야 배치가 일을 시작한다.

        띄워 둔 배치는 이 프로세스가 사라지기를 기다린다. 못 닫으면 배치가
        스스로 물러나므로 아무 일도 일어나지 않는다 — 앱이 사라진 채로
        남는 것보다는 낫다.

        **여기서 삼키지 않는다.** 부르는 쪽이 알아야 화면의 단추를 되돌리고
        왜 안 됐는지 띄운다. 삼키면 「앱을 닫는 중…」 으로 굳어 버린다.
        """
        window.destroy()

    controller.close_window = 창닫기

    def 창깜빡이기() -> None:
        """받아쓰기가 다 끝났을 때 작업 표시줄의 이 앱을 깜빡인다.

        20분짜리를 걸어 놓고 딴 창을 보고 있으면 끝난 줄 모른다. 소리도 알림
        상자도 없이 **작업 표시줄만 깜빡인다** — 하던 일을 끊지 않고, 이 창이
        앞에 있으면 아무 일도 안 한다. 윈도우 밖에서는 조용히 아무것도 안 한다.

        pywebview 는 창 손잡이(HWND)를 안 내준다. 제목으로 찾는다.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, TITLE)
            if not hwnd or user32.GetForegroundWindow() == hwnd:
                return

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                            ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                            ("dwTimeout", wintypes.DWORD)]

            # FLASHW_TRAY(2) | FLASHW_TIMERNOFG(12): 작업 표시줄만, 앞으로 올 때까지
            정보 = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 2 | 12, 0, 0)
            user32.FlashWindowEx(ctypes.byref(정보))
        except Exception:
            pass       # 알리다 터지면 안 알리는 것으로 끝낸다

    controller.flash_window = 창깜빡이기

    # 놓은 파일의 실제 경로를 화면이 받게 한다. 판에 따라 이 인자가 없어서,
    # 없으면 끌어다 놓기만 포기하고 창은 그대로 띄운다. 파일 고르기 단추가 있다.
    #
    # **아이콘도 같은 이유로 조심해서 넘긴다.** pywebview 문서에는
    # 「GTK/QT 에서만 된다」 고 적혀 있는데, 윈도우 백엔드 코드를 열어 보면
    # `_state['icon']` 을 읽어서 `self.Icon` 에 넣는다. 안 주면 그 자리에서
    # `pythonw.exe` 의 아이콘을 뽑아 쓴다 — 지금 파이썬 아이콘이 뜨는 까닭이다.
    # 문서와 코드가 어긋나 있어서, 안 받는 판을 만나면 아이콘만 포기한다
    아이콘 = icon_path()
    for 인자 in ([{"drag_drop": True, "icon": str(아이콘)}] if 아이콘 else []) + [
        {"drag_drop": True}, {}
    ]:
        try:
            webview.start(debug=False, **인자)
            return 0
        except TypeError:
            continue
    webview.start(debug=False)
    return 0


def _open_dialog(webview):
    """파일 고르기 창을 여는 값. 판에 따라 이름이 다르다."""
    dialog = getattr(webview, "FileDialog", None)
    if dialog is not None and hasattr(dialog, "OPEN"):
        return dialog.OPEN
    return webview.OPEN_DIALOG
