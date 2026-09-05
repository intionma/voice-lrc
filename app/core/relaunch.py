"""앱을 껐다가, 할 일을 하고, 다시 켠다.

## 왜 이렇게 하나

업데이트(`git pull`)는 **지금 돌고 있는 `.py` 파일들을 갈아엎는다.** 파이썬은
이미 읽어들인 코드로 계속 도니까, 받는 중에 절반은 옛 코드 절반은 새 코드가
된다. 운 나쁘면 그 자리에서 죽고 왜 죽었는지도 모른다.

그래서 앱이 스스로 하지 않는다. **작은 배치를 하나 띄우고 앱은 죽는다.**
배치는 앱이 완전히 사라질 때까지 기다렸다가 할 일을 하고 앱을 다시 켠다.
ComfyUI 도 같은 방식이다.

## 반드시 지키는 것

**무슨 일이 있어도 앱을 다시 켠다.** `git pull` 이 실패해도 켠다. 안 켜지면
사용자는 앱이 사라진 채로 남는다 — 그게 제일 나쁘다.

**받는 것은 `--ff-only` 로만 한다.** 그냥 `git pull` 은 갈라져 있으면 merge 를
하고, 충돌이 나면 `.py` 안에 `<<<<<<<` 마커가 박힌다. 그러면 앱이 아예 안
켜진다 — 앱이 사라진 채로 돌아오지 않는 바로 그 상황이다. `--ff-only` 는
그럴 때 아무것도 안 건드리고 그냥 실패한다.

**앱이 안 죽으면 아무것도 하지 않는다.** 기다리다 지쳐서 그냥 진행하면
돌고 있는 앱 위에 파일을 덮어쓰게 되고, 다시 켜면 앱이 두 개가 된다.

**기다리는 자리에 파이프를 쓰지 않는다.** `tasklist | find` 가 실제 컴퓨터에서
영영 멈췄다. 창 제목이 `find "14792"` 인 채로 안 넘어갔다. 떼어 놓은 콘솔
안에서 파이프를 쓰면 `find` 가 안 닫히는 손잡이를 붙들고 기다릴 수 있다.
그러면 **아래의 60번 세는 안전장치도 소용없다** — 한 바퀴가 끝나야 세는데
그 안에서 멈추기 때문이다. 파일에서 읽으면 멈출 수가 없다.

**한 일을 파일에 적는다.** 앱이 죽어 있는 동안 벌어지는 일이라 화면에 띄울
데가 없다. 안 돌아오면 이 파일 말고는 볼 것이 없다.

**됐는지 안 됐는지를 한 줄로 남긴다**(`RESULT ok` / `RESULT failed`).
`git pull` 은 손댄 파일이 있으면 그냥 실패한다. 그래도 앱은 다시 켜지므로,
표시가 없으면 **아무 일도 안 일어난 것처럼 보인다.** 사용자는 판이 그대로인
이유를 모른 채 또 누른다.

## 배치에 경로를 안 적는다

배치 파일은 **ASCII 로만 적는다.** cmd.exe 가 한글이 든 배치 파일을 잘못
읽어서 엉뚱한 줄을 실행한다. 이 저장소의 다른 배치들도 같은 이유로 그렇다.

그런데 `C:\\Users\\철수\\...` 처럼 폴더 이름에 한글이 있으면 경로를 적는
순간 ASCII 가 아니게 된다. 그래서 **경로를 한 글자도 안 적는다.** 배치를
프로그램 폴더에 두고 `%~dp0` 로 자기 자리를 찾게 하면 된다 — `START.bat` 을
비롯한 다른 배치들이 쓰는 방법이다. 시험이 이것을 잡았다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 앱이 죽기를 이만큼 기다린다. 넘으면 아무것도 안 하고 물러난다.
# 사용자는 돌고 있는 앱을 그대로 쓰면 된다
기다림_한도 = 60

# 배치와 적은 것은 **프로그램 폴더**에 둔다. 그래야 `%~dp0` 로 서로를 찾는다
배치이름 = "_relaunch.bat"
적은것이름 = "_relaunch.log"

# 앱이 살아 있는지 본 것을 잠깐 적어 두는 곳. **파이프를 안 쓰려고** 둔다
본것이름 = "_transtext_wait.txt"

# 됐는지 안 됐는지를 적는 표. 창구가 이것을 보고 화면에 알린다
됨표 = "RESULT ok"
실패표 = "RESULT failed"


class 할일:
    """배치가 앱 대신 해 줄 일."""

    def __init__(self, id: str, 이름: str, 줄들: list[str]):
        self.id = id
        self.이름 = 이름          # 배치 안에 적힌다. ASCII 여야 한다
        self.줄들 = 줄들


# **`git pull` 은 여기 없다.** 앱이 직접 한다(`판올림하기`).
#
# 여태는 이 배치가 받았다. 그런데 앱이 꺼진 뒤에 벌어지는 일이라 **화면에
# 띄울 데가 없었다.** 배치가 뜨다 말면 사용자가 보는 것은 「앱만 꺼짐」 이
# 전부고, 판이 그대로인 까닭은 알 길이 없다. 실제로 그렇게 됐다 —
# 「업데이트하고 다시 켜기」 를 눌러도 판이 그대로였고, 손으로 배치를 누르면
# 됐다. 그래서 **받는 것은 앱이 눈앞에서 하고**, 배치는 뒷정리만 맡는다.
업데이트 = 할일(
    "update", "update",
    [
        # **받은 코드가 새 라이브러리를 요구할 수 있다.**
        #
        # 코드는 새것인데 라이브러리가 처음 깔던 그대로면 그 기능이 영영
        # 안 켜진다. 묶어 넣기(faster-whisper 1.1)가 딱 그런 경우였다.
        #
        # 이것은 앱이 죽은 뒤에 해야 한다. 도는 프로세스가 쓰는 꾸러미를
        # pip 이 갈아엎으면 그 자리에서 죽는다.
        #
        # 실패해도 넘어간다. 인터넷이 끊겨 있을 수 있고, 그렇다고 이미 받은
        # 코드를 되돌릴 수는 없다. 앱은 예전 라이브러리로도 돌아간다
        'echo Updating libraries... >> "%LOG%"',
        'if exist ".venv\\Scripts\\python.exe" goto haveenv',
        f'echo {실패표} .venv was not found. >> "%LOG%"',
        "goto donelibs",
        ":haveenv",
        '".venv\\Scripts\\python.exe" -m pip install -q -r app\\requirements.txt'
        ' >> "%LOG%" 2>&1',
        "if errorlevel 1 goto libsbad",
        f'echo {됨표} >> "%LOG%"',
        "goto donelibs",
        ":libsbad",
        f'echo {실패표} libraries were left as they were. >> "%LOG%"',
        ":donelibs",
    ],
)

# `8_fix_gpu.bat` 와 같은 일을 한다. pip 가 넣어 둔 CUDA DLL 을 python.exe
# 옆으로 옮긴다 — 윈도우가 site-packages 안을 자동으로 뒤지지 않는다
그래픽카드고치기 = 할일(
    "fix_gpu", "fix gpu",
    [
        "set NVIDIA=.venv\\Lib\\site-packages\\nvidia",
        'if not exist "%NVIDIA%" goto nocuda',
        '    for /d %%D in ("%NVIDIA%\\*") do (',
        '        if exist "%%D\\bin\\*.dll" (',
        '            echo   %%~nxD >> "%LOG%"',
        '            copy /Y "%%D\\bin\\*.dll" ".venv\\Scripts\\" >> "%LOG%" 2>&1',
        "        )",
        "    )",
        'if exist ".venv\\Scripts\\cublas64_12.dll" goto gpuok',
        f'echo {실패표} cublas64_12.dll was not copied. >> "%LOG%"',
        "goto donefix",
        ":gpuok",
        f'echo {됨표} cublas64_12.dll is in place. >> "%LOG%"',
        "goto donefix",
        ":nocuda",
        f'echo {실패표} CUDA packages are not installed. >> "%LOG%"',
        ":donefix",
    ],
)


할일들 = {것.id: 것 for 것 in (업데이트, 그래픽카드고치기)}


def 뿌리() -> Path:
    """`START.bat` 이 있는 곳."""
    return Path(__file__).resolve().parents[2]


def 적은것(뿌리길: Path | None = None) -> Path:
    """배치가 한 일을 적는 곳."""
    return (뿌리길 or 뿌리()) / 적은것이름


def 마지막결과(글: str) -> dict:
    """적어 둔 것에서 **마지막으로 한 일**이 됐는지 읽는다.

    `git pull` 은 손댄 파일이 있으면 그냥 실패한다. 그래도 앱은 다시 켜지므로
    표시가 없으면 아무 일도 안 일어난 것처럼 보인다.
    """
    무엇 = ""
    결과: dict | None = None
    for 줄 in (글 or "").splitlines():
        벗긴것 = 줄.strip()
        if 벗긴것.startswith("----") and 벗긴것.endswith("----"):
            무엇 = 벗긴것.strip("- ").strip()
            결과 = None          # 새 회차가 시작됐다. 앞의 결과는 잊는다
        elif 벗긴것.startswith(실패표):
            결과 = {"job": 무엇, "ok": False,
                    "why": 벗긴것[len(실패표):].strip()}
        elif 벗긴것.startswith(됨표):
            결과 = {"job": 무엇, "ok": True, "why": ""}
    return 결과 or {}


def 배치글(무엇: 할일, pid: int) -> str:
    """띄울 배치의 내용. ASCII 로만 적는다.

    **경로를 한 글자도 안 적는다.** 자기 자리(`%~dp0`)에서 출발한다.

    따로 떼어 둔 이유는 **이것만 시험할 수 있어야** 하기 때문이다. 실제로
    껐다 켜지는지는 윈도우에서만 확인할 수 있다.
    """
    줄 = [
        "@echo off",
        "chcp 65001 >nul",
        f"title trans-text {무엇.이름}",
        'cd /d "%~dp0"',
        "",
        f"set LOG=%~dp0{적은것이름}",
        f'echo ---- {무엇.이름} ---- >> "%LOG%"',
        "",
        "REM Say things on screen too. A black window with nothing in it",
        "REM looks frozen even when it is working, and there is no way to",
        "REM tell. That is exactly how this looked when it did hang.",
        "echo trans-text",
        "echo.",
        "echo Waiting for the app to close...",
        "REM Give the app a moment to close before checking anything.",
        "REM If tasklist is blocked the check below fails open, and without",
        "REM this wait we would touch files while the app is still running.",
        "ping -n 4 127.0.0.1 >nul",
        "",
        "REM Wait until the app is really gone. Do nothing if it stays.",
        "REM",
        "REM NO PIPE HERE. \"tasklist | find\" hung forever on a real machine:",
        "REM the window sat at find \"<pid>\" and never came back. A pipe inside",
        "REM a detached console can leave find waiting on a handle that never",
        "REM closes, and then the counter below never advances either - the",
        "REM safety net only works if each round finishes. Reading from a file",
        "REM cannot block.",
        f'set SEEN=%TEMP%\\{본것이름}',
        "set N=0",
        ":wait",
        f'tasklist /FI "PID eq {pid}" /NH /FO CSV > "%SEEN%" 2>nul',
        "if errorlevel 1 goto ready",
        f'find "{pid}" < "%SEEN%" >nul 2>nul',
        "if errorlevel 1 goto ready",
        "set /a N+=1",
        f"if %N% GTR {기다림_한도} goto stuck",
        "ping -n 2 127.0.0.1 >nul",
        "goto wait",
        "",
        ":stuck",
        'del "%SEEN%" >nul 2>nul',
        'echo The app did not close. Nothing was done. >> "%LOG%"',
        "echo.",
        "echo The app did not close, so nothing was done.",
        "echo The app is still running. You can close this window.",
        "echo.",
        "pause",
        "exit",
        "",
        ":ready",
        'del "%SEEN%" >nul 2>nul',
        f"echo Working: {무엇.이름}",
    ]
    줄 += 무엇.줄들
    줄 += [
        "",
        "REM Always start the app again, even if the job above failed.",
        "echo.",
        "echo Starting the app again...",
        'start "" "%~dp0START.bat"',
        "exit",
    ]
    return "\r\n".join(줄) + "\r\n"


def 할수있나(무엇: 할일, 뿌리길: Path | None = None) -> str:
    """못 하는 일이면 왜 못 하는지. 할 수 있으면 빈 글."""
    뿌리길 = 뿌리길 or 뿌리()
    if not (뿌리길 / "START.bat").exists():
        return ("START.bat 을 찾지 못했습니다. 다시 켜 줄 방법이 없어서 "
                "하지 않습니다.")
    if 무엇.id == "update" and not (뿌리길 / ".git").exists():
        return ("이 폴더는 git 으로 받은 것이 아니라 업데이트할 수 없습니다. "
                "git clone 으로 다시 받아 주세요.")
    if 무엇.id == "fix_gpu" and not (뿌리길 / ".venv").exists():
        return ".venv 가 없습니다. START.bat 을 한 번 돌려 주세요."
    return ""


def 띄우기(무엇: 할일, 뿌리길: Path | None = None, 띄우개=None) -> Path:
    """배치를 만들어 띄운다. 만든 배치의 자리를 돌려준다.

    띄운 뒤에 **부르는 쪽이 앱을 닫아야** 배치가 일을 시작한다.
    """
    뿌리길 = 뿌리길 or 뿌리()
    배치 = 뿌리길 / 배치이름
    # ASCII 가 아닌 글자가 하나라도 있으면 여기서 터진다. 조용히 `?` 로
    # 바꿔 두면 배치가 엉뚱한 줄을 실행하고 앱은 안 돌아온다
    배치.write_text(배치글(무엇, os.getpid()), encoding="ascii")

    (띄우개 or _띄우기)(배치)
    return 배치


def _띄우기(배치: Path) -> None:
    """배치를 앱과 **떼어서, 제 창을 주어** 띄운다.

    떼지 않으면 앱이 죽을 때 같이 죽는다. 그러면 아무 일도 안 일어나고
    앱만 사라진다.

    **창을 준다(`CREATE_NEW_CONSOLE`).** 예전에는 `DETACHED_PROCESS` 였다.
    그것은 「콘솔을 만들지도, 물려받지도 마라」 는 뜻이라 **배치에 화면이
    아예 없었다.** 그런데 배치는 `echo` 로 진행을 말하도록 적혀 있다 — 짝이
    안 맞는다. 게다가 앱은 콘솔 없는 `pythonw` 로 도는 일이 많아서, 콘솔 없는
    부모가 콘솔 없이 `cmd` 를 띄우는 꼴이 된다. 그 조합에서 배치가 뜨다 마는
    일이 실제로 있었다 — 앱만 꺼지고 판올림은 안 됐다.

    창이 뜨는 편이 오히려 낫다. 사용자가 **무엇이 되고 있는지 본다.**

    **윈도우가 아니면 아예 안 띄운다.** 배치 파일을 `sh` 에 물리면 줄마다
    엉뚱하게 해석해서 `nul` 이나 `%LOG%` 같은 파일을 만들어 놓는다. 실제로
    시험을 돌리다 그런 쓰레기가 생겼다. 될 리 없는 것은 시작하지 않는다.
    """
    if sys.platform != "win32":
        raise RuntimeError("껐다 켜기는 윈도우에서만 됩니다.")
    깃발 = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        ["cmd", "/c", str(배치)],
        cwd=str(배치.parent),
        creationflags=깃발,
        close_fds=True,
    )


def 판올림하기(뿌리길: Path | None = None, 시간제한: float = 120.0) -> dict:
    """**앱 안에서** `git pull --ff-only` 를 돌린다. 무엇이 됐는지 돌려준다.

    여태 이것은 배치가 했다. 앱이 꺼진 뒤에 벌어지는 일이라 **화면에 띄울
    데가 없었고**, 배치가 뜨다 말면 사용자는 앱만 꺼지는 것을 보고 끝이었다.
    실제로 그렇게 됐다 — 「업데이트하고 다시 켜기」 를 눌러도 판이 그대로였고,
    손으로 배치를 누르면 됐다.

    받는 것 자체는 앱이 해도 된다. 파이썬은 이미 읽어 둔 코드로 계속 돌기
    때문이다. 위험한 것은 **받은 뒤에도 계속 일하는 것**이다 — 그때 새로
    읽히는 파일만 새 코드가 되어 반쪽짜리가 된다. 그래서 부르는 쪽은 이것을
    부른 뒤에 **곧바로 앱을 닫는 것 말고 아무것도 하지 않는다.**

    `--ff-only` 인 까닭은 위 문서에 적어 두었다.
    """
    뿌리길 = 뿌리길 or 뿌리()
    if not (뿌리길 / ".git").exists():
        return {"ok": False, "말": "git 으로 받은 폴더가 아니라 판올림할 수 없습니다.",
                "글": ""}
    try:
        난것 = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(뿌리길), capture_output=True, text=True,
            timeout=시간제한, errors="replace",
        )
    except FileNotFoundError:
        return {"ok": False, "말": "git 을 찾지 못했습니다. git 을 깔아야 합니다.",
                "글": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "말": "판올림이 너무 오래 걸려 그만뒀습니다.", "글": ""}
    except Exception as error:      # 무엇이 나오든 앱이 죽으면 안 된다
        return {"ok": False, "말": f"판올림하지 못했습니다: {error}", "글": ""}

    글 = ((난것.stdout or "") + (난것.stderr or "")).strip()
    if 난것.returncode != 0:
        # 손댄 파일이 있으면 `--ff-only` 는 아무것도 안 건드리고 실패한다.
        # **그것을 그대로 말해 준다.** 예전에는 이 글이 어디에도 안 보였다
        return {"ok": False, "말": "판올림하지 못했습니다. 아무것도 바뀌지 않았습니다.",
                "글": 글}
    if "Already up to date" in 글 or "이미 최신" in 글:
        return {"ok": True, "바뀜": False, "말": "이미 최신입니다.", "글": 글}
    return {"ok": True, "바뀜": True, "말": "새 판을 받았습니다.", "글": 글}
