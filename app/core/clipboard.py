"""윈도우 클립보드가 바뀌면 알려 준다.

## 왜 숨은 창을 안 만드는가

처음에는 `AddClipboardFormatListener` 로 숨은 창에 알림을 걸었다.
**그 코드는 윈도우에서 한 번도 돌아 본 적이 없었고, 실제로 안 됐다.**

`CreateWindowExW` 에 `HWND_MESSAGE`(-3)를 넘기는데 `argtypes` 를 안 정해
두면, 64비트에서 ctypes 가 그것을 32비트 정수로 밀어 넣는다. 받는 쪽은
64비트 포인터라 값이 어긋나 창이 안 만들어진다. 창이 없으면 알림도 없다.
그런데 `켜기()` 는 이미 「켜짐」 이라고 답한 뒤라, **화면에는 초록 불이
켜져 있는데 아무것도 안 오는** 상태가 됐다. 제일 나쁜 종류다.

## 지금 방식 — 번호만 물어본다

윈도우는 클립보드가 바뀔 때마다 번호를 하나씩 올린다
(`GetClipboardSequenceNumber`). 그 번호만 0.4초마다 물어본다.

    - 클립보드를 **여는 것이 아니다.** 남이 복사하는 것을 방해하지 않는다.
      0.3초마다 클립보드를 열어 읽는 방식이 엑셀 복사를 깨뜨리는 것이다
    - 창도, 메시지 루프도, 콜백도 없다. 어긋날 자리가 없다
    - 번호가 바뀐 때에만 실제로 열어서 읽는다

번호가 안 바뀌면 아무 일도 안 한다. 사람이 복사한 그 순간에만 한 번 읽는다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

# 윈도우 메시지 번호. `winuser.h` 의 값이다
WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY = 0x0002


@dataclass
class 감시:
    """클립보드가 바뀌면 `알림(글)` 을 부른다.

    앱 어디에도 안 붙어 있다. 부르는 쪽이 무엇을 할지 정한다.
    """

    알림: Callable[[str], None]
    # 왜 안 켜졌는지. 화면이 그대로 보여 준다
    까닭: str = ""
    켜짐: bool = False
    _멈춤: threading.Event = field(default_factory=threading.Event)
    _일꾼: threading.Thread | None = None
    # 마지막으로 알린 글. 같은 것을 두 번 알리지 않는다 —
    # 윈도우는 한 번 복사에 알림을 여러 번 주는 판이 있다
    _마지막: str = ""
    _붙었나: threading.Event = field(default_factory=threading.Event)

    # ---- 켜고 끄기 ----

    def 켜기(self) -> bool:
        """켜졌으면 `True`. 못 켜면 까닭을 남기고 `False` — 앱은 그대로 돈다."""
        if self.켜짐:
            return True
        try:
            import ctypes  # noqa: F401
        except ImportError as 오류:          # pragma: no cover - 있을 수 없다
            self.까닭 = f"ctypes 를 못 씁니다: {오류}"
            return False

        import sys
        if not sys.platform.startswith("win"):
            self.까닭 = "윈도우에서만 됩니다."
            return False

        # **일꾼이 실제로 붙기 전에는 「켜짐」 이라고 하지 않는다.**
        # 예전에는 여기서 바로 True 로 두어서, 안쪽이 실패해도 화면에는
        # 초록 불이 켜져 있었다. 「본다고 해 놓고 안 보는」 상태였다
        self._멈춤.clear()
        self._붙었나 = threading.Event()
        self._일꾼 = threading.Thread(target=self._돌기, daemon=True)
        self._일꾼.start()
        # 번호를 한 번 물어보는 데 걸리는 시간이다. 안 되면 곧바로 안다
        self._붙었나.wait(2.0)
        return self.켜짐

    def 끄기(self) -> None:
        self._멈춤.set()
        self.켜짐 = False

    def 상태(self) -> dict[str, object]:
        """화면에 그대로 실어 보낼 수 있는 모양."""
        return {"켜짐": self.켜짐, "까닭": self.까닭}

    # ---- 안쪽 ----

    def _알릴까(self, 글: str) -> None:
        """같은 글을 두 번 알리지 않는다."""
        글 = 글 or ""
        if not 글.strip() or 글 == self._마지막:
            return
        self._마지막 = 글
        try:
            self.알림(글)
        except Exception:      # pragma: no cover - 부르는 쪽이 터져도 감시는 산다
            pass

    def _돌기(self) -> None:                 # pragma: no cover - 윈도우에서만 돈다
        """클립보드 번호가 바뀌면 그때만 읽는다.

        **이 함수는 이 컨테이너에서 돌아 본 적이 없다.** 다만 쓰는 것이
        `GetClipboardSequenceNumber` 하나뿐이라 어긋날 자리가 거의 없다 —
        인자도 없고, 돌려주는 것은 그냥 부호 없는 정수다.
        """
        import ctypes
        from ctypes import wintypes

        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            번호받기 = user32.GetClipboardSequenceNumber
            번호받기.restype = wintypes.DWORD
            번호받기.argtypes = []
        except (OSError, AttributeError) as 오류:
            self.까닭 = f"클립보드 번호를 못 읽습니다: {오류}"
            self.켜짐 = False
            self._붙었나.set()
            return

        try:
            지난번호 = 번호받기()
        except Exception as 오류:
            self.까닭 = f"클립보드 번호를 못 읽습니다: {오류}"
            self.켜짐 = False
            self._붙었나.set()
            return

        # 여기까지 왔으면 진짜로 보고 있는 것이다
        self.까닭 = ""
        self.켜짐 = True
        self._붙었나.set()

        try:
            while not self._멈춤.wait(0.4):
                try:
                    이번번호 = 번호받기()
                except Exception:
                    continue          # 한 번 못 읽었다고 감시를 접지 않는다
                if 이번번호 == 지난번호:
                    continue          # **아무것도 안 한다.** 클립보드를 안 연다
                지난번호 = 이번번호
                self._알릴까(읽기())
        finally:
            self.켜짐 = False

def 읽기() -> str:
    """지금 클립보드에 든 글. 못 읽으면 빈 글.

    **모든 함수의 인자·반환형을 정한다.** 안 정하면 ctypes 가 64비트
    손잡이를 32비트 정수로 잘라 버린다. 그 잘린 값을 `GlobalLock` 에
    넘기면 엉뚱한 자리를 가리키고, 거기를 글자로 읽는 순간 **파이썬
    오류가 아니라 프로세스가 통째로 죽는다.** try/except 로 못 잡는다.

    실제로 그렇게 겪었다 — 번역을 복사하면 앱이 튕겼다.

    **못 읽는 것이 흔하다.** 다른 프로그램이 붙잡고 있으면 열리지 않고,
    글이 아니라 그림일 수도 있다. 그때 터지면 감시가 죽는다.
    """
    import sys
    if not sys.platform.startswith("win"):
        return ""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:                      # pragma: no cover
        return ""

    try:                                     # pragma: no cover - 윈도우에서만
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # **여기가 핵심이다.** 손잡이는 64비트다
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        CF_UNICODETEXT = 13
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        if not user32.OpenClipboard(None):
            return ""
        try:
            손잡이 = user32.GetClipboardData(CF_UNICODETEXT)
            if not 손잡이:
                return ""
            자리 = kernel32.GlobalLock(손잡이)
            if not 자리:
                return ""
            try:
                return ctypes.c_wchar_p(자리).value or ""
            finally:
                kernel32.GlobalUnlock(손잡이)
        finally:
            user32.CloseClipboard()
    except Exception:                        # pragma: no cover
        return ""
