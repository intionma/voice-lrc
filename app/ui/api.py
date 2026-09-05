"""화면이 부르는 창구.

화면(HTML)은 여기 있는 것만 부른다. 창을 띄우는 부품(pywebview)에 기대지 않으므로
창 없이도 시험할 수 있다.

무거운 일은 따로 도는 일꾼에게 맡기고 바로 돌려준다. 받아쓰기가 12분씩 걸리는데
그동안 창이 얼어 있으면 사용자는 프로그램이 죽은 줄 안다. 화면은 짧은 간격으로
`state()`를 물어보며 진행 상황을 그린다.

번역은 파일 하나씩이 아니라 **작품 단위 대기열**로 흐른다. 화면은 `prompt()`와
`submit()`만 되풀이하면 되고, 지금 몇 번 파일인지 알 필요가 없다.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import platform
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from app.core import (
    clip,
    compare as 견주기,
    dlsite,
    minor_terms,
    exchange,
    gpu,
    lrc,
    ollama,
    catch,
    clipboard,
    preset as presets,
    route as routes,
    log,
    relaunch,
    model_notes,
    names as names_store,
    providers,
    quality,
    settings as settings_store,
    titles as titles_store,
    wordbook,
    transcribe as asr,
    translate as translate_module,
    update,
)
from app.core.job import (
    Job, Pipeline, Stage, cache_dir, cache_key, load_transcript, save_transcript,
    강도가_다르다는_말,
)
from app.core.queue import Group, build_groups, 칸시작, 번호표주기, 트랙번호_매기기


# 어느 차례에 몇 개가 있는지 보여 줄 때 쓰는 이름
_차례이름 = {
    "대기": "받아쓰기 전",
    "받아쓰기": "받아쓰는 중",
    "다시훑기": "다시 보는 중",
    "번역": "번역할 차례",
    "자막": "자막 만드는 중",
    "완료": "끝남",
    "건너뜀": "말이 없어 건너뜀",
    "실패": "실패",
}


def _작품상태(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """이 작품이 어디까지 왔는지 한눈에.

    트랙이 아홉이면 어느 것이 안 됐는지 카드를 하나씩 봐야 알 수 있었다.
    """
    센것: dict[str, int] = {}
    for job in jobs:
        센것[job["stage"]] = 센것.get(job["stage"], 0) + 1

    전체 = len(jobs)
    끝난것 = 센것.get("완료", 0)
    건너뜀 = 센것.get("건너뜀", 0)
    실패 = 센것.get("실패", 0)
    도는중 = any(job["stage"] in ("받아쓰기", "다시훑기", "자막") for job in jobs)

    # 말이 없어 건너뛴 것도 더 할 일이 없다. 이것 때문에 영영 "안 끝남" 이면
    # 효과음 트랙 하나가 낀 작품은 끝나는 날이 오지 않는다
    if 끝난것 + 건너뜀 == 전체:
        한마디 = "다 끝났습니다"
    elif 도는중:
        한마디 = "돌아가는 중"
    elif 센것.get("번역"):
        한마디 = f"번역할 것 {센것['번역']}개"
    elif 센것.get("대기") == 전체:
        한마디 = "받아쓰기 전"
    else:
        한마디 = f"{끝난것}/{전체} 끝남"
    if 건너뜀:
        한마디 += f" · 말이 없어 건너뜀 {건너뜀}개"
    if 실패:
        한마디 += f" · 실패 {실패}개"

    return {
        "done": 끝난것,
        "total": 전체,
        "skipped": 건너뜀,
        "failed": 실패,
        "running": 도는중,
        "label": 한마디,
        "counts": [
            {"stage": 차례, "name": _차례이름.get(차례, 차례), "count": 수}
            for 차례, 수 in 센것.items()
        ],
    }


def _지우기(folder: Path) -> int:
    """폴더 안의 파일을 지우고 몇 개였는지 돌려준다."""
    if not folder.is_dir():
        return 0
    센것 = 0
    for 파일 in folder.glob("*"):
        try:
            if 파일.is_file():
                파일.unlink()
                센것 += 1
        except OSError:
            pass
    return 센것


_한글 = re.compile(r"[가-힣]")


class _그만둠(Exception):
    """사용자가 무른 것. 오류가 아니다."""


def _한글있나(줄: str) -> bool:
    return bool(_한글.search(줄))


class Controller:
    """작업 목록을 들고 일꾼을 부린다."""

    def __init__(
        self,
        *,
        make_pipeline: Callable[[dict[str, Any]], Pipeline] | None = None,
        run_in_background: bool = True,
        fetch_work: Callable[..., Any] = dlsite.fetch,
    ):
        self.jobs: list[Job] = []
        self.groups: list[Group] = []
        # 품번 → 작품 정보. 파일을 넣자마자 가져와서 표지와 태그를 보여 준다
        self.works: dict[str, dlsite.Work] = {}
        self.settings = settings_store.load()
        self.busy = False
        self.notice = ""
        # 새 판이 몇 개 나왔나. 켤 때 뒤에서 한 번만 본다.
        # **모르면 0 이다** — 인터넷이 없거나 ZIP 으로 받았을 수 있다
        self._새판수 = 0
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._make_pipeline = make_pipeline or (lambda s: Pipeline(settings=s))
        self._run_in_background = run_in_background
        self._fetch_work = fetch_work
        # 지금 보고 있는 묶음. 앞뒤로 옮겨 다닐 수 있다
        self._at = 0
        # 창을 만든 쪽이 끼워 준다. 창 없이 시험할 때는 비어 있다
        self.file_picker: Callable[[], list[str]] | None = None
        # 앱을 껐다 켜는 것들이 쓴다. 창이 없으면 끌 것도 없다
        self.close_window: Callable[[], None] | None = None
        # 다 끝났을 때 작업 표시줄을 깜빡이는 것. 창 쪽이 걸어 준다
        self.flash_window: Callable[[], None] | None = None
        # 이미 있는 `.lrc` 를 갈아끼워도 좋다고 한 작품들.
        #
        # 열네 묶음짜리 트랙에 묶음마다 물어보면 못 쓴다. 작품마다 한 번만
        # 묻고, 그 뒤로는 그 작품 안에서 안 묻는다
        self._덮어써도됨: set[str] = set()
        # **트랙 하나만 그만두라고 한 것.** 전역 `_stop` 은 판을 통째로 세운다.
        # 「이 트랙만 그만」 은 그것과 다르다 — 나머지는 계속 돌아야 한다
        self._그만둔것: set[int] = set()
        # ---- 클립보드 감시 ----
        #
        # **「복사」 를 누른 뒤부터만 본다.** 앱을 켜자마자 클립보드를 보면
        # 남의 것을 볼 뿐이고, 사람은 자기가 감시당하는 줄도 모른다.
        # 복사를 눌렀다는 것은 「이제 답을 받아 올 참이다」 라는 뜻이다
        self._감시 = clipboard.감시(알림=self._클립보드가바뀜)
        # 방금 우리가 내보낸 글들. 그대로 돌아오면 메아리다.
        # 트랙마다 하나씩만 들고 있으면 된다
        self._내보낸글: dict[str, str] = {}
        # 감시가 넣은 것. 되돌리기가 이것을 되뇐다
        self._감시가넣은것: dict[str, Any] | None = None
        # **되돌릴 것 한 자리.** 담아 둔 것을 지우는 일은 전부 여기에 먼저
        # 찍어 두고 지운다. 화면은 토스트로 8초 동안 물릴 기회를 준다.
        #
        # 여태는 같은 문제를 네 가지로 막고 있었다 — 되돌리기 토스트 한 군데,
        # 두 번 누르기 넷, `confirm()` 팝업 셋, **아무것도 안 하는 것 다섯.**
        # 제일 좋은 것을 제일 안 썼다. 하나로 모은다.
        #
        # 한 자리뿐이라 **마지막 것만** 물릴 수 있다. 여러 개를 쌓으면 무엇이
        # 물려질지 사람이 못 셈한다
        self._되돌릴것: dict[str, Any] | None = None
        # 화면에 보여 줄 마지막 판정. **왜 안 받았는지 보여야** 한다
        self._감시말 = ""
        # 감시가 마지막으로 넣은 트랙 자리와, 넣은 횟수. 화면이 그 줄로
        # 데려가 번쩍이는 데 쓴다
        self._감시자리 = -1
        self._감시횟수 = 0
        # 내 컴퓨터 AI 로 번역할 트랙 줄. 넣은 차례대로 돈다
        self._번역줄: list[str] = []
        self._도는트랙 = ""
        self._줄일꾼돎 = False
        # 번역 모델을 받는 중인지. 9GB 라 오래 걸린다
        self._pulling = False
        # 칸을 미리 다 만들어 둔다. 일꾼이 나중에 키를 더하면 읽는 쪽에서
        # "dictionary changed size during iteration" 으로 터진다
        self._pull: dict[str, Any] = {
            "model": "", "ratio": 0.0, "message": "", "done": True, "ok": True,
        }
        # 복붙 화면에서 내 컴퓨터 AI 로 넘긴 번역이 도는 중인지
        # 작품 열쇠 → 그 작품에 내준 제목 첫 번호. **한 번 내주면 안 바꾼다.**
        # 복사해 가서 붙여넣는 사이에 목록이 바뀌어도 답이 제 작품을 찾아간다
        self._제목번호: dict[str, int] = {}
        self._local_run: dict[str, Any] = {
            "busy": False, "done": 0, "total": 0, "message": "",
            "finished": True, "ok": True,
        }
        # **실수로 눌렀을 때 무를 수 있어야 한다.** 내 컴퓨터 AI 번역은
        # 몇 분씩 걸리는데, 시작하면 끝날 때까지 붙잡혀 있었다
        self._local_cancel = threading.Event()
        # 일꾼이 쓰는 흐름. 받아쓰기 모델을 내려야 할 때 여기로 부른다
        self._pipeline: Pipeline | None = None
        # 강도를 견주는 중인지. 여기도 칸을 미리 다 만들어 둔다
        self._comparing = False
        self._compare: dict[str, Any] = {
            "done": True, "ok": True, "message": "", "result": None,
            "left": "", "right": "",
        }
        # 앞 2분만 미리 받아쓰는 중인지
        self._previewing = False
        self._preview: dict[str, Any] = {
            "done": True, "ok": True, "message": "", "lines": [], "index": -1,
        }
        # 고른 트랙만 받아쓰는 판. 비어 있으면 전부 받아쓴다
        self._only: set[int] = set()

        # 지난번에 넣어 둔 음원을 되살린다. 껐다 켜면 목록이 통째로 사라져서
        # 스무 개를 다시 끌어다 놓아야 했다
        self._목록되살리기()

    # ---- 넣어 둔 목록을 담아 두기 ----
    #
    # 앱을 껐다 켜면 넣어 둔 음원이 통째로 사라졌다. 받아쓴 결과는 담아 두고
    # 있었는데 **목록이 메모리에만** 있었기 때문이다. 스무 개를 끌어다 놓고
    # 하루 뒤에 켜면 처음부터 다시 넣어야 했다.

    def _목록자리(self) -> Path:
        return settings_store.config_dir() / "queue.json"

    def _목록담기(self) -> None:
        """넣어 둔 음원 경로를 남긴다. 실패해도 넘어간다."""
        try:
            자리 = self._목록자리()
            자리.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                경로들 = [str(job.audio) for job in self.jobs]
            임시 = 자리.with_suffix(".json.tmp")
            임시.write_text(
                json.dumps({"files": 경로들}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            임시.replace(자리)
        except OSError as error:
            log.write("목록", "담아 두지 못함", 까닭=str(error))

    def _목록되살리기(self) -> None:
        """지난번에 넣어 둔 음원을 되살린다.

        **파일이 그 자리에 없으면 조용히 건너뛴다.** 옮기거나 지운 것을 두고
        「없습니다」 를 늘어놓으면 켤 때마다 잔소리가 된다.

        **여기서는 무슨 일이 있어도 터지지 않는다.** 이것은 창구를 만들 때
        돌아서, 여기서 터지면 **앱이 아예 안 켜진다.** 담아 둔 목록은 편의일
        뿐인데 그것 때문에 프로그램을 못 쓰게 되면 안 된다. 실제로 담아 둔
        파일의 모양이 다르면(리스트로 저장돼 있으면) 그대로 터졌다.
        """
        try:
            self._목록읽기()
        except Exception as error:            # noqa: BLE001 — 켜는 것이 먼저다
            log.error("담아 둔 목록을 되살리지 못했다. 빈 목록으로 켠다", error)
            self.jobs = []

    def _목록읽기(self) -> None:
        try:
            자리 = self._목록자리()
            if not 자리.is_file():
                return
            데이터 = json.loads(자리.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(데이터, dict):
            return

        살아있는것 = []
        사라진것 = 0
        for 경로 in (데이터.get("files") or []):
            길 = Path(str(경로))
            if not 길.is_file():
                사라진것 += 1
                continue
            job = Job(audio=길)
            self._담아둔것되살리기(job)
            살아있는것.append(job)

        if not 살아있는것 and not 사라진것:
            return
        self.jobs = 살아있는것
        log.write("목록", "되살림", 살린것=len(살아있는것), 사라진것=사라진것 or None)
        if 사라진것:
            self.notice = f"{사라진것}개는 그 자리에 없어서 뺐습니다."
        self._rebuild_queue()
        self._look_up_works()

    def _담아둔것되살리기(self, job: Job) -> bool:
        """이 파일을 전에 받아썼으면 그 결과를 그대로 올린다.

        **넣는 순간에 한다.** 예전에는 「받아쓰기」를 눌러야만 알 수 있었다.
        이미 다 해 둔 파일인지 아닌지를 보려고 매번 눌러 봐야 했고, 누르면
        모델부터 올라가서 그것만 20초였다.
        """
        if not load_transcript(job):
            return False

        # **어느 강도로 받아쓴 것인지 본다.**
        #
        # 지금 고른 강도와 다르면 여기서 끝난 것으로 치면 안 된다. 「강도가
        # 다르면 다시 받아쓴다」 는 판단은 `Pipeline.transcribe` 안에 있는데,
        # 여기서 차례를 넘겨 버리면 그 판단이 아예 안 돈다. 강도를 올려도
        # 옛 결과가 그대로 쓰이게 된다 — 시험이 그것을 잡았다.
        #
        # 붙여넣은 번역이 있으면 다르다. 다시 받아쓰면 줄 번호가 바뀌어
        # 그동안 붙여넣은 것이 통째로 쓸모없어진다. 그때는 그대로 쓴다.
        지금강도 = presets.get(str(self.settings.get("asr", {}).get("preset", "")))
        강도가다름 = bool(job.cached_preset) and job.cached_preset != 지금강도.id
        if 강도가다름 and job.translations:
            # 다시 받아쓰면 줄 번호가 바뀌어 붙여넣은 것이 통째로 쓸모없어진다.
            # 그대로 쓰되 **왜 그대로인지** 알려 준다
            job.hint = 강도가_다르다는_말(presets.get(job.cached_preset), 지금강도)
        if 강도가다름 and not job.translations:
            옛강도 = presets.get(job.cached_preset)
            job.message = (
                f"전에 「{옛강도.name}」로 받아쓴 것이 있습니다. "
                f"지금 강도는 「{지금강도.name}」라 다시 받아씁니다"
            )
            job.segments = []      # 차례를 넘기지 않는다. `start` 가 다시 받아쓴다
            job.translations = {}
            return False

        job.report = quality.inspect(
            job.segments, job.duration_sec,
            translation=job.translations, work=job.work, uncovered=job.uncovered,
        )
        빠진줄 = sum(
            1 for s in job.segments
            if not str(job.translations.get(s["index"], "")).strip()
        )
        자막 = job.audio.with_suffix(".lrc")
        if 빠진줄 == 0 and 자막.is_file():
            job.stage = Stage.완료
            job.output = 자막
            job.message = "이미 다 됐습니다"
        else:
            job.stage = Stage.번역
            남은말 = f" · 번역할 줄 {빠진줄}" if 빠진줄 else ""
            job.message = f"받아쓴 것이 있습니다 ({len(job.segments)}줄){남은말}"
        job.progress = 1.0
        return True

    # ---- 파일 넣기 ----

    def pick_files(self) -> list[str]:
        """윈도우 기본 파일 고르기 창을 연다.

        끌어다 놓기로 경로를 못 받는 환경이 있어 이 길이 항상 있어야 한다.
        """
        if self.file_picker is None:
            return []
        return list(self.file_picker() or [])

    def add_files(self, paths: list[str]) -> dict[str, Any]:
        """끌어다 놓았거나 골라 온 것을 대기열에 넣는다."""
        found = asr.collect_audio([Path(p) for p in paths])
        if not found:
            self.notice = "음원 파일을 찾지 못했습니다. mp3, wav, m4a 등을 넣어 주세요."
            return self.state()

        def 같은지(길: Path) -> str:
            try:
                return str(길.resolve())
            except OSError:
                return str(길)  # 이름이 너무 길거나 경로가 이상해도 넘어간다

        이미있음 = {같은지(job.audio) for job in self.jobs}
        added = 0
        with self._lock:
            새것 = []
            for path in found:
                if 같은지(path) in 이미있음:
                    continue
                새것.append(Job(audio=path))
                added += 1
            self.jobs.extend(새것)

        # **넣는 순간에** 담아 둔 받아쓰기를 올린다. 예전에는 「받아쓰기」를
        # 눌러야만 이미 해 둔 것인지 알 수 있었다
        되살린것 = sum(1 for job in 새것 if self._담아둔것되살리기(job))

        한마디 = f"{added}개를 넣었습니다." if added else "이미 들어와 있는 파일입니다."
        if 되살린것:
            한마디 += f" 그중 {되살린것}개는 전에 받아써 둔 것이 있습니다."
        self.notice = 한마디
        self._rebuild_queue()
        self._목록담기()
        self._look_up_works()
        return self.state()

    def _look_up_works(self) -> None:
        """넣은 파일들의 품번으로 작품 정보를 가져온다.

        따로 도는 일꾼에게 맡긴다. 남의 서버가 느리다고 창이 멈추면 안 된다.
        """
        # 꺼 두었으면 인터넷에 아무것도 안 보낸다. 작품은 폴더 이름으로만 묶인다
        if not (self.settings.get("works") or {}).get("lookup_online", True):
            return
        # 파일을 두 번 빠르게 끌어다 놓으면 이 함수가 겹쳐서 돈다. 한쪽이
        # `self.works` 를 훑는 동안 다른 쪽이 칸을 늘리면 훑던 쪽이 터진다
        with self._lock:
            이미아는것 = set(self.works)
            할것 = [
                dlsite.extract_work_id(job.audio)
                for job in self.jobs
                if dlsite.extract_work_id(job.audio)
            ]
        모를것 = {w for w in 할것 if w not in 이미아는것}
        if not 모를것:
            return

        def 가져오기() -> None:
            # 하나씩은 이미 감쌌지만 **바깥이 비어 있었다.** `config_dir()` 이나
            # 잠금에서 터지면 일꾼이 그 자리에서 죽고, 나머지 품번은 영영 안
            # 가져온다 — 작품 이름이 안 뜨는데 까닭이 어디에도 안 남는다
            try:
                for work_id in sorted(모를것):
                    try:
                        작품 = self._fetch_work(
                            work_id, cache_dir=settings_store.config_dir() / "works"
                        )
                    except Exception:
                        continue  # 하나 실패해도 나머지는 가져온다
                    with self._lock:
                        self.works[work_id] = 작품
            except Exception as error:      # noqa: BLE001
                log.error("작품 정보 가져오기가 죽음", error)

        if not self._run_in_background:
            가져오기()
            return
        threading.Thread(target=가져오기, daemon=True).start()

    def _display_key(self, job: Job) -> str:
        """이 파일이 어느 상자에 들어갈지.

        사용자가 폴더에 품번을 정해 줬으면 그것이 이긴다.
        """
        정해준것 = self._overrides().get(str(job.audio.parent))
        if 정해준것:
            return 정해준것
        return dlsite.extract_work_id(job.audio) or str(job.audio.parent)

    def _overrides(self) -> dict[str, str]:
        """폴더 → 사용자가 정해 준 품번. 한 번 정하면 다음에도 기억한다."""
        값 = self.settings.get("work_ids") or {}
        return 값 if isinstance(값, dict) else {}

    def set_work_id(self, key: str, work_id: str) -> dict[str, Any]:
        """품번을 직접 정해 준다.

        파일 이름에 품번이 없거나, 있어도 조회가 안 되는 작품이 있다. 그럴 때
        사용자가 넣어 주면 그 상자의 파일 전부에 붙는다.

        조회가 안 돼도 넣은 품번은 이름표로 쓴다. 구별하는 것이 먼저다.
        """
        work_id = (work_id or "").strip().upper()
        if not work_id:
            return {"ok": False, "message": "품번을 입력해 주세요."}

        폴더들 = {str(j.audio.parent) for j in self.jobs if self._display_key(j) == key}
        if not 폴더들:
            return {"ok": False, "message": "그 작품을 찾지 못했습니다."}

        정해준것 = dict(self._overrides())
        for 폴더 in 폴더들:
            정해준것[폴더] = work_id
        self.settings = settings_store.save({"work_ids": 정해준것})

        for job in self.jobs:
            if str(job.audio.parent) in 폴더들:
                job.work_id = work_id

        작품 = None
        try:
            작품 = self._fetch_work(
                work_id, cache_dir=settings_store.config_dir() / "works"
            )
        except Exception:
            pass
        if 작품 is not None:
            self.works[work_id] = 작품
            for job in self.jobs:
                if job.work_id == work_id and 작품.found:
                    job.work = 작품
                    job.work_context = 작품.context()

        # 아직 손대지 않은 묶음만 다시 만든다. 번역을 시작한 것은 그대로 둔다
        with self._lock:
            건드릴것 = [g for g in self.groups if not g.touched]  # 손댄 것은 그대로
            for group in 건드릴것:
                for job in group.jobs:
                    job.grouped = False
                self.groups.remove(group)
        self._rebuild_queue()
        찾음 = bool(작품 is not None and 작품.found)
        self.notice = (
            f"{work_id} — {작품.title}" if 찾음
            else f"{work_id} 로 묶었습니다. DLsite 에서는 찾지 못해 표지와 태그는 없습니다."
        )
        return {"ok": True, "found": 찾음}

    def _works_view(self) -> list[dict[str, Any]]:
        """작품별로 묶어서 화면에 넘긴다. 넣은 순서를 지킨다."""
        순서: list[str] = []
        모음: dict[str, dict[str, Any]] = {}
        # 훑는 동안 일꾼이 칸을 늘리면 터진다. 한 번 떠 놓고 쓴다
        with self._lock:
            아는작품 = dict(self.works)

        # 트랙 하나가 묶음 몇 개인지. **작업 화면에도 점 막대를 그린다.**
        # 지금까지 묶음 진행은 번역 화면에만 있었다. 작업 화면에서는 「번역
        # 차례」 라고만 떠서, 열네 묶음 중 넷을 넣은 것인지 하나도 안 넣은
        # 것인지 알 수가 없었다
        묶음자리: dict[int, list[bool]] = {}
        # 번역 대기열에서 몇 번째인지. **왼쪽 나무에서 트랙을 누르면 곧바로
        # 그 묶음으로 간다** — `go_to()` 에 넘길 자리가 있어야 한다.
        # 다 끝난 트랙은 대기열에 없으므로 -1 이다
        대기자리: dict[int, int] = {}
        # 내 컴퓨터 AI 대기열에 걸어 둔 것. **걸어 둔 것이 보여야 한다** —
        # 안 보이면 같은 트랙을 또 걸거나, 걸어 놓고도 기다리는 줄 모른다
        걸린것: dict[int, dict[str, Any]] = {}
        with self._lock:
            묶음들목록 = list(self.groups)
        남은것 = [g for g in 묶음들목록 if not g.done]
        남은번호 = {id(g): 번호 for 번호, g in enumerate(남은것)}
        일자리 = {id(j): 번호 for 번호, j in enumerate(self.jobs)}
        for g in 묶음들목록:
            점들 = [bool(b["done"]) for b in (g.session.batches_view() if g.session else [])]
            for j in g.jobs:
                자리 = 일자리.get(id(j))
                if 자리 is not None:
                    묶음자리[자리] = 점들
                    대기자리[자리] = 남은번호.get(id(g), -1)
                    걸린것[자리] = {
                        "queued": self._대기자리(g),
                        "running": self._도는트랙 == g.key,
                    }

        # 작품마다 한 번만 읽는다. 트랙마다 읽으면 열다섯 번 파일을 연다
        제목담긴것: dict[str, dict[str, Any]] = {}

        def 한국어(열쇠: str) -> dict[str, str]:
            if 열쇠 not in 제목담긴것:
                제목담긴것[열쇠] = titles_store.가져오기(열쇠)["tracks"]
            return 제목담긴것[열쇠]

        for index, job in enumerate(self.jobs):
            열쇠 = self._display_key(job)
            if 열쇠 not in 모음:
                모음[열쇠] = {"key": 열쇠, "jobs": []}
                순서.append(열쇠)
            모음[열쇠]["jobs"].append({
                **job.to_view(), "index": index,
                # 점 하나가 묶음 하나. 번역 화면 나무와 **같은 부품**이다
                "dots": 묶음자리.get(index, []),
                # 번역 대기열 자리. 왼쪽에서 누르면 여기로 `go_to` 한다
                "at": 대기자리.get(index, -1),
                # 번역해 뒀으면 한국어 제목. **긴 일본어 파일 이름을 눈으로
                # 가릴 수는 없다** — 왼쪽 나무가 앱 뼈대가 된 뒤로는 여기가
                # 유일하게 트랙 이름이 뜨는 자리다
                "ko": 한국어(열쇠).get(job.audio.name, ""),
                **걸린것.get(index, {"queued": 0, "running": False}),
                # **받아쓰기 큐에 걸어 뒀나.** 이것이 없어서, 도는 중에
                # 다른 트랙의 「받아쓰기」 를 눌러도 줄이 그대로였다.
                # 큐에는 제대로 붙는데 화면이 아무 말을 안 해서
                # 「눌러도 아무 반응이 없다」 가 됐다
                "asr_queued": self._받아쓰기_기다리나(index, job),
                # **멈추라고 한 뒤 실제로 설 때까지가 몇 분이다.** 그동안
                # 단추가 「■ 멈추기」 그대로여서, 눌러도 아무 일이 없는 줄 알고
                # 사용자가 계속 다시 눌렀다. 지금 무엇을 하고 있는지 말한다
                "stopping": index in self._그만둔것,
            })

        묶음들 = []
        for 열쇠 in 순서:
            것 = 모음[열쇠]
            정보 = 아는작품.get(열쇠)
            것["info"] = 정보.to_view() if 정보 is not None else None
            것["name"] = (
                정보.title if 정보 is not None and 정보.found and 정보.title
                else Path(열쇠).name or 열쇠
            )
            # 제목을 번역해 뒀으면 한국어. 왼쪽 나무에서 이것이 앞에 선다
            것["ko"] = titles_store.가져오기(열쇠)["work"]
            것["lines"] = sum(j["lines"] for j in 것["jobs"])
            것["status"] = _작품상태(것["jobs"])
            # 품번이 없거나 조회가 안 된 상자는 사용자에게 물어봐야 한다
            것["needs_id"] = not (정보 is not None and 정보.found)
            것["folder"] = Path(것["jobs"][0]["path"]).parent.name
            것["guess"] = 열쇠 if dlsite.WORK_ID.fullmatch(열쇠) else ""
            묶음들.append(것)
        return 묶음들

    def remove(self, index: int) -> dict[str, Any]:
        """파일 하나를 목록에서 뺀다. 잘못 넣었을 때 쓴다.

        자물쇠를 쥔 채로 `state()` 를 부르면 안 된다. `state()` 도 같은 자물쇠를
        잡으려 해서 서로 기다린다. 그래서 자물쇠 안에서는 판단만 하고 나온다.
        """
        job = None
        도는중 = False
        with self._lock:
            if 0 <= index < len(self.jobs):
                job = self.jobs[index]
                도는중 = job.stage in (Stage.받아쓰기, Stage.다시훑기)
                if not 도는중:
                    self.jobs.pop(index)

        if job is None:
            return self.state()
        if 도는중:
            self.notice = "지금 받아쓰는 중인 파일이라 뺄 수 없습니다."
            return self.state()

        self._release_group_of(job)
        self._rebuild_queue()
        self.notice = f"{job.name} 을 뺐습니다."
        self._목록담기()
        return self.state()

    def remove_work(self, key: str) -> dict[str, Any]:
        """작품 하나를 통째로 뺀다. 폴더째 잘못 넣었을 때 쓴다."""
        대상 = [j for j in self.jobs if self._display_key(j) == key]
        도는중 = [j for j in 대상 if j.stage in (Stage.받아쓰기, Stage.다시훑기)]
        if 도는중:
            self.notice = "지금 받아쓰는 중인 파일이 있어 뺄 수 없습니다."
            return self.state()

        for job in 대상:
            self._release_group_of(job)
        with self._lock:
            self.jobs = [j for j in self.jobs if j not in 대상]
        self._rebuild_queue()
        self.notice = f"{len(대상)}개를 뺐습니다."
        self._목록담기()
        return self.state()

    def clear(self, confirm: bool = False) -> dict[str, Any]:
        """목록을 비운다. 되돌릴 수 없어서 한 번 더 묻는다.

        멈추기 바로 밑에 붙어 있어 오눌리기 쉽다. 받아쓴 것은 담아 두므로 다시
        넣으면 되살아나지만, 붙여넣던 번역은 날아간다.
        """
        if self.busy:
            self.notice = "돌아가는 중에는 목록을 비울 수 없습니다. 먼저 멈춰 주세요."
            return self.state()
        if not confirm:
            self.notice = "정말 비울까요? 한 번 더 누르면 비웁니다."
            return {**self.state(), "confirm": True}

        with self._lock:
            self.jobs = []
            self.groups = []
        self.notice = "목록을 비웠습니다."
        self._목록담기()
        return self.state()

    # ---- 상태 ----

    def 새판보기(self) -> None:
        """새 판이 나왔는지 뒤에서 한 번 본다. **켤 때 한 번만.**

        인터넷을 쓰므로 화면을 붙잡으면 안 된다. 실패하면 조용히 넘어간다 —
        고칠 수도 없는 경고를 띄우면 사용자는 그것을 무시하는 법부터 배운다.
        """
        def 일하기() -> None:
            try:
                self._새판수 = update.몇_판_뒤처졌나()
            except Exception:
                self._새판수 = 0

        threading.Thread(target=일하기, daemon=True).start()

    def state(self) -> dict[str, Any]:
        self._heal()
        with self._lock:
            # 위쪽 납작한 목록과 작품별 목록이 **같은 것을 말해야 한다.**
            # 한쪽에만 `stopping` 이 있으면 어느 화면에서 보느냐에 따라
            # 단추가 「■ 멈추기」 였다 「멈추는 중…」 이었다 한다
            jobs = [{**job.to_view(), "index": i, "stopping": i in self._그만둔것}
                    for i, job in enumerate(self.jobs)]
        return {
            "jobs": jobs,
            "works": self._works_view(),
            "busy": self.busy,
            # **누른 뒤에도 「멈추기」 그대로면 눌린 줄 모른다.** 세우라고 한
            # 뒤 실제로 서기까지 지금 파일 하나만큼이 남는다
            "stopping": self.busy and self._stop.is_set(),
            # **동작을 가르는 상태는 화면에 떠 있어야 한다** (규칙 5).
            #
            # `_only` 가 비어 있으면 도는 중에 끌어다 놓은 작품도 잡고, 차
            # 있으면 안 잡는다. 그런데 화면 어디에도 지금이 어느 쪽인지
            # 표시가 없었다. 사용자는 「받아쓰는 중에 작품을 넣으면 멈췄다가
            # 다시 눌러야 한다」 를 스스로 알아냈고, 그것을 이해하지 못했다
            "only": len(self._only) if self.busy else 0,
            "notice": self.notice,
            "queue": self._queue_view(),
            # 그래픽카드가 지금 어떤 상태인지. **화면 맨 위에 늘 뜬다.**
            #
            # 지금까지는 볼 방법이 없었다. 자리가 모자라 받아쓰기가 통째로
            # 죽었는데도 무엇이 얼마나 쓰고 있는지 알 수가 없어서 엉뚱한
            # 데를 팠다. `gpu.state()` 가 2초쯤 담아 두므로 자주 물어도 된다
            "gpu": gpu.state(),
            # **그래픽카드를 못 써서 CPU 로 내려갔나.** 고치는 길은 설정 깊은
            # 곳에 있는데, 오류 문구를 읽고 거기까지 찾아가라고 할 수는 없다.
            # 일이 난 자리에서 바로 내민다
            "gpu_고칠까": any(getattr(j, "gpu문제", False) for j in self.jobs),
            # **새 판이 나왔나.** 공개 저장소가 곧 업데이트 통로라, 받아 간
            # 사람은 우리가 고친 것을 알 길이 없다. 설정 깊은 곳에 단추를 두고
            # 눌러 보라고 하면 누르기 전에는 있는지조차 모른다
            "새판": self._새판수,
            # **처음 켰나.** 설정을 나열하면 아무도 안 읽는다. 한 화면에
            # 질문 하나씩 묻고, 물은 것은 다시 묻지 않는다
            "처음켬": not bool(self.settings.get("onboarded")),
            "settings": settings_store.for_display(self.settings),
            # 받아쓰기 강도 목록. 화면이 라디오로 그린다
            "presets": presets.to_view(),
            # **번역을 어디로 보내는가 — 길 셋과 손잡이 넷.**
            # 화면은 이것만 보고 그린다. 공급자 목록은 「자동」 길에서
            # 「보내는길」 손잡이를 고를 때만 쓴다
            "route": routes.to_view(self.settings),
            # 클립보드 감시. 켜졌는지 · 마지막 판정 · 되돌릴 것이 있는지
            "감시": self.감시상태(),
            "providers": [
                {
                    "id": info.id,
                    "name": info.name,
                    "needs_key": info.needs_key,
                    "key_url": info.key_url,
                    "note": info.note,
                    "has_key": bool(settings_store.api_key(self.settings, info.id)),
                    # 내 컴퓨터에서 도는 것은 키가 아니라 주소를 받는다
                    "local": info.local,
                    "default_url": info.default_url,
                    "default_model": info.default_model,
                }
                for info in providers.available()
            ],
        }

    def _heal(self) -> None:
        """일꾼이 죽었는데 "도는 중" 표시만 남는 것을 푼다.

        그대로 두면 시작 단추가 영영 안 돌아오고, 목록도 못 비운다.
        """
        # 일꾼을 아직 안 띄운 틈에는 `_worker` 가 비어 있다 (`start` 가 띄운 뒤에
        # 넣는다). 그때 죽은 것으로 보면 도는 중인데 안 도는 것처럼 보인다
        if self.busy and self._worker is not None and not self._worker.is_alive():
            self.busy = False
            if not self.notice:
                self.notice = "작업이 멈췄습니다. 다시 시작할 수 있습니다."

    def _queue_view(self) -> dict[str, Any]:
        """전체 대기열을 한 눈에. 진행은 작품이 몇 개든 통째로 센다."""
        done = sum(g.to_view()["batch_done"] for g in self.groups)
        total = sum(g.to_view()["batch_total"] for g in self.groups)
        지금 = self.current_group()
        return {
            "done": min(done, total),
            "total": total,
            "ready": 지금 is not None,
            "groups": [g.to_view() for g in self.groups],
            "current": 지금.to_view() if 지금 else None,
        }

    # ---- 받아쓰기 ----

    def start_tracks(self, indices: list[int]) -> dict[str, Any]:
        """고른 트랙만 받아쓴다.

        「받아쓰기 시작」 전역 단추는 무엇에 작용하는지 말하지 못했다.
        이제 화면이 트랙을 고르고, 여기는 그것만 받아쓴다. 이미 돌고 있으면
        고른 것을 판에 **덧붙인다** — 도는 일꾼이 이어서 집어 간다.
        """
        고른 = {int(i) for i in (indices or [])}
        # 화면의 트랙 번호는 목록에서의 자리다 — `_job()` 과 같은 셈법
        대상 = [자리 for 자리, j in enumerate(self.jobs)
                if 자리 in 고른 and j.stage == Stage.대기 and not j.transcribed]
        if not 대상:
            self.notice = "받아쓸 것이 없습니다. 이미 받아썼거나 도는 중입니다."
            return self.state()

        # **자물쇠를 쥔 채 `state()` 를 부르면 안 된다.**
        #
        # `self._lock` 은 `threading.Lock` 이라 **겹쳐 잡을 수 없다.**
        # `state()` 는 `_works_view()` 에서 같은 자물쇠를 다시 쥐므로, 여기서
        # 부르면 그 자리에서 영영 선다. 그리고 창구 호출은 한 줄로 처리되기
        # 때문에 **앱 전체가 굳는다** — 받아쓰기·번역·제목·낱말 저장, 화면을
        # 새로 그리는 것까지 전부. 받아쓰기는 뒤에서 멀쩡히 끝나는데 화면만
        # 죽어 있어서, 「앱이 멈췄다」 로만 보인다.
        #
        # 실제로 그렇게 겪었다. 받아쓰는 중에 다른 트랙의 「받아쓰기」 를
        # 누른 그 순간 굳었다. 그래서 자물쇠 안에서는 **판단만** 하고,
        # 밖에 나와서 답을 만든다.
        with self._lock:
            도는중 = self.busy
            if 도는중:
                self._only.update(대상)
            else:
                self._only = set(대상)

        if 도는중:
            self.notice = f"{len(대상)}개를 큐에 붙였습니다."
            return self.state()
        return self.start(_고른것만=True)

    def start(self, _고른것만: bool = False) -> dict[str, Any]:
        """받아쓰기를 시작한다. 이미 돌고 있으면 아무것도 하지 않는다."""
        if self.busy or not self.jobs:
            return self.state()
        if not _고른것만:
            # 전부 받아쓰는 판. 지난번 「고른 것만」 이 남아 있으면 안 된다
            self._only = set()

        self._worker = None      # 지난번 일꾼을 죽은 것으로 오해하지 않게
        self.busy = True
        self.notice = ""
        self._stop.clear()

        if not self._run_in_background:
            self._work()  # 시험에서는 그 자리에서 돈다
            return self.state()

        일꾼 = threading.Thread(target=self._work, daemon=True)
        # **띄운 뒤에** 넣는다. 넣고 나서 띄우면 그 사이에 화면이 물어볼 수 있는데,
        # 아직 안 띄운 스레드는 `is_alive()` 가 False 라 `_heal` 이 죽은 것으로 본다.
        # 그러면 받아쓰기가 막 시작됐는데 "작업이 멈췄습니다" 가 뜨고 시작 단추가
        # 되살아난다. 그 단추를 누르면 일꾼이 **둘** 이 되어 같은 파일을 함께
        # 받아쓰고 모델이 두 번 올라간다. 12GB 에서는 그것으로 터진다
        일꾼.start()
        self._worker = 일꾼
        return self.state()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self.notice = "멈추는 중입니다. 지금 파일이 끝나면 섭니다."

        # **세우기도 물릴 수 있어야 한다.** 지금 파일이 끝날 때까지는 아직
        # 아무것도 잃지 않았다. 다른 위험한 일과 같은 방법으로 물린다
        def 되돌리기() -> None:
            if not self.busy:
                raise RuntimeError("이미 다 섰습니다. 「받아쓰기 시작」 을 누르세요")
            self._stop.clear()
        self._되돌릴것찍기("멈추기", 되돌리기)
        return {"undo": "멈추기", **self.state()}

    def _받아쓰기_기다리나(self, 자리: int, job: Job) -> bool:
        """이 트랙이 받아쓰기 차례를 기다리고 있나.

        도는 중(`busy`)이고, 아직 안 받아썼고, 「고른 것만」 판이면 그 목록에
        들어 있어야 기다리는 것이다.
        """
        if not self.busy or job.stage != Stage.대기 or job.transcribed:
            return False
        if 자리 in self._그만둔것:
            return False
        return (자리 in self._only) if self._only else True

    def stop_track(self, index: int) -> dict[str, Any]:
        """**이 트랙만** 그만둔다. 나머지는 계속 돈다.

        「받아쓰기」 를 누른 자리에서 한 번 더 누르면 물릴 수 있어야 한다.
        멈추는 단추가 화면 맨 위에만 있으면, 누른 자리에서 멀고 무엇을
        멈추는 것인지도 말하지 못한다.
        """
        자리 = int(index)
        job = self._job(자리)
        if job is None:
            return {"ok": False, "message": "그 트랙이 없습니다.", **self.state()}

        with self._lock:
            # `_only` 가 비어 있으면 「전부 받아쓴다」 는 뜻이다. 되돌릴 때
            # 무턱대고 다시 넣으면 「이것 하나만」 으로 뜻이 뒤집힌다
            골라둔판 = bool(self._only)
            self._only.discard(자리)
            도는중 = job.stage in (Stage.받아쓰기, Stage.다시훑기)
            if 도는중:
                # 받아쓰는 중이면 그 자리에서 끊는다. 지금까지 받아쓴 것은
                # 버린다 — 반쯤 받아쓴 자막은 맞는지 알 수 없어 더 나쁘다
                self._그만둔것.add(자리)

        self.notice = ("멈추는 중입니다. 곧 섭니다." if 도는중
                       else f"「{job.audio.stem}」 을 기다리는 줄에서 뺐습니다.")
        log.write("받아쓰기", "트랙 하나를 그만둠", 파일=job.audio.name, 도는중=도는중)

        # **멈추기도 물릴 수 있어야 한다.** 실제로 서기까지 몇 분이 걸리니,
        # 그 사이라면 아직 아무것도 잃지 않았다. 다른 위험한 일과 같은 방법
        # — 되돌리기 토스트 — 으로 물린다
        def 되돌리기(자리=자리, 골라둔판=골라둔판) -> None:
            with self._lock:
                self._그만둔것.discard(자리)
                if 골라둔판:
                    self._only.add(자리)
        self._되돌릴것찍기("멈추기", 되돌리기)
        return {"ok": True, "undo": "멈추기", **self.state()}

    def _next_pending(self, 해본것: set[int] | None = None) -> Job | None:
        """아직 받아쓰지 않은 파일 하나. 돌아가는 중에 넣은 것도 잡는다.

        `해본것` 은 이번 판에 이미 손댄 파일이다. 이것이 없으면 **영원히 돈다.**
        받아쓰기가 멈춤으로 끝나면 그 파일은 다시 '대기' 로 돌아가는데, 그러면
        곧바로 같은 파일을 또 집어 든다. 한 판에 한 번씩만 손댄다.
        """
        이미 = 해본것 or set()
        with self._lock:
            for 자리, job in enumerate(self.jobs):
                if job.stage == Stage.대기 and not job.transcribed and id(job) not in 이미:
                    # 고른 것만 받아쓰는 판이면 목록 밖은 건너뛴다.
                    # 「무엇을」은 화면이 정하고, 여기는 큐를 돌릴 뿐이다
                    if self._only and 자리 not in self._only:
                        continue
                    if 자리 in self._그만둔것:
                        continue      # 기다리는 줄에서 빼 달라고 한 것
                    return job
        return None

    def _work(self) -> None:
        try:
            pipeline = self._make_pipeline(self.settings)
            self._pipeline = pipeline

            # 목록을 미리 떠 놓지 않는다. 그러면 돌아가는 중에 끌어다 놓은 파일이
            # 통째로 무시된다. 계속 밀어 넣을 수 있어야 한다
            해본것: set[int] = set()
            while not self._stop.is_set():
                job = self._next_pending(해본것)
                if job is None:
                    break
                해본것.add(id(job))
                자리 = next((i for i, j in enumerate(self.jobs) if j is job), -1)

                def 멈출까(자리=자리) -> bool:
                    # 판 전체를 세우라고 했거나, **이 트랙만** 그만두라고 했거나
                    return self._stop.is_set() or 자리 in self._그만둔것

                try:
                    pipeline.transcribe(job, should_stop=멈출까)
                except asr.NoSpeech as 조용함:
                    # 효과음만 있는 트랙이다. 고장이 아니므로 빨강으로 두지 않는다
                    job.stage = Stage.건너뜀
                    job.error = str(조용함)
                    log.write("받아쓰기", "건너뜀 — 말이 없음", 파일=job.audio.name)
                except Exception as error:
                    # 한 파일이 터져도 대기열은 계속 돈다
                    job.stage = Stage.실패
                    job.error = str(error)
                    log.error("받아쓰기 실패", error, 파일=job.audio.name)
                # **그만두라는 표시를 치운다.** 안 치우면 나중에 그 트랙의
                # 「받아쓰기」 를 다시 눌러도 시작하자마자 또 멈춘다
                self._그만둔것.discard(자리)
                # 끝나는 대로 번역할 수 있게 한다. 나머지가 받아쓰는 동안
                # 사용자는 이미 끝난 것을 붙여넣고 있으면 된다
                self._rebuild_queue()

            self._rebuild_queue()
            self._run_auto(pipeline)
        except Exception as error:      # noqa: BLE001
            # 파일 하나가 터지는 것은 위에서 잡는다. 여기까지 온 것은 **판을
            # 세우는 데서** 터진 것이다 — 모델을 못 올렸거나, 대기열을 다시
            # 짜다가 어긋났거나.
            #
            # 잡지 않으면 일꾼만 조용히 죽는다. `busy` 는 `finally` 가 내려
            # 주니 화면은 멀쩡해 보이는데 **아무것도 안 돌고, 왜 안 도는지
            # 아무 데도 안 적힌다.** 남은 트랙은 「기다리는 중」 그대로다.
            self.notice = f"받아쓰기를 이어 가지 못했습니다: {error}"
            log.error("받아쓰기 일꾼이 죽음", error)
        finally:
            self.busy = False
            self._그만둔것.clear()
            # 딴 창을 보고 있어도 끝난 것을 알게. 멈추라고 해서 끝난 것은 안 알린다
            if not self._stop.is_set():
                self._끝났다고_알리기()

    def _run_auto(self, pipeline: Pipeline) -> None:
        """키가 있으면 자동으로 돌려 본다. 막힌 것은 복붙 대기열에 남는다."""
        if routes.정해진값(self.settings)["보내는길"] == "manual":
            return
        for group in self.groups:
            if self._stop.is_set():
                break
            try:
                pipeline.run_auto_translation(group, should_stop=self._stop.is_set)
            except Exception as error:
                self.notice = f"자동 번역이 실패해 복붙으로 넘깁니다: {error}"
                log.error("자동 번역 실패", error, 작품=group.key)
            self._absorb(group, pipeline)
            # 묶음 단위로 넘어간 것은 예외로 안 올라온다(`run_auto` 가 안에서
            # 삼킨다). 여기서 안 말하면 사용자는 「자동을 켰는데 왜 복붙이
            # 남지?」 하고 사유도 모른 채 마주한다
            넘긴것 = group.session.handoffs if group.session else []
            if 넘긴것:
                사유들 = {h.reason for h in 넘긴것}
                self.notice = (
                    f"{len(넘긴것)}묶음이 자동으로 안 되어 복붙으로 넘어왔습니다"
                    f" — {' / '.join(sorted(사유들))}"
                )
                for h in 넘긴것:
                    log.write("번역", "복붙으로 넘김",
                              작품=group.key, 묶음=h.batch.number, 사유=h.reason)

    def _묶음한도(self) -> dict[str, Any]:
        """묶음을 어떻게 짤지 — 한 묶음의 크기와, 낱말을 가릴지.

        **길이 정한다** (`route.정해진값`). 예전에는 「고른 공급자가 내
        컴퓨터에서 도는가」를 여기서 물어봤는데, 그러면 화면에서 고른 것과
        실제로 나가는 것이 어긋날 자리가 하나 더 생긴다.

        복붙은 사람이 채팅창에 옮기므로 작게 준다. 내 컴퓨터에서 도는 모델은
        사람 손이 안 가므로 크게 준다 — 묶음 수가 절반이 되어 왕복이 줄고,
        모델이 앞뒤 문맥을 두 배로 본다. 창은 `providers.필요한창()` 이 거기에
        맞춰 넓혀 준다.

        자동이 막혀 복붙으로 넘어온 묶음은 큰 채로 온다. 그때는 화면이 파일로
        건네주므로(`Batch.prefers_file`) 채팅창에 손으로 옮길 일은 없다.
        """
        값 = routes.정해진값(self.settings)
        줄 = int(값["묶음"])
        # 글자 한도는 줄 수에 맞춰 따라간다. 줄만 늘려 놓고 글자를 그대로
        # 두면 글자에서 먼저 잘려 늘린 뜻이 없다
        글자 = exchange.LOCAL_BATCH_CHARS if 줄 > exchange.BATCH_LINES else exchange.BATCH_CHARS
        return {"batch_lines": 줄, "batch_chars": 글자, "가리기": bool(값["가리기"])}

    def _rebuild_queue(self) -> None:
        """받아쓰기가 끝난 트랙을 대기열에 더한다.

        트랙 하나가 묶음 하나다. 끝나는 대로 바로 번역할 수 있고, 쌓아 두지
        않는다. 이미 묶음에 들어간 트랙은 다시 묶지 않는다. 다시 묶으면 번호가
        바뀌어 이미 붙여넣은 번역이 어긋난다.
        """
        with self._lock:
            # **다시 받아쓸 것으로 돌아간 트랙을 먼저 뺀다.**
            #
            # 여기는 더할 줄만 알고 뺄 줄을 몰랐다. 강도를 올리면 `_강도바뀜` 이
            # 트랙을 「대기」로 돌리고 `grouped` 를 내리는데, 묶음은 그대로
            # 남아 있었다. 화면은 "「극한」으로 다시 받아씁니다" 라고 해놓고
            # 번역 화면에는 「빠르게」로 받아쓴 것이 그대로 떠 있었다.
            # 그것을 번역해 자막을 만들면 강도를 고른 뜻이 없어지고, 나중에
            # 다시 받아쓰면 줄 번호가 바뀌어 붙여넣은 것이 통째로 어긋난다.
            #
            # **더하기보다 먼저 한다.** 한 묶음에 트랙이 여럿이면 멀쩡한 것까지
            # 딸려 나가는데, 먼저 빼야 그것들이 이 번에 다시 묶인다.
            버릴것 = [g for g in self.groups if any(not j.grouped for j in g.jobs)]
            if 버릴것:
                self.groups = [g for g in self.groups if g not in 버릴것]
                for g in 버릴것:
                    for job in g.jobs:
                        job.grouped = False

            새로운것 = [j for j in self.jobs if j.transcribed and not j.grouped]
            if 새로운것:
                # **번호표는 앱이 아는 모든 트랙을 보고 준다.**
                #
                # `build_groups` 에는 새로 받아쓴 것만 넘어간다. 거기서 번호표를
                # 매기면 매번 1번부터 다시 줘서 **트랙마다 떼어 준 칸이 겹친다.**
                # 그러면 번호만 보고 주인을 찾는다는 전제가 통째로 무너진다
                번호표주기(self.jobs)
                self.groups += build_groups(
                    새로운것, work_key_of=self._display_key, **self._묶음한도()
                )
                for job in 새로운것:
                    job.grouped = True
                    # **여기서 그 트랙의 사전을 못 박는다.**
                    #
                    # 담아 두지 않으면 앱을 껐다 켤 때 지금 목록으로 표를 다시
                    # 매긴다. 그 사이에 낱말 하나를 끄면 번호가 밀려서, 아까
                    # 복사해 둔 프롬프트로 받은 답의 `KW01` 이 딴 낱말로
                    # 조용히 되돌아온다. 아무 오류도 안 난다.
                    #
                    # 붙여넣을 때까지 기다리면 늦다 — 복사만 해 놓고 닫는 길이
                    # 있다. 묶음을 짜서 사전이 정해진 바로 이 자리에서 담는다
                    # **번호표도 같은 이유로 여기서 담는다.** 번호표가 바뀌면
                    # 브라우저에 남아 있던 답이 통째로 어긋난다. 다음에 켤 때
                    # 같은 번호표를 다시 쓰려면 받아쓴 것과 함께 적혀 있어야 한다
                    if job.가림사전 and not job.사전담김:
                        save_transcript(job)
                        job.사전담김 = True
                        job._번호표담김 = True
                    elif job.track_id and not getattr(job, "_번호표담김", False):
                        save_transcript(job)
                        job._번호표담김 = True

            # 목록에서 뺀 파일만 남은 묶음은 버린다
            남은것 = {id(j) for j in self.jobs}
            self.groups = [
                g for g in self.groups if any(id(j) in 남은것 for j in g.jobs)
            ]

            # 넣은 차례대로 세운다.
            #
            # **작품끼리 섞이지 않게 한다.** 예전에는 넣은 차례만 봤다.
            # 작품 1 을 돌리는 중에 작품 2 를 넣고, 작품 1 의 트랙을 다시
            # 받아쓰면 그 트랙이 뒤로 밀려서 작품이 뒤죽박죽 섞였다.
            # 작품을 먼저 묶고 그 안에서 넣은 차례를 지킨다.
            자리 = {id(j): 번호 for 번호, j in enumerate(self.jobs)}

            def 어디쯤(g):
                return min((자리.get(id(j), 10**9) for j in g.jobs), default=10**9)

            작품첫자리: dict[str, int] = {}
            for g in self.groups:
                이제 = 어디쯤(g)
                if g.work_key not in 작품첫자리 or 이제 < 작품첫자리[g.work_key]:
                    작품첫자리[g.work_key] = 이제
            self.groups.sort(key=lambda g: (작품첫자리.get(g.work_key, 10**9), 어디쯤(g)))
            트랙번호_매기기(self.groups)

    # ---- 번역 대기열 ----

    def pending_groups(self) -> list[Group]:
        """아직 번역이 남은 묶음들. 건너뛴 것도 그대로 남는다."""
        return [g for g in self.groups if not g.done]

    def current_group(self) -> Group | None:
        남은것 = self.pending_groups()
        if not 남은것:
            return None
        self._at = max(0, min(self._at, len(남은것) - 1))
        return 남은것[self._at]

    def go_to(self, 자리: int) -> dict[str, Any]:
        """다른 묶음으로 옮겨 간다.

        건너뛴 것으로 되돌아갈 길이 있어야 한다. 거절당해서 건너뛴 것을 다른
        AI 로 다시 하려면 돌아가야 한다.
        """
        남은것 = self.pending_groups()
        if not 남은것:
            return {"ready": False}
        self._at = max(0, min(int(자리), len(남은것) - 1))
        return self.prompt()

    def go_next(self) -> dict[str, Any]:
        return self.go_to(self._at + 1)

    def go_prev(self) -> dict[str, Any]:
        return self.go_to(self._at - 1)

    def _다끝난것(self) -> dict[str, Any]:
        """번역할 것이 없을 때도 **나무는 준다.**

        예전에는 여기서 나무 없이 `ready: False` 만 돌려줬다. 그러면 화면이
        번역 화면에서 사용자를 쫓아내서, 나무에 매달아 둔 것이 통째로 막혔다 —
        듣고 고치기도, 검수도, 나무 맨 위의 제목 번역도.

        **나무는 할 일 목록이 아니라 관리하는 곳이다.** 다 끝난 뒤가 오히려
        들어가고 싶은 때다. 되돌리고, 다시 듣고, 제목을 고치러 온다.
        """
        return {
            "ready": False,
            "done": bool(self.groups),
            "tree": self._나무([], self.groups),
            "list": [],
            "batches": [],
            "track_at": 0,
            "track_total": 0,
            "has_prev": False,
            "has_next": False,
        }

    def prompt(self, key: str = "") -> dict[str, Any]:
        """AI 에 넣어야 할 것. `key` 를 주면 **그 트랙 것**을 준다.

        **병렬로 뿌리려면 트랙을 골라 복사할 수 있어야 한다.** 창을 여럿 열어
        트랙을 하나씩 맡기려면 트랙마다 「복사」 를 눌러 각각 다른 세션에
        넣어야 하는데, 지금 열린 것 하나만 내주면 트랙을 바꿔 가며 열고
        복사하고를 되풀이하게 된다.
        """
        group = self._그룹찾기(key) if key else self.current_group()
        if group is None or group.session is None:
            return self._다끝난것()

        batch = group.session.pending_batch()
        if batch is None:
            return self._다끝난것()

        남은것 = self.pending_groups()
        묶음들 = group.session.batches_view()
        # **그대로 돌아오면 메아리다.** 복사해 놓고 붙여넣기를 잘못하면
        # 우리가 낸 글이 그대로 클립보드에 남아 감시에 걸린다.
        # 트랙마다 마지막 것 하나만 들고 있으면 된다
        self._내보낸글[group.key] = batch.prompt
        return {
            "ready": True,
            "text": batch.prompt,
            # 지시문 없이 번호와 원문만. 기계 번역기(구글·파파고)에 넣을 때
            # 쓴다. 화면이 토글로 골라서 복사한다
            "plain": batch.plain,
            "lines": len(batch.segments),
            "is_retry": batch.is_retry,
            "prefers_file": batch.prefers_file,
            # 이 묶음이 자동에서 넘어온 것이면 왜인지. 복붙 카드가 띄운다 —
            # 거절이면 다른 AI 에 넣어 볼 일이고, 한도면 기다리면 되고,
            # 서버 오류면 내용과 무관하다. 사유를 모르면 다 같은 벽이다
            "reason": next(
                (h.reason for h in group.session.handoffs
                 if h.batch.number == batch.number), ""),
            "title": group.title,
            "files": group.file_count,
            "span": batch.span,
            "at": self._at,
            # 큰 숫자는 **묶음** 진행이다. 사용자가 몇 번 붙여넣어야 하는지가
            # 그것이다. 트랙 자리는 트랙이 여럿일 때만 뜻이 있다.
            #
            # 예전에는 여기에 트랙 자리를 넣었다. 3시간짜리 하나를 넣으면
            # 묶음이 열여덟 개인데 화면에는 "1 / 1" 만 떠 있었고, 붙여넣어도
            # 숫자가 안 바뀌어서 먹히는지도 알 수 없었다
            "number": batch.number,
            "total": group.session.total_batches,
            "batches": 묶음들,
            "batch_done": sum(1 for b in 묶음들 if b["done"]),
            "track_at": self._at + 1,
            "track_total": len(남은것),
            "has_prev": self._at > 0,
            "has_next": self._at < len(남은것) - 1,
            "list": [
                {
                    "at": 번호,
                    "title": g.title,
                    "lines": g.line_count,
                    "now": 번호 == self._at,
                }
                for 번호, g in enumerate(남은것)
            ],
            # 작품 → 트랙 → 묶음 세 층. 화면 왼쪽 나무가 이것으로 그려진다
            # **끝난 트랙도 남긴다.** 번역을 넣으면 목록에서 사라져서, 방금
            # 넣은 것이 맞는지 보려면 다른 화면을 찾아 들어가야 했다.
            # 나무를 만든 까닭이 늘 보이게 하려는 것이었다
            "tree": self._나무(남은것, self.groups),
            "preview": [
                {"n": s["index"], "ja": s["ja"]} for s in batch.segments[:400]
            ],
        }

    def _나무(self, 남은것: list[Group],
              모두: list[Group] | None = None) -> list[dict[str, Any]]:
        """작품 → 트랙 → 묶음 세 층.

        **이 층이 없어서 오래 헷갈렸다.** 번역 화면에는 작품이라는 개념이
        아예 없었고, 작품 셋을 넣으면 트랙 열다섯 개가 일렬로 늘어섰다.
        어느 작품 것인지 긴 제목 글자로만 가려야 했고, 작품별로 넘어갈
        방법도 없었다. 표지도 안 넘겨서 눈으로 구별할 수도 없었다.

        묶음은 개수만 준다. 하나하나의 글자는 지금 보는 트랙 것만 있으면 된다.
        """
        with self._lock:
            아는작품 = dict(self.works)

        # 끝난 것까지 그린다. 남은 것은 몇 번째인지도 함께 들고 다닌다 —
        # 「다음 할 것」 은 남은 것 안에서 세기 때문이다
        전부 = 모두 if 모두 is not None else 남은것
        남은자리 = {id(g): 번호 for 번호, g in enumerate(남은것)}
        with self._lock:
            일자리 = {id(j): 번호 for 번호, j in enumerate(self.jobs)}

        순서: list[str] = []
        모음: dict[str, dict[str, Any]] = {}
        for g in 전부:
            열쇠 = g.work_key
            if 열쇠 not in 모음:
                정보 = 아는작품.get(열쇠)
                보임 = 정보.to_view() if 정보 is not None else None
                한국어 = titles_store.가져오기(열쇠)
                모음[열쇠] = {
                    "key": 열쇠,
                    "name": (보임 or {}).get("title") or Path(열쇠).name or 열쇠,
                    # 번역해 뒀으면 한국어를 함께 준다. **긴 일본어를 눈으로
                    # 가릴 수는 없다** — 일곱 줄이 늘어서 있어도 어느 것이
                    # 무슨 내용인지 모른 채 고르게 된다
                    "ko": 한국어["work"],
                    "track_ko": dict(한국어["tracks"]),
                    # 표지. 이것이 있어야 작품이 눈으로 구별된다
                    "cover": (보임 or {}).get("image") or "",
                    "tracks": [],
                }
                순서.append(열쇠)
            묶음들 = g.session.batches_view() if g.session else []
            번호 = 남은자리.get(id(g), -1)
            모음[열쇠]["tracks"].append({
                "at": 번호,
                # 끝난 트랙은 번역할 것이 없다. 눌렀을 때 「듣고 고치기」로
                # 가야 하므로 어느 작업인지도 함께 준다
                "done": bool(g.done),
                "job_at": next((일자리[id(j)] for j in g.jobs if id(j) in 일자리), -1),
                "no": g.track_no,
                "name": g.track_name,
                "ko": 모음[열쇠]["track_ko"].get(
                    next((j.audio.name for j in g.jobs), ""), ""),
                "lines": g.line_count,
                "now": 번호 == self._at,
                # 대기열에 몇 번째로 걸려 있나. 0 이면 안 걸림
                "queued": self._대기자리(g),
                "running": self._도는트랙 == g.key,
                "batch_total": len(묶음들),
                "batch_done": sum(1 for b in 묶음들 if b["done"]),
                # 점 하나가 묶음 하나다. 어느 것이 끝났는지 그대로 보여 준다
                "dots": [bool(b["done"]) for b in 묶음들],
            })

        나온것 = []
        for 열쇠 in 순서:
            것 = 모음[열쇠]
            것.pop("track_ko", None)   # 트랙마다 이미 제 것을 들고 있다
            트랙 = 것["tracks"]
            것["track_total"] = len(트랙)
            것["track_done"] = sum(1 for t in 트랙 if t["batch_done"] >= t["batch_total"] > 0)
            것["batch_total"] = sum(t["batch_total"] for t in 트랙)
            것["batch_done"] = sum(t["batch_done"] for t in 트랙)
            것["now"] = any(t["now"] for t in 트랙)
            나온것.append(것)
        return 나온것

    def look_at_batch(self, number: int) -> dict[str, Any]:
        """사용자가 목록에서 묶음 하나를 골랐다. 그것을 화면에 띄운다.

        0 을 주면 "고른 것 없음" 으로 돌아가 안 끝난 것 중 가장 앞을 띄운다.
        """
        group = self.current_group()
        if group is None or group.session is None:
            return {"ok": False, "message": "번역할 것이 없습니다."}
        if not group.session.look_at(int(number or 0)):
            return {"ok": False, "message": "그런 묶음이 없습니다."}
        return {"ok": True}

    def all_batches_text(self) -> dict[str, Any]:
        """묶음 **전부**의 프롬프트. 창을 여러 개 열어 한꺼번에 돌릴 때 쓴다.

        3시간짜리는 묶음이 열여덟 개다. 하나씩 복사해 오가는 것보다 한 번에
        받아 두고 창마다 나눠 넣는 편이 훨씬 빠르다.
        """
        group = self.current_group()
        if group is None or group.session is None:
            return {"ok": False, "message": "번역할 것이 없습니다."}
        묶음들 = []
        for 것 in group.session.batches_view():
            if 것["done"]:
                continue
            batch = group.session.batch_by_number(것["number"])
            if batch is None:
                continue
            묶음들.append({"number": 것["number"], "lines": 것["lines"],
                          "span": 것["span"], "text": batch.prompt,
                          "plain": batch.plain})
        if not 묶음들:
            return {"ok": False, "message": "남은 묶음이 없습니다."}
        return {"ok": True, "title": group.title, "batches": 묶음들}

    # ---- 제목 번역 ----
    #
    # 넣어 둔 작품 **전부**의 제목이 한꺼번에 뜬다. 작품 하나 것만이 아니다.
    # 할 일이 아니라 **관리하는 곳**이라서, 다 끝난 뒤에도 들어가서 마음에 안
    # 드는 작품만 다시 번역하고 특정 작품만 되돌린다.

    def _작품별파일(self) -> list[tuple[str, str, list[Job]]]:
        """`(열쇠, 일본어 작품 제목, 그 작품의 일들)`. 넣은 순서 그대로."""
        순서: list[str] = []
        모음: dict[str, list[Job]] = {}
        with self._lock:
            일들 = list(self.jobs)
            아는작품 = dict(self.works)
        for job in 일들:
            열쇠 = self._display_key(job)
            if 열쇠 not in 모음:
                순서.append(열쇠)
                모음[열쇠] = []
            모음[열쇠].append(job)
        나온것 = []
        for 열쇠 in 순서:
            정보 = 아는작품.get(열쇠)
            이름 = (정보.title if 정보 is not None and 정보.found else "") or Path(열쇠).name
            나온것.append((열쇠, 이름, 모음[열쇠]))
        return 나온것

    def _제목묶음들(self) -> list[titles_store.제목묶음]:
        """작품마다 제목 번호를 매긴다.

        **한 번 내준 번호는 그 작품 것으로 붙들고 있는다.** 번호를 자리
        순서로만 매기면, 복사해 가서 AI 에 붙여넣는 사이에 작품을 하나 더
        넣거나 목록을 비우면 번호가 밀린다. 그 답을 나중에 넣으면 **엉뚱한
        작품에 제목이 들어간다.** 되돌릴 수 없는 종류의 사고다.
        """
        것들 = [
            (열쇠, 이름, [j.audio.name for j in 일들])
            for 열쇠, 이름, 일들 in self._작품별파일()
        ]
        묶음들 = titles_store.번호매기기(것들)
        새로준것 = False

        if not self._제목번호:
            self._제목번호 = titles_store.번호표()
        쓰는번호 = set(self._제목번호.values())
        다음번호 = titles_store.첫번호
        for 묶음 in 묶음들:
            잡아둔것 = self._제목번호.get(묶음.열쇠)
            if 잡아둔것 is not None:
                묶음.첫번호 = 잡아둔것
                continue
            # 새 작품에는 아직 아무도 안 쓰는 번호대를 준다
            while 다음번호 in 쓰는번호:
                다음번호 += titles_store.작품폭
            묶음.첫번호 = 다음번호
            쓰는번호.add(다음번호)
            self._제목번호[묶음.열쇠] = 다음번호
            새로준것 = True
        if 새로준것:
            titles_store.번호표담기(self._제목번호)
        return 묶음들

    def titles(self) -> dict[str, Any]:
        """넣어 둔 작품 전부의 제목. 왼쪽에 원문, 오른쪽에 번역."""
        묶음들 = self._제목묶음들()
        작품들 = []
        for 묶음, (열쇠, 이름, 일들) in zip(묶음들, self._작품별파일()):
            담긴것 = titles_store.가져오기(열쇠)
            작품들.append({
                "key": 열쇠,
                "n": 묶음.작품번호,
                # **이 작품 것만** 뽑은 글. 작품 열 개를 넣어 두고 한 작품만
                # 다시 하고 싶을 때, 전부 아니면 한 줄씩밖에 길이 없었다
                "text": titles_store.내보낼글([묶음]),
                "ja": 이름,
                "ko": 담긴것["work"],
                # **상태가 둘이다.** 번역은 했는데 이름은 아직 안 바꾼 것이
                # 정상이고 오히려 기본이다. 둘 다 보여야 한다
                "translated": bool(담긴것["work"] or 담긴것["tracks"]),
                "renamed": bool(담긴것["renamed"]),
                "too_many": 묶음.너무많나,
                "tracks": [
                    {
                        "n": 묶음.트랙번호(몇번째),
                        "file": job.audio.name,
                        "ja": job.audio.name,
                        "ko": 담긴것["tracks"].get(job.audio.name, ""),
                    }
                    for 몇번째, job in enumerate(일들)
                ],
            })
        return {"ok": True, "works": 작품들, "text": titles_store.내보낼글(묶음들)}

    def submit_titles(self, pasted: str) -> dict[str, Any]:
        """붙여넣은 제목 답을 담는다. **파일 이름은 안 바꾼다.**

        답이 여러 작품 것으로 섞여 와도 번호대를 보고 갈라 담는다. 사용자가
        「몇 번 작품 것입니다」 를 고를 일이 없다.
        """
        묶음들 = self._제목묶음들()
        받은것 = dict(exchange.번호줄(pasted or ""))
        if not 받은것:
            return {"ok": False, "message": "번호가 붙은 줄을 찾지 못했습니다."}

        나눈것 = titles_store.나눠담기(받은것, 묶음들)
        if not 나눈것:
            return {"ok": False, "message":
                    "제목 번호(9000번대)를 찾지 못했습니다. 자막 답을 넣으신 것은 아닌가요?"}

        # 원문이 그대로 `.lrc` 에 박혔던 사고를 다시 겪지 않는다. 제목도
        # 똑같이 일본어가 그대로 돌아올 수 있고, 그러면 파일 이름이
        # 일본어인 채로 「번역됨」 이 된다
        온글들 = [
            글 for 값 in 나눈것.values()
            for 글 in [값.get("work", "")] + list(값.get("tracks", {}).values())
        ]
        if titles_store.일본어그대로인가(온글들):
            return {
                "ok": False, "not_korean": True,
                "message": "번역이 아니라 일본어가 그대로 온 것 같습니다. 담지 않았습니다.",
            }

        담긴수 = 0
        for 열쇠, 값 in 나눈것.items():
            titles_store.제목넣기(열쇠, 값.get("work", ""), 값.get("tracks", {}))
            담긴수 += (1 if 값.get("work") else 0) + len(값.get("tracks", {}))
        return {"ok": True, "saved": 담긴수, "message": f"{담긴수}줄을 담았습니다."}

    def save_title(self, work_key: str, number: int, text: str) -> dict[str, Any]:
        """한 줄만 손으로 고친다.

        **손으로 적은 것은 검사하지 않는다.** 사용자가 직접 친 것이다.
        """
        묶음 = next((m for m in self._제목묶음들() if m.열쇠 == str(work_key)), None)
        if 묶음 is None:
            return {"ok": False, "message": "그 작품을 찾지 못했습니다."}
        무엇 = 묶음.어느것(int(number))
        if 무엇 is None:
            return {"ok": False, "message": "그 줄을 찾지 못했습니다."}
        종류, 파일이름 = 무엇
        if 종류 == "work":
            titles_store.제목넣기(묶음.열쇠, text or "", {})
        else:
            titles_store.제목넣기(묶음.열쇠, "", {파일이름: text or ""})
        return {"ok": True}

    def rename_files(self, work_key: str) -> dict[str, Any]:
        """음원 파일 이름을 실제로 바꾼다. **누를 때만 움직인다.**"""
        찾은것 = next(
            (것 for 것 in self._작품별파일() if 것[0] == str(work_key)), None)
        if 찾은것 is None:
            return {"ok": False, "message": "그 작품을 찾지 못했습니다."}
        _, _, 일들 = 찾은것
        담긴것 = titles_store.가져오기(work_key)

        난것 = titles_store.이름바꾸기(
            work_key, [j.audio for j in 일들], 담긴것["tracks"])
        # **부분만 바뀌어도 따라가야 한다.** 안 따라가면 앱이 이미 없는
        # 파일을 가리킨 채로 남는다 — 목록에는 옛 이름이 떠 있고 눌러도
        # 아무 일이 안 난다. 실패했을 때가 오히려 더 헷갈리는 상태다
        if 난것.get("renamed"):
            # 바꾼 뒤의 `original` 은 {새이름: 옛이름} 이다. 앱은 옛 이름을
            # 들고 있으므로 뒤집어서 넘긴다
            바뀐표 = titles_store.가져오기(work_key).get("original") or {}
            self._이름따라가기(일들, {옛: 새 for 새, 옛 in 바뀐표.items()})
        if not 난것["ok"]:
            if 난것.get("nothing"):
                return {"ok": False, "message": "바꿀 것이 없습니다. 먼저 제목을 번역해 주세요."}
            부분 = bool(난것.get("partial"))
            앞 = f"{난것['renamed']}개만 바뀌었습니다. " if 부분 else ""
            return {
                "ok": False,
                # **몇 개가 바뀌었는지 숨기지 않는다.** 「바꾸지 못했습니다」
                # 만 뜨면 아무것도 안 바뀐 줄 알고, 폴더를 열어 보고서야
                # 절반이 바뀐 것을 안다
                "partial": 부분,
                "renamed": 난것.get("renamed", 0),
                "blocked": 난것["blocked"],
                "message": f"{앞}바꾸지 못했습니다: " + " / ".join(난것["blocked"][:3]),
            }

        말 = f"{난것['renamed']}개 파일 이름을 바꿨습니다."
        if 난것.get("fixed"):
            말 += " (윈도우가 못 쓰는 글자를 고쳤습니다)"
        return {"ok": True, "renamed": 난것["renamed"],
                "fixed": 난것.get("fixed", []), "message": 말}

    def revert_names(self, work_key: str) -> dict[str, Any]:
        """이름만 원래대로 되돌린다. 번역해 둔 한국어는 남는다."""
        찾은것 = next(
            (것 for 것 in self._작품별파일() if 것[0] == str(work_key)), None)
        if 찾은것 is None:
            return {"ok": False, "message": "그 작품을 찾지 못했습니다."}
        _, _, 일들 = 찾은것
        if not 일들:
            return {"ok": False, "message": "그 작품에 파일이 없습니다."}

        # 되돌리기는 일을 마치면서 이 표를 비운다. **미리** 잡아 둔다
        되돌릴표 = dict(titles_store.가져오기(work_key).get("original") or {})
        난것 = titles_store.되돌리기(work_key, 일들[0].audio.parent)
        if 난것.get("renamed"):
            self._이름따라가기(일들, 되돌릴표)
        if not 난것["ok"]:
            부분 = bool(난것.get("partial"))
            앞 = f"{난것['renamed']}개만 되돌렸습니다. " if 부분 else ""
            return {"ok": False, "partial": 부분,
                    "renamed": 난것.get("renamed", 0),
                    "blocked": 난것["blocked"],
                    "message": f"{앞}되돌리지 못했습니다: " + " / ".join(난것["blocked"][:3])}

        return {"ok": True, "renamed": 난것["renamed"],
                "message": f"{난것['renamed']}개를 원래 이름으로 되돌렸습니다."}

    def _이름따라가기(self, 일들: list[Job], 지도: dict[str, str]) -> None:
        """앱이 들고 있는 경로를 새 이름에 맞춘다.

        `지도` 는 **「앱이 지금 든 이름 → 가야 할 이름」** 이다. 부르는 쪽이
        만들어 준다 — 되돌리기는 일을 마치면서 그 표를 비우기 때문에, 여기서
        다시 읽으면 이미 없다.

        **디스크에 실제로 있는 것만 옮긴다.** 예전에는 「번역이 이러니 새
        이름은 이럴 것이다」 를 다시 계산했는데, 그러면 절반만 바뀌었을 때
        안 바뀐 파일까지 새 이름으로 바꿔 놓아서 **앱이 없는 파일을 가리킨
        채로** 남는다. 실패했을 때가 오히려 더 헷갈리는 상태다.
        """
        if not 일들 or not 지도:
            return
        with self._lock:
            for job in 일들:
                갈곳 = 지도.get(job.audio.name)
                if not 갈곳 or 갈곳 == job.audio.name:
                    continue
                if not (job.audio.parent / 갈곳).is_file():
                    continue          # 그 파일은 안 바뀌었다. 건드리지 않는다
                # `name` 은 `audio` 를 보고 답하는 값이라 따로 안 고친다
                job.audio = job.audio.with_name(갈곳)
        self._rebuild_queue()
        self._목록담기()

    # ---- 호칭 ----
    #
    # 「お兄さん → 언니」 같은 것은 한 번 틀리면 그 작품 내내 틀린다. 사람이
    # 한 줄 적어 주면 끝나는 일이라 그것만 받는다. 용어집이 아니다.

    def names(self) -> dict[str, Any]:
        """지금 작품의 호칭 표."""
        group = self.current_group()
        열쇠 = group.work_key if group is not None else ""
        return {
            "ok": bool(열쇠),
            "work": 열쇠,
            "title": group.title if group is not None else "",
            "text": names_store.가져오기(열쇠),
            "max_lines": names_store.줄한도,
        }

    def save_names(self, text: str) -> dict[str, Any]:
        """호칭 표를 담고 묶음을 다시 만든다.

        **다시 만들어야 프롬프트에 붙는다.** 담기만 하면 지금 화면에 떠 있는
        프롬프트는 옛것이라, 복사해 가서 붙여넣어도 호칭이 안 들어간다.
        """
        group = self.current_group()
        if group is None or not group.work_key:
            return {"ok": False, "message": "작품을 고른 뒤에 적어 주세요."}

        담긴것 = names_store.담기(group.work_key, text or "")
        # 손댄 묶음은 그대로 둔다. 번호가 바뀌면 이미 넣은 번역이 어긋난다
        with self._lock:
            for g in self.groups:
                if g.work_key == group.work_key and not g.touched:
                    한도 = self._묶음한도()
                    g.build(
                        context=names_store.붙이기(
                            getattr(g.jobs[0], "work_context", "") if g.jobs else "",
                            담긴것,
                        ),
                        **한도,
                    )
        줄수 = len([줄 for 줄 in 담긴것.splitlines() if 줄.strip()])
        return {
            "ok": True,
            "text": 담긴것,
            "message": f"{줄수}줄 담았습니다." if 줄수 else "비웠습니다.",
        }

    # ---- 검수 ----

    def _검수묶음(self) -> Group | None:
        """검수할 트랙.

        **다 끝난 트랙도 잡는다.** 끝나면 번역 화면에서 사라지는데, 검수는
        바로 그 끝난 것을 다시 보는 일이다. 안 잡으면 번역을 마치는 순간
        검수 단추가 「번역할 것이 없습니다」 로 바뀐다.
        """
        group = self.current_group()
        if group is not None and group.session is not None:
            return group
        끝난것 = [
            g for g in self.groups
            if g.session is not None and g.session.translations
        ]
        return 끝난것[-1] if 끝난것 else None

    def _검수대상(self, number: int = 0) -> tuple[Group | None, int]:
        """어느 트랙의 몇 번 묶음을 검수할지. 없으면 `(None, 0)`."""
        group = self._검수묶음()
        if group is None or group.session is None:
            return None, 0
        if number:
            return group, int(number)
        batch = group.session.pending_batch()
        if batch is not None and group.session.review_batch(batch.number) is not None:
            return group, batch.number
        # 다 끝난 트랙에는 「지금 묶음」이 없다. 검수할 수 있는 것 중 마지막
        할수있는것 = group.session.reviewable()
        return group, (할수있는것[-1] if 할수있는것 else 0)

    def review_prompt(self, number: int = 0) -> dict[str, Any]:
        """지금 보고 있는 묶음의 검수 프롬프트. 못 하면 왜 못 하는지."""
        group, 번호 = self._검수대상(number)
        if group is None or group.session is None:
            return {"ready": False, "message": "번역할 것이 없습니다."}
        검수 = group.session.review_batch(번호) if 번호 else None
        if 검수 is None:
            할수있는것 = group.session.reviewable()
            말 = (
                f"아직 안 끝난 묶음입니다. 끝난 묶음: {', '.join(map(str, 할수있는것))}번"
                if 할수있는것 else "먼저 한 번 번역해야 검수할 수 있습니다."
            )
            return {"ready": False, "message": 말, "can": 할수있는것}
        return {
            "ready": True,
            "text": 검수.prompt,
            "plain": 검수.plain,
            "number": 번호,
            "title": group.title,
            "lines": len(검수.segments),
            "prefers_file": 검수.prefers_file,
        }

    def local_review(self) -> dict[str, Any]:
        """내 컴퓨터 AI 로 지금 묶음을 검수한다.

        번역과 달리 **답을 화면에 돌려주지 않고 바로 넣는다.** 검수는 고칠
        줄만 오는 것이라 사람이 읽어 봐야 무엇이 달라졌는지 알 수 없다.
        달라진 것은 「듣고 고치기」 에서 원문과 나란히 보인다.
        """
        with self._lock:
            if self._local_run.get("busy"):
                return {"ok": True, "already": True, "message": "이미 도는 중입니다."}
            group, 번호 = self._검수대상()
            if group is None or group.session is None:
                return {"ok": False, "message": "번역할 것이 없습니다."}
            검수 = group.session.review_batch(번호) if 번호 else None
            if 검수 is None:
                return {"ok": False, "message": "먼저 한 번 번역해야 검수할 수 있습니다."}
            상태 = self.local_helper()
            if 상태["next"] != "ready":
                return {"ok": False, "message": 상태["note"], "needs": 상태["next"]}
            self._local_cancel.clear()
            self._local_run = {
                "busy": True, "done": 0, "total": len(검수.segments),
                "message": f"{번호}번 묶음 검수 중…", "finished": False, "ok": True,
                "answer": "", "review": False, "unit": "줄",
            }

        모델 = 상태["model"]
        번역설정 = self.settings.get("translation", {})

        def 돌리기() -> None:
            provider = None
            try:
                if self._pipeline is not None:
                    self._pipeline.release_model()
                provider = providers.create(
                    "ollama", model=모델, url=str(번역설정.get("url") or "")
                )
                고친수 = group.session.run_review_one(provider, 번호)
                if 고친수:
                    self._absorb(group)
                self._local_run["done"] = self._local_run["total"]
                self._local_run["message"] = (
                    f"{고친수}줄을 고쳤습니다." if 고친수
                    else "고칠 줄이 없다고 합니다."
                )
            except Exception as error:
                self._local_run.update(
                    {"ok": False, "message": f"검수하지 못했습니다: {error}"})
                log.error("내 컴퓨터 AI 검수 실패", error, 모델=모델)
            finally:
                if provider is not None:
                    provider.unload()
                self._local_run["finished"] = True
                self._local_run["busy"] = False

        if not self._run_in_background:
            돌리기()
        else:
            threading.Thread(target=돌리기, daemon=True).start()
        return {"ok": True, "started": True}

    def submit_review(self, pasted: str, number: int = 0) -> dict[str, Any]:
        """검수 답을 넣는다. 고친 줄만 갈아 끼운다."""
        group, 번호 = self._검수대상(number)
        if group is None or group.session is None:
            return {"ok": False, "message": "번역할 것이 없습니다."}
        if not 번호:
            return {"ok": False, "message": "검수할 묶음이 없습니다."}

        고친수 = group.session.submit_review(pasted or "", 번호)
        if 고친수:
            self._absorb(group)
        # 검수도 표가 박힌 글을 보고 하는 것이라 고친 줄에서 표가 빠질 수 있다
        낱말사고 = group.session.어긋난표말()
        한마디 = (f"{고친수}줄을 고쳤습니다." if 고친수
                else "고칠 줄이 없다고 합니다. 그대로 둡니다.")
        return {
            "ok": True,
            "changed": 고친수,
            "message": f"{한마디} {낱말사고}" if 낱말사고 else 한마디,
        }

    def submit(self, pasted: str, confirm: bool = False) -> dict[str, Any]:
        """AI에게 받은 답을 넣는다.

        **어느 묶음 것인지 고르지 않아도 된다.** 답에 적힌 줄 번호를 보고
        알아서 찾는다. 창을 일곱 개 열어 놓고 답이 나오는 대로 아무 순서로나
        넣을 수 있어야 하기 때문이다.

        **여러 트랙이 섞여 있으면 갈라서 각각 넣는다.** 트랙을 묶어서 한
        번에 맡겼으면 답도 한 덩이로 온다. 그때 트랙 하나만 골라 넣으면
        나머지는 조용히 버려진다.
        """
        나뉜것, 버린것 = self._답을트랙별로(pasted or "")
        if len(나뉜것) > 1:
            return self._여러트랙넣기(나뉜것, confirm, 버린것)

        group = self._답의주인(pasted or "") or self.current_group()
        if group is None or group.session is None:
            return {"ok": False, "message": "번역할 것이 없습니다."}

        # **담기 전에** 묻는다. 담고 나서 물으면 아니라고 해도 담긴 것이 남는다
        물음 = self._덮어쓰기물음(group, confirm)
        if 물음 is not None:
            return 물음

        # **여러 트랙을 동시에 돌리는 중이면 되매김을 받지 않는다.**
        #
        # 번호를 트랙마다 다른 칸으로 떼어 놔도, AI 가 1부터 다시 매겨 보내면
        # 되매김 복구가 「줄 수만 같으면」 순서대로 끼워 넣어서 딴 트랙 답이
        # 칸을 넘어 들어간다. 기다리는 트랙이 하나뿐일 때만 주인을 정할 수 있다
        기다리는것 = sum(
            1 for g in self.groups
            if g.session is not None and not g.session.done
        )
        group.session.되매김허용 = 기다리는것 <= 1

        어느것 = group.session.whose_answer(pasted or "")
        result = group.session.submit(pasted or "")

        # **내준 프롬프트의 기억을 트랙에 담는다.** 앱을 껐다 켠 뒤에 옛 답이
        # 들어와도 그새 바뀐 줄을 걸러 내려면 이것이 남아 있어야 한다
        self._내준것담기(group)

        if result.refused:
            # 예전에는 "Grok 처럼 덜 막는 AI 에서 다시 해 보세요" 라고만 했다.
            # 알려만 주고 여기서 할 수 있는 것이 없었다. 이제는 바로 옆에
            # 「내 컴퓨터 AI로 번역」 단추가 있으므로 그것을 가리킨다
            도움 = self.local_helper()
            길 = ("왼쪽 아래 「내 컴퓨터 AI로 번역」 을 누르면 이어서 합니다."
                  if 도움.get("next") == "ready"
                  else "왼쪽 아래 단추로 내 컴퓨터 AI 를 준비하면 이어서 할 수 있습니다.")
            return {
                "ok": False,
                "refused": True,
                "can_local": 도움.get("available", False),
                "message": f"거절당했습니다. {길}",
            }
        if result.not_korean:
            # **담기지는 않는다**(`TranslationSession.submit` 이 막는다). 그런데
            # 여기서 그 값을 안 봐서 "넣었습니다" 라고 답했다. 사용자는 됐다고
            # 알고 다음으로 넘어가는데 묶음은 그대로 남는다. 원문을 되돌려 주는
            # 모델을 쓰고도 그것을 모른 채 계속 붙여넣게 된다
            도움 = self.local_helper()
            길 = (" 「내 컴퓨터 AI로 번역」 을 눌러도 됩니다."
                  if 도움.get("next") == "ready" else "")
            return {
                "ok": False,
                "not_korean": True,
                "message": ("번역이 아니라 일본어가 그대로 온 것 같습니다. "
                            f"넣지 않았습니다. 다시 물어보세요.{길}"),
            }
        if result.renumbered and not result.translations:
            # **되매김을 막았으면 왜 막았는지 말한다.** 그냥 "못 읽었다" 고만
            # 하면 사용자는 멀쩡한 답을 몇 번이고 다시 붙여넣는다
            if not group.session.되매김허용:
                return {
                    "ok": False,
                    "message": ("답의 번호가 1번부터 다시 매겨져 있습니다. "
                                "지금은 트랙 여럿이 답을 기다리는 중이라 "
                                "어느 트랙 것인지 알 수 없어 넣지 않았습니다. "
                                "그 트랙을 열고 다시 넣어 주세요."),
                }
            return {
                "ok": False,
                "message": ("답의 번호가 1번부터 다시 매겨져 있고 줄 수도 "
                            "안 맞습니다. 번호를 그대로 두고 다시 물어보세요."),
            }
        if not result.translations and result.missing:
            # **「읽지 못했습니다」 만으로는 고칠 방법이 없다.**
            #
            # 벤더마다 복사되는 꼴이 다르고 다음 달에 또 바뀐다. 그때 사용자는
            # 복사를 눌렀는데 앱이 못 읽는다고만 하고, 무엇이 잘못됐는지 알
            # 길이 없다. 한국어 줄은 분명히 있는데 번호가 안 붙어 있다면
            # **그것을 말해 줘야** 한다
            글줄 = [줄.strip() for 줄 in (pasted or "").splitlines() if 줄.strip()]
            한글줄 = [줄 for 줄 in 글줄 if _한글있나(줄)]
            if len(한글줄) >= 3:
                return {
                    "ok": False,
                    "message": ("한국어 줄은 있는데 줄 번호가 안 붙어 있습니다. "
                                "AI 답을 다시 복사하거나, 번호까지 함께 오도록 "
                                "「번호<탭>한국어 로만 답하라」 고 한 번 더 일러 주세요."),
                }
            return {"ok": False, "message": "번역을 읽지 못했습니다. 답을 통째로 붙여넣어 주세요."}

        만든것 = self._absorb(group)
        어디 = f"{어느것}번 묶음 · " if 어느것 else ""

        # **적어 둔 낱말이 어긋났으면 반드시 말한다.** 빼먹힌 줄은 멀쩡해
        # 보이는 문장이 되어 아무 표시도 안 남는다. 여기서 안 짚으면 사용자는
        # 자기가 정한 낱말이 그대로 나간 줄 안다
        낱말사고 = group.session.어긋난표말() if group.session else ""

        # **그새 다시 받아쓴 줄은 왜 안 담았는지 말한다.** 안 그러면 사용자는
        # 답을 제대로 넣었는데 「빠졌다」 는 말만 보고 같은 답을 또 붙여넣는다
        낡은것 = list(getattr(group.session, "낡은줄", []) or [])
        낡은말 = (f" 그중 {len(낡은것)}줄은 그 사이에 다시 받아써서 "
                 "옛 답을 넣지 않았습니다 — 다시 복사해 주세요." if 낡은것 else "")

        if result.missing:
            return {
                "ok": True,
                "again": True,
                "message": (f"{어디}{len(result.missing)}줄이 빠져서 그것만 "
                            f"다시 물어봅니다.{낡은말}"
                            f"{' ' + 낱말사고 if 낱말사고 else ''}"),
            }

        # 자막은 번역이 있는 대로 계속 다시 쓴다(`_absorb`). 그래서 넣을 때마다
        # "자막을 만들었습니다" 가 떴다. 열네 묶음 중 네 번째를 넣었는데 다
        # 끝난 줄 알게 된다. **남은 것이 있으면 남았다고 말한다**
        남은것 = group.session.total_batches - group.session.finished_count
        if 남은것 > 0:
            message = f"{어디}넣었습니다. {남은것}묶음 남았습니다."
        elif 만든것:
            message = "자막을 만들었습니다: " + ", ".join(p.name for p in 만든것)
        else:
            message = f"{어디}넣었습니다."
        if 낱말사고:
            message = f"{message} {낱말사고}"
        return {
            "ok": True,
            "message": message,
            "done": self._다끝났나_알리며(self.current_group() is None),
            "made": [str(p) for p in 만든것],
        }

    def skip(self) -> dict[str, Any]:
        """지금 것을 미루고 다음으로 간다.

        미룰 다른 트랙이 있으면 그쪽으로만 옮긴다. **지우지 않는다.** 거절당해서
        넘긴 것을 나중에 다른 AI 로 다시 할 수 있어야 한다. 되돌아올 길이 없으면
        그 줄은 영영 자막에서 빠진다.

        옮겨 갈 트랙이 없을 때만 지금 묶음을 건너뛴다. 안 그러면 거절당한 묶음
        앞에서 아무 단추도 듣지 않는다. 트랙을 통째로 버리는 '포기' 밖에 길이
        없으면 뒤에 남은 묶음까지 같이 날아간다.
        """
        group = self.current_group()
        if group is None:
            return self.state()

        남은것 = self.pending_groups()
        if len(남은것) > 1:
            self._absorb(group)
            self._at = (self._at + 1) % len(self.pending_groups())
            self.notice = "이 트랙을 두고 다음으로 넘어갑니다. 나중에 돌아올 수 있습니다."
            return self.state()

        # 여기밖에 없다. 이 묶음을 건너뛰지 않으면 앞으로 갈 길이 없다
        if group.session is not None:
            남았던수 = group.session.total_batches - group.session.finished_count
            group.session.skip_current()
        else:
            남았던수 = 0
        self._absorb(group)
        self.notice = (
            "이 묶음을 건너뛰고 같은 트랙의 다음 묶음으로 갑니다."
            if 남았던수 > 1
            else "건너뛴 줄은 자막에서 빠집니다."
        )
        return self.state()

    def give_up(self) -> dict[str, Any]:
        """이 묶음을 아주 포기한다. 번역 안 된 줄은 자막에서 빠진다."""
        group = self.current_group()
        if group is None or group.session is None:
            return self.state()

        # **안 된 줄이 자막에서 통째로 빠진다.** 찍어 두고 간다
        찍은것 = [(j, dict(j.translations), j.output) for j in group.jobs]

        def 되돌리개(찍은것=찍은것, 열쇠=group.key) -> None:
            for job, 번역, 낸것 in 찍은것:
                job.translations = dict(번역)
                job.output = 낸것
                job.grouped = False
            # 묶음을 다시 짜야 남은 묶음이 되살아난다
            with self._lock:
                self.groups = [g for g in self.groups if g.key != 열쇠]

        while group.session.pending_batch() is not None:
            group.session.skip_current()
        self._absorb(group)
        self._되돌릴것찍기("포기했습니다", 되돌리개)
        self.notice = "포기했습니다. 번역된 줄만으로 자막을 만들었습니다."
        return {**self.state(), "undo": "포기"}

    def _덮어쓸자막(self, group: Group) -> list[str]:
        """이 묶음을 넣으면 **말없이 갈아끼울** 자막들.

        음원 옆에 이미 `.lrc` 가 있는데 우리가 이번에 만든 것이 아니면,
        손으로 고쳐 둔 것일 수 있다. 갈아끼우면 되돌릴 길이 없다.

        **우리가 이미 쓴 것(`job.output`)은 안 센다.** 열네 묶음 중 네
        번째를 넣을 때마다 물어보면 쓸 수가 없다.

        음원 옆이 아닌 다른 자리에 쓰는 설정이면 아무것도 안 본다. 그쪽은
        앱이 고른 새 자리라 사용자가 고쳐 둔 것이 없다.
        """
        if not self.settings.get("output", {}).get("next_to_audio", True):
            return []
        나온것 = []
        for job in group.jobs:
            if job.output:
                continue
            길 = lrc.output_path_for(job.audio)
            try:
                if 길.exists():
                    나온것.append(길.name)
            except OSError:
                # 못 보면 막지 않는다. 없는 것으로 치고 지나간다
                pass
        return 나온것

    def _내준것담기(self, group: Group) -> None:
        """세션이 적어 둔 「내줄 때 무슨 말이었나」를 트랙에 옮겨 담는다."""
        적힌것 = getattr(group.session, "내준원문", None)
        if not 적힌것:
            return
        for job in group.jobs:
            if not hasattr(job, "내준원문"):
                continue  # 시험의 가짜 일감
            if job.내준원문 != 적힌것:
                job.내준원문 = dict(적힌것)
                save_transcript(job)

    def 온보딩끝(self, route: str = "chat", ais: list[str] | None = None,
                 ) -> dict[str, Any]:
        """첫 실행에서 고른 길을 담는다.

        **물어만 보고 아무것도 안 바뀌면 물은 뜻이 없다.** 고른 길이 곧
        복사 형식이 된다 — 번역기로 갈 사람에게 지시문 붙은 것을 주면
        그것까지 번역해서 돌려준다.

        고르지 않은 길의 설정은 화면에도 안 보인다. 그것이 「설정을 쉽게」 다.
        """
        길 = str(route or "chat")
        고른AI = [str(a) for a in (ais or [])]

        # **고른 길이 곧 손잡이 넷을 정한다** (`route.ROUTES`).
        # 여기서 손잡이를 따로 적지 않는다 — 두 군데서 정하면 어긋난다
        덮을것: dict[str, Any] = {
            "onboarded": True,
            "onboarding": {"ais": 고른AI},
            # 첫 실행에서 고른 길이므로 건드린 손잡이는 없다
            "translation": {"route": routes.get(길).id, "고친것": {}, "_옮김": True},
        }

        self.settings = settings_store.save(덮을것)
        log.write("온보딩", "첫 실행에서 길을 골랐다", 길=길, AI=",".join(고른AI))
        return {"ok": True, **self.state()}

    # ---- 클립보드 감시 ----
    #
    # 「복사」 를 누른 뒤부터 본다. 화면에 켜졌다고 표시하고, 넣은 것은
    # 되돌릴 수 있다. **어디에도 안 적는다** — 클립보드에는 남의 비밀번호도
    # 지나간다. 판정에 쓰고 버린다.

    def 감시켜기(self) -> dict[str, Any]:
        """복사를 누르면 화면이 부른다. 못 켜져도 앱은 그대로 돈다."""
        self._감시.켜기()
        return self.감시상태()

    def 감시끄기(self) -> dict[str, Any]:
        self._감시.끄기()
        self._감시말 = ""
        return self.감시상태()

    def 감시상태(self) -> dict[str, Any]:
        난것 = dict(self._감시.상태())
        난것["말"] = self._감시말
        난것["되돌릴것"] = bool(self._감시가넣은것)
        # 어느 줄에 들어갔는지. 화면이 그 자리로 데려가 번쩍인다
        난것["넣은자리"] = self._감시자리
        난것["넣은횟수"] = self._감시횟수
        return 난것

    def _기다리는것들(self) -> list[catch.기다리는것]:
        """지금 답을 기다리는 트랙들. **앱의 물건을 그대로 넘기지 않는다.**"""
        난것 = []
        for g in self.groups:
            if g.session is None or g.session.done or not g.jobs:
                continue
            시작 = 칸시작(g.jobs[0])
            난것.append(catch.기다리는것(
                열쇠=g.key,
                칸시작=시작,
                칸끝=시작 + exchange.칸크기,
                내준원문=dict(getattr(g.session, "내준원문", {}) or {}),
            ))
        return 난것

    def _클립보드가바뀜(self, 글: str) -> None:
        """감시가 부른다. **여기서 아무것도 안 적는다.**

        판정이 받는다고 할 때만 `submit` 으로 넘긴다. 안 받으면 왜 안 받는지만
        남긴다 — 말없이 흘리면 「감시가 도는 건가」 부터 알 수 없다.
        """
        난것 = catch.보기(
            글, self._기다리는것들(),
            우리가낸것=list(self._내보낸글.values()),
        )
        if not 난것.받는다:
            # 남의 글(카톡·주소)은 조용히 넘긴다. 답 비슷한데 안 받은 것만
            # 말한다 — 복사할 때마다 말을 걸면 그것이 더 성가시다
            self._감시말 = 난것.말
            return

        group = self._그룹찾기(난것.주인)
        if group is None or group.session is None:
            return
        # 되돌릴 수 있게 **넣기 전에** 지금 것을 잡아 둔다
        되돌릴것 = {
            "key": group.key,
            "번역": {j.audio: dict(j.translations) for j in group.jobs},
        }
        답 = self.submit(글, confirm=True)
        if not 답.get("ok"):
            self._감시말 = str(답.get("message") or "")
            return
        self._감시가넣은것 = 되돌릴것
        self._감시말 = f"{난것.줄수}줄을 「{group.title}」 에 넣었습니다."
        # **어느 줄에 들어갔는지 화면이 알아야 한다.** 이름을 아무리 잘 골라도
        # 트랙 이름은 길어서 못 읽는다. 화면은 이 자리로 데려가 번쩍이면 된다 —
        # 읽고 이해하는 것보다 눈이 그리로 가는 편이 빠르다.
        # 셀 때마다 늘어나는 수를 같이 둔다. 같은 트랙에 두 번 넣어도 화면이
        # 「새로 들어왔다」 를 알아챈다
        # `Job` 은 제 자리를 모른다. 자리는 목록에서의 위치다 —
        # 화면이 쓰는 `index` 도 같은 값이다
        self._감시자리 = self._job자리(group.jobs[0]) if group.jobs else -1
        self._감시횟수 += 1

    def _되돌릴것찍기(self, 말: str, 되돌리개) -> None:
        """지우기 전에 어떻게 되돌릴지 적어 둔다.

        `되돌리개` 는 부르면 원래대로 돌려놓는 함수다. 창구 안에만 있으므로
        화면으로 나가지 않는다 — 화면은 `undo` 딱지만 보고 토스트를 띄운다.
        """
        self._되돌릴것 = {"말": 말, "되돌리개": 되돌리개}

    def undo_last(self) -> dict[str, Any]:
        """마지막으로 지운 것을 되돌린다. 한 번만 물릴 수 있다."""
        잡은것 = self._되돌릴것
        self._되돌릴것 = None
        if not 잡은것:
            return {"ok": False, "message": "되돌릴 것이 없습니다.", **self.state()}
        try:
            잡은것["되돌리개"]()
        except Exception as error:
            log.error("되돌리지 못했다", error)
            return {"ok": False, "message": f"되돌리지 못했습니다: {error}",
                    **self.state()}
        self._rebuild_queue()
        self.notice = f"{잡은것['말']} — 되돌렸습니다."
        self._목록담기()
        return {"ok": True, "message": self.notice, **self.state()}

    def 감시되돌리기(self) -> dict[str, Any]:
        """감시가 넣은 것을 물린다. 한 번만 물릴 수 있다."""
        잡은것 = self._감시가넣은것
        self._감시가넣은것 = None
        if not 잡은것:
            return {"ok": False, "message": "되돌릴 것이 없습니다."}
        group = self._그룹찾기(str(잡은것["key"]))
        if group is None:
            return {"ok": False, "message": "그 트랙이 없습니다."}
        for job in group.jobs:
            옛것 = 잡은것["번역"].get(job.audio)
            if 옛것 is not None:
                job.translations = dict(옛것)
        self._rebuild_queue()
        self._감시말 = "되돌렸습니다."
        return {"ok": True, **self.state()}

    # ---- 여러 트랙을 한 번에 ----
    #
    # 트랙마다 들어가서 복사하고 붙여넣기를 되풀이하는 것이 제일 힘들다는
    # 말을 들었다. 트랙이 넷이면 여덟 번을 오간다.
    #
    # **번호가 이미 트랙마다 갈라져 있어서** 합쳐 보내도 답이 제자리로
    # 돌아온다(`queue.칸시작`). 그래서 합치는 쪽만 만들면 된다.

    def _트랙의묶음(self, 자리: int) -> tuple[Group, Any] | None:
        """이 트랙 자리의 묶음과 다음 배치. 번역할 것이 없으면 None."""
        job = self._job(자리)
        if job is None:
            return None
        for g in self.groups:
            if g.session is None or g.done or job not in g.jobs:
                continue
            batch = g.session.pending_batch()
            if batch is not None:
                return g, batch
        return None

    def prompt_many(self, indices: list[int]) -> dict[str, Any]:
        """고른 트랙들의 다음 묶음을 **하나로 합쳐서** 준다.

        「한 번에 보낼 줄 수」 손잡이까지만 담는다. 트랙을 반만 자르지
        않고 통째로 담는다 — 반쪽만 보내면 나머지 반쪽을 또 따로 물어야 해서
        오가는 횟수가 도로 늘어난다.
        """
        고른 = [int(i) for i in (indices or [])]
        한도 = int(routes.정해진값(self.settings)["묶음"])

        낱장들: list[exchange.낱장] = []
        담은묶음: list[Group] = []
        못담은것: list[str] = []
        줄수 = 0
        for 자리 in 고른:
            찾음 = self._트랙의묶음(자리)
            if 찾음 is None:
                continue
            g, batch = 찾음
            if any(g is 든것 for 든것 in 담은묶음):
                continue          # 한 트랙을 두 번 담지 않는다
            길이 = len(batch.indices)
            if 낱장들 and 줄수 + 길이 > 한도:
                못담은것.append(g.title)
                continue
            job = g.jobs[0] if g.jobs else None
            낱장들.append(exchange.낱장(
                묶음=batch,
                트랙이름=g.track_name or g.title,
                작품이름=self._작품이름(g.work_key),
                작품열쇠=g.work_key or "",
            ))
            담은묶음.append(g)
            줄수 += 길이

        if not 낱장들:
            return {"ok": False, "message": "고른 것 중에 번역할 것이 없습니다."}

        for g in 담은묶음:
            # **합쳐 보낸 답은 되매김을 복구할 수 없다.** 1번부터 다시 매겨
            # 오면 어느 트랙 것인지 알 길이 없다. 아예 안 받는다
            if g.session is not None:
                g.session.되매김허용 = False
            self._내준것담기(g)

        작품열쇠들 = list(dict.fromkeys(장.작품열쇠 for 장 in 낱장들))
        return {
            "ok": True,
            "text": exchange.합친묶음글(낱장들),
            "plain": exchange.합친원문글(낱장들),
            "lines": 줄수,
            "tracks": [
                {"title": 장.트랙이름, "work": 장.작품이름,
                 "lines": len(장.묶음.indices)}
                for 장 in 낱장들
            ],
            "works": len(작품열쇠들),
            "left_out": 못담은것,
            # **조용히 틀리는 자리라 미리 말한다.** 막지는 않는다
            "name_clashes": names_store.부딪히나(작품열쇠들),
        }

    def _작품이름(self, 열쇠: str) -> str:
        정보 = self.works.get(str(열쇠 or ""))
        이름 = getattr(정보, "title", "") if 정보 is not None else ""
        return str(이름 or 열쇠 or "")

    def _답을트랙별로(
        self, pasted: str
    ) -> tuple[dict[str, list[tuple[int, str]]], int]:
        """붙여넣은 답을 **번호로** 트랙별로 가른다. 갈린 것과 **버려진 줄 수**.

        **푸는 것보다 가르는 것이 먼저다.** 가림표(`KW01`)는 트랙마다 표가
        다르다. 합쳐 놓고 풀면 트랙 A 의 `KW01` 을 트랙 B 의 표로 되돌려서
        엉뚱한 낱말이 조용히 들어간다. 가른 뒤에 각 트랙이 제 표로 푼다.

        **어느 칸에도 안 드는 줄을 센다.** 이미 끝낸 트랙 것이거나 AI 가
        지어낸 번호다. 여태는 말없이 버렸다 — 열일곱 줄을 넣었는데 「열두 줄
        넣음」 이라고만 뜨면, 앱이 흘린 것인지 AI 가 틀린 것인지 알 수가 없다.
        """
        칸: dict[str, tuple[int, int]] = {}
        for g in self.groups:
            if g.session is None or g.done or not g.jobs:
                continue
            시작 = 칸시작(g.jobs[0])
            칸[g.key] = (시작, 시작 + exchange.칸크기)

        난것: dict[str, list[tuple[int, str]]] = {}
        버린것 = 0
        for 번호, 글 in exchange.번호줄(pasted or ""):
            for 열쇠, (처음, 끝) in 칸.items():
                if 처음 <= 번호 < 끝:
                    난것.setdefault(열쇠, []).append((번호, 글))
                    break
            else:
                버린것 += 1
        return 난것, 버린것

    def _여러트랙넣기(self, 나뉜것: dict[str, list[tuple[int, str]]],
                      confirm: bool = False, 버린것: int = 0) -> dict[str, Any]:
        """트랙별로 가른 답을 각 트랙에 넣는다.

        **트랙마다 제 표로 푼다.** 여기까지 오면 이미 번호로 갈라져 있어서
        가림표가 섞일 자리가 없다.

        한 트랙이 실패해도 나머지는 넣는다. 하나 때문에 전부 버리면 다시
        받아 오는 수고가 통째로 되풀이된다.
        """
        넣음: list[str] = []
        빠짐: list[str] = []
        막힘: list[str] = []
        모두넣은줄 = 0

        for 열쇠, 줄들 in 나뉜것.items():
            group = self._그룹찾기(열쇠)
            if group is None or group.session is None:
                continue
            물음 = self._덮어쓰기물음(group, confirm)
            if 물음 is not None:
                return 물음      # 덮어쓸지부터 묻는다. 담고 나서 물으면 늦다

            # **합쳐 보낸 답은 되매김을 복구할 수 없다**
            group.session.되매김허용 = False
            글 = "\n".join(f"{번호}\t{말}" for 번호, 말 in 줄들)
            result = group.session.submit(글)
            self._내준것담기(group)

            이름 = group.track_name or group.title
            if result.refused:
                막힘.append(이름)
            elif result.not_korean:
                막힘.append(f"{이름}(일본어가 그대로 옴)")
            elif result.missing:
                빠짐.append(f"{이름} {len(result.missing)}줄 빠짐")
                모두넣은줄 += len(result.translations)
            else:
                넣음.append(f"{이름} {len(result.translations)}줄")
                모두넣은줄 += len(result.translations)
            self._absorb(group)

        if not 넣음 and not 빠짐 and not 막힘:
            return {"ok": False, "message": "넣을 것을 찾지 못했습니다."}

        말조각 = []
        if 넣음:
            말조각.append(" · ".join(넣음) + " 넣음")
        if 빠짐:
            말조각.append(" · ".join(빠짐))
        if 막힘:
            말조각.append("거절/못 읽음: " + " · ".join(막힘))
        # **버린 줄을 말한다.** 이미 끝낸 트랙 것이거나 AI 가 지어낸 번호다.
        # 말없이 버리면 앱이 흘린 것인지 AI 가 틀린 것인지 알 수가 없다
        if 버린것:
            말조각.append(
                f"{버린것}줄은 어느 트랙 것도 아니라 넘겼습니다"
                " (이미 끝낸 트랙이거나 AI 가 번호를 지어낸 것입니다)")
        self.notice = " / ".join(말조각)
        return {
            "ok": bool(넣음 or 빠짐),
            "many": True,
            "message": self.notice,
            "put": 넣음, "short": 빠짐, "blocked": 막힘,
            "dropped": 버린것,
            "lines": 모두넣은줄,
            **self.state(),
        }

    def _그룹찾기(self, key: str) -> Group | None:
        return next((g for g in self.groups if g.key == key), None)

    def _답의주인(self, pasted: str) -> Group | None:
        """붙여넣은 답이 **어느 트랙** 것인지 번호로 찾는다. 모르면 `None`.

        **병렬로 돌릴 때 트랙을 골라 주지 않아도 되게 하는 자리다.** 창을 여럿
        열어 트랙을 하나씩 맡기면 끝나는 차례가 제각각인데, 그때마다 앱에서
        그 트랙을 찾아 고르게 하면 그것부터가 일이고 잘못 고르면 조용히 엉뚱한
        트랙에 들어간다.

        트랙마다 번호 칸이 다르므로(`queue.칸시작`) 번호만 보면 주인이 정해진다.
        **가장 많이 겹치는 트랙**을 고른다 — 한 줄쯤 어긋나도 흔들리지 않게.
        """
        번호들 = {n for n, _ in exchange.번호줄(pasted)}
        if not 번호들:
            return None

        가장맞는것, 가장많이 = None, 0
        for g in self.groups:
            if g.session is None or g.session.done:
                continue
            겹침 = len(번호들 & set(g.owner))
            if 겹침 > 가장많이:
                가장맞는것, 가장많이 = g, 겹침
        return 가장맞는것

    def _덮어쓰기물음(self, group: Group, confirm: bool) -> dict[str, Any] | None:
        """덮어쓸 것이 있으면 한 번 더 묻는다. 물을 것이 없으면 `None`.

        **아무것도 담기 전에** 부른다. 담고 나서 물으면, 아니라고 해도 담긴
        것은 남아서 물어본 뜻이 없어진다.
        """
        열쇠 = group.work_key
        if 열쇠 in self._덮어써도됨:
            return None
        막힌것 = self._덮어쓸자막(group)
        if not 막힌것:
            return None
        if not confirm:
            보일것 = ", ".join(막힌것[:3]) + ("…" if len(막힌것) > 3 else "")
            return {
                "ok": False, "needs_confirm": True, "files": 막힌것,
                "message": (f"{보일것} 이 이미 있습니다. 손으로 고친 자막이라면 "
                            "사라집니다. 한 번 더 누르면 덮어씁니다."),
            }
        self._덮어써도됨.add(열쇠)
        log.write("자막", "덮어쓰기 허락", 작품=열쇠, 파일=막힌것)
        return None

    def _absorb(self, group: Group, pipeline: Pipeline | None = None) -> list[Path]:
        """모인 번역을 파일별로 나눠 담고, 다 찬 파일은 바로 자막으로 만든다.

        전체가 끝날 때까지 기다리지 않는다. 자막이 하나씩 나오는 편이 낫다.
        """
        pipeline = pipeline or self._make_pipeline(self.settings)
        만든것: list[Path] = []

        # 붙여넣은 것을 바로 담아 둔다. 창을 닫아도 날아가지 않는다
        group.absorb()
        for job in group.jobs:
            if job.transcribed:
                save_transcript(job)

        def 자막만들기(job: Job) -> None:
            """자막을 못 써도 대기열 전체가 멈추지 않게 한다."""
            try:
                pipeline.finish(job)
            except Exception as error:
                job.stage = Stage.실패
                job.error = f"자막을 만들지 못했습니다: {error}"
                log.error("자막 만들기 실패", error, 파일=job.audio.name)
                return
            if job.output:
                만든것.append(job.output)

        # 묶음이 다 끝나야 만드는 것이 아니다. 번역이 있는 대로 만든다.
        # 건너뛰거나 거절당해도 앞에서 한 것은 자막으로 남아야 한다
        for job in group.jobs:
            if any(job.translations.get(s["index"], "").strip() for s in job.segments):
                자막만들기(job)

        # 더 물어볼 것이 없으면 여기서 끝이다. 포기했거나 끝까지 건너뛴 경우다.
        # 자막은 이미 만들어 뒀으니 번역 차례로 남겨 두면 영영 안 끝난 것처럼 보인다
        if group.done:
            for job in group.jobs:
                if job.stage == Stage.번역 and job.output:
                    빠진줄 = sum(
                        1 for s in job.segments
                        if not job.translations.get(s["index"], "").strip()
                    )
                    job.stage = Stage.완료
                    job.progress = 1.0
                    if 빠진줄:
                        job.message = f"{빠진줄}줄은 번역이 없어 자막에서 빠졌습니다"
        return 만든것

    # ---- 설정과 파일 ----

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        # 화면은 가려진 키를 다시 보내므로, 가려진 값은 저장하지 않는다
        keys = patch.get("keys")
        if isinstance(keys, dict):
            patch["keys"] = {k: v for k, v in keys.items() if v and "●" not in str(v)}
        옛강도 = str(self.settings.get("asr", {}).get("preset", ""))
        self.settings = settings_store.save(patch)
        새강도 = str(self.settings.get("asr", {}).get("preset", ""))
        if 새강도 != 옛강도:
            self._강도바뀜()
        self.notice = "설정을 저장했습니다."
        return self.state()

    # ---- 낱말 목록 두 개 ----
    #
    # **모양이 똑같다.** 하나는 「내 낱말」(내가 정한 대로 옮긴다), 하나는
    # 「위험낱말」(미성년 설정을 짚는다). 둘 다 사용자가 켜고 끄고 더한다.
    #
    # 창구를 하나로 두면 화면도 하나로 만들 수 있다. 따로 만들면 두 번 만들고
    # 두 번 고치게 된다.

    def word_list(self, 어느것: str = "내낱말") -> dict[str, Any]:
        """`{종류, 목록, 글}`. `글` 은 글상자에 그대로 넣을 수 있는 모양이다."""
        if 어느것 == "위험낱말":
            목록 = minor_terms.목록읽기()
            return {"kind": 어느것, "목록": 목록, "글": 위험낱말글(목록)}
        목록 = wordbook.목록읽기()
        return {"kind": 어느것, "목록": 목록, "글": 내낱말글(목록)}

    def word_save(self, 어느것: str, 글: str) -> dict[str, Any]:
        """글상자에 적은 것을 그대로 받아 저장한다.

        **한 번에 통째로 받는다.** 줄을 더하면 추가, 지우면 삭제, 오른쪽을
        고치면 수정, `#` 를 붙이면 끄기다. 사용자가 배울 것이 없다.
        """
        try:
            if 어느것 == "위험낱말":
                난것 = minor_terms.목록쓰기(위험낱말읽기(글))
            else:
                난것 = wordbook.목록쓰기(내낱말읽기(글))
        except OSError as 오류:
            return {"ok": False, "message": f"저장하지 못했습니다: {오류}"}
        self.notice = f"낱말을 저장했습니다 ({난것.name})."
        # 위험낱말은 가리기와 무관하다 — 검사가 읽을 때마다 새 목록을 본다
        다시묶음 = self._새낱말로_다시묶기() if 어느것 == "내낱말" else 0
        답 = {"ok": True, **self.word_list(어느것)}
        if 다시묶음:
            답["applied"] = 다시묶음
            답["said"] = (
                f"번역 전 트랙 {다시묶음}개는 새 낱말을 바로 씁니다. "
                "복사해 둔 프롬프트가 있으면 다시 복사하세요."
            )
        return 답

    def _새낱말로_다시묶기(self) -> int:
        """번역 전 트랙을 새 낱말 목록으로 다시 묶는다. 몇 트랙을 다시 묶었는지.

        **손댄 트랙은 안 건드린다.** 내준 표가 바뀌면 이미 복사해 둔
        프롬프트로 받은 답의 `KW01` 이 딴 낱말로 조용히 되돌아온다 — 낱말
        하나를 껐다가 「젖가슴」 자리에 「자지」 가 들어가던 사고다. 번역이
        하나도 안 담긴 트랙만이 안전하다.

        새로 못 박은 사전은 바로 담아 둔다. 안 담으면 껐다 켤 때 또 옛
        사전으로 돌아간다.
        """
        groups = getattr(self, "groups", None)
        if not groups:
            return 0
        다시묶음 = 0
        with self._lock:
            for g in groups:
                if g.touched or g.session is None:
                    continue
                for job in g.jobs:
                    job.가림사전 = {}
                    job.사전담김 = False
                g.build(
                    context=names_store.붙이기(
                        getattr(g.jobs[0], "work_context", "") if g.jobs else "",
                        names_store.가져오기(g.work_key),
                    ),
                    **self._묶음한도(),
                )
                for job in g.jobs:
                    if job.transcribed and job.가림사전:
                        save_transcript(job)
                        job.사전담김 = True
                다시묶음 += 1
        return 다시묶음

    def word_reset(self, 어느것: str = "내낱말") -> dict[str, Any]:
        """기본 목록으로 돌린다. **잘못 지워 놓고 못 돌아오면 안 된다.**

        여태는 팝업으로 물었다. 이제는 안 묻고 **되돌릴 수 있게** 한다 —
        지우는 다른 일들과 같은 방식이다.
        """
        적어둔것 = (self.word_list(어느것) or {}).get("글", "")

        def 되돌리개(어느것=어느것, 글=적어둔것) -> None:
            self.word_save(글, 어느것)

        if 어느것 == "위험낱말":
            minor_terms.되돌리기()
        else:
            wordbook.되돌리기목록()
        self._되돌릴것찍기("기본 낱말로 되돌렸습니다", 되돌리개)
        self.notice = "기본 낱말로 되돌렸습니다."
        return {"ok": True, "undo": "낱말 되돌리기", **self.word_list(어느것)}

    def _강도바뀜(self) -> None:
        """강도를 바꿨으면 **되살려 둔 것을 다시 본다.**

        목록을 되살릴 때는 그때 저장돼 있던 강도로 판단한다. 그 뒤에 사용자가
        강도를 올리면, 이미 「번역 차례」로 올라와 있는 것들이 그대로 남아서
        시작을 눌러도 다시 받아쓰지 않는다. **강도를 고르는 뜻이 통째로 없어진다.**

        붙여넣은 번역이 있는 것은 건드리지 않는다. 다시 받아쓰면 줄 번호가
        바뀌어 그동안 붙여넣은 것이 통째로 쓸모없어진다.
        """
        지금 = presets.get(새강도) if (새강도 := str(
            self.settings.get("asr", {}).get("preset", ""))) else presets.get("")
        되돌린것 = 0
        with self._lock:
            대상 = list(self.jobs)
        for job in 대상:
            if job.stage in (Stage.받아쓰기, Stage.다시훑기):
                continue      # 도는 중인 것은 건드리지 않는다
            if not job.cached_preset or job.cached_preset == 지금.id:
                continue
            옛것 = presets.get(job.cached_preset)
            if job.translations:
                # 붙여넣은 것을 잃지 않는다. 다시 받아쓰면 줄 번호가 바뀌어
                # 그동안 붙여넣은 것이 통째로 쓸모없어진다.
                # **다만 왜 그대로인지는 알려 준다.** 말없이 두면 강도를 올린
                # 것이 안 먹은 줄 안다
                job.hint = 강도가_다르다는_말(옛것, 지금)
                continue
            job.segments = []
            job.report = None
            job.output = None
            job.progress = 0.0
            job.grouped = False
            job.stage = Stage.대기
            job.message = (
                f"전에 「{옛것.name}」로 받아쓴 것이 있습니다. "
                f"「{지금.name}」로 다시 받아씁니다"
            )
            되돌린것 += 1
        if 되돌린것:
            self._rebuild_queue()
            log.write("강도", "바뀌어 다시 받아쓸 것으로 돌림", 개수=되돌린것, 강도=지금.id)

    # ---- 강도를 실제로 재 보기 ----

    # ---- 복붙 화면에서 바로 내 컴퓨터 AI 로 넘기기 ----
    #
    # 사용자가 쓰는 길은 복붙이다. 브라우저에서 제미나이가 거절하는 것은 이
    # 프로그램이 **볼 수가 없다** — 아예 다른 창에서 일어나는 일이다.
    # 그래서 거절을 알아채고 넘겨주는 것이 아니라, 단추를 늘 띄워 두고
    # 사용자가 보고 누르게 한다.

    def local_helper(self) -> dict[str, Any]:
        """복붙 화면의 「내 컴퓨터 AI로 번역」 단추가 무엇을 보여줄지.

        화면은 이것만 보고 그린다. 무엇을 눌러야 하는지(`next`)까지 여기서 정한다.
        """
        빈것 = {
            "available": False, "next": "install", "model": "", "remaining": 0,
            "label": "Ollama 받으러 가기", "note": "내 컴퓨터에서 도는 번역 AI 가 없습니다",
            "busy": False, "url": ollama.INSTALL_URL,
        }
        group = self.current_group()
        남은것 = 0
        if group is not None and group.session is not None:
            남은것 = max(0, group.session.total_batches - group.session.finished_count)

        try:
            base = ollama.base_from(str(self.settings.get("translation", {}).get("url", "")))
            if ollama.find_exe() is None:
                return {**빈것, "remaining": 남은것}
            if not ollama.is_running(base):
                return {**빈것, "available": True, "next": "start", "remaining": 남은것,
                        "label": "Ollama 켜기", "url": "",
                        "note": "깔려 있는데 안 켜져 있습니다. 누르면 켭니다"}

            크기 = ollama.model_sizes(base)
            고른것 = providers.pick_local_model(
                크기,
                gpu.total_vram_gb(),
                prefer=str(self.settings.get("translation", {}).get("model", "")),
            )
            if not 고른것:
                기본 = ollama.DEFAULT_MODEL
                return {**빈것, "available": True, "next": "pull", "remaining": 남은것,
                        "model": 기본, "label": f"{기본} 받기", "url": "",
                        "note": "번역할 모델이 아직 없습니다. 한 번만 받으면 됩니다"}

            return {
                "available": True, "next": "ready", "model": 고른것, "remaining": 남은것,
                "label": "내 컴퓨터 AI로 번역", "url": "",
                "note": f"{고른것} · 거절 없음",
                "busy": self._local_run.get("busy", False),
            }
        except Exception as error:
            log.error("내 컴퓨터 AI 상태 확인 실패", error)
            return {**빈것, "remaining": 남은것}

    def translate_locally(self, all_remaining: bool = False) -> dict[str, Any]:
        """내 컴퓨터 AI 로 번역한다.

        ## 한 묶음일 때는 **담지 않는다**

        답을 그대로 돌려주기만 하고, 화면이 그것을 「답 붙여넣기」 칸에 넣는다.
        거기서부터는 밖의 AI 에서 복사해 온 것과 **똑같은 길**이다 — 사용자가
        읽어 보고 「넣기」를 누른다.

        예전에는 번역해서 곧바로 담고 자막까지 만들었다. 화면에는 아무 일도
        안 일어나 보여서 「단추가 안 눌린다」 로 느껴졌고, 정작 눈으로 볼
        기회도 없이 `.lrc` 가 나와 있었다. **검수가 이 프로그램의 핵심인데
        그 단계를 통째로 건너뛴 것이다.**

        로컬 모델은 밖의 AI 보다 자주 틀린다. 그것을 안 보고 넘기면 안 된다.

        ## 「남은 것 전부」는 그대로 자동이다

        열여덟 묶음을 하나씩 칸에 넣어 줄 수는 없다. 이쪽은 검수 없이 담는
        길이고, 단추 이름에 그렇게 적어 둔다.

        오래 걸리므로 따로 도는 일꾼에게 맡기고 바로 돌려준다. 화면은
        `local_progress()` 로 물어본다.
        """
        with self._lock:
            if self._local_run.get("busy"):
                return {"ok": True, "already": True, "message": "이미 번역하는 중입니다."}
            group = self.current_group()
            if group is None or group.session is None:
                return {"ok": False, "message": "번역할 것이 없습니다."}
            상태 = self.local_helper()
            if 상태["next"] != "ready":
                return {"ok": False, "message": 상태["note"], "needs": 상태["next"]}
            self._local_run = {
                "busy": True, "done": 0,
                "total": (상태["remaining"] if all_remaining else 1),
                "message": "시작합니다", "finished": False, "ok": True,
                # 검수하라고 화면에 돌려줄 답. 「남은 것 전부」에서는 비어 있다
                "answer": "",
                "review": not all_remaining,
                # 진행 막대에 쓸 단위. 한 묶음일 때는 줄, 전부일 때는 묶음이다
                "unit": "줄" if not all_remaining else "묶음",
            }

        모델 = 상태["model"]
        번역설정 = self.settings.get("translation", {})

        def 돌리기() -> None:
            provider = None
            try:
                # 받아쓰기 모델을 먼저 내린다. 12GB 에 둘 다 올리면 터진다
                if self._pipeline is not None:
                    self._pipeline.release_model()
                provider = providers.create(
                    "ollama", model=모델, url=str(번역설정.get("url") or "")
                )

                if not all_remaining:
                    # 담지 않는다. 답만 받아서 화면에 돌려준다.
                    # 여기서부터는 밖의 AI 에서 복사해 온 것과 똑같은 길이다
                    batch = group.session.pending_batch()
                    if batch is None:
                        self._local_run["message"] = "번역할 묶음이 없습니다."
                        return
                    바랄줄 = len(batch.indices)
                    self._local_run.update({
                        "total": 바랄줄, "unit": "줄",
                        "message": f"{batch.number}번 묶음 번역 중…",
                    })

                    def 흘러올때(그때까지: str) -> None:
                        """받는 대로 몇 줄이 왔는지 센다.

                        1~3분 동안 화면에 아무 표시도 없으면 사용자는 멈춘 줄
                        알고 창을 껐다 켠다. 껐다 켜면 처음부터다.

                        **그만두라고 했으면 여기서 끊는다.** 답이 흘러오는
                        동안이 제일 긴 구간이라, 여기서 안 끊으면 「취소」 를
                        눌러도 몇 분을 더 기다리게 된다.
                        """
                        if self._local_cancel.is_set():
                            raise _그만둠()
                        온줄 = len(exchange.번호줄(그때까지))
                        self._local_run["done"] = min(온줄, 바랄줄)
                        self._local_run["message"] = (
                            f"{batch.number}번 묶음 번역 중 · {온줄}/{바랄줄}줄"
                        )

                    # 지시문과 알맹이를 나눠 보내는 길. 작은 모델은 한 덩어리로
                    # 주면 우리 규칙에 번호를 매겨서 되돌려 준다
                    self._local_run["answer"] = translate_module._물어보기(
                        provider, batch, 흘러올때
                    )
                    self._local_run["done"] = 바랄줄
                    self._local_run["message"] = (
                        f"{batch.number}번 묶음을 번역했습니다. "
                        "읽어 보고 「넣기」를 누르세요"
                    )
                    return

                while True:
                    self._local_run["message"] = (
                        f"번역 중 {self._local_run['done'] + 1}/{self._local_run['total']}"
                    )
                    if not group.session.run_one(provider):
                        break
                    self._local_run["done"] += 1
                    self._absorb(group)
                    if not all_remaining or self._local_run["done"] >= self._local_run["total"]:
                        break
                    if self._stop.is_set() or self._local_cancel.is_set():
                        break
                self._local_run["message"] = f"{self._local_run['done']}묶음을 번역했습니다."
            except _그만둠:
                # 사용자가 무른 것이다. 빨간 오류로 띄우지 않는다
                self._local_run.update({"ok": True, "message": "번역을 그만뒀습니다."})
                log.write("번역", "내 컴퓨터 AI 번역을 사용자가 그만둠", 모델=모델)
            except Exception as error:
                if self._local_cancel.is_set():
                    self._local_run.update({"ok": True, "message": "번역을 그만뒀습니다."})
                    log.write("번역", "그만두는 중에 끊김", 모델=모델)
                else:
                    self._local_run.update(
                        {"ok": False, "message": f"번역하지 못했습니다: {error}"})
                    log.error("내 컴퓨터 AI 번역 실패", error, 모델=모델)
            finally:
                if provider is not None:
                    provider.unload()  # 그래픽카드를 받아쓰기에 돌려준다
                self._local_run["finished"] = True
                self._local_run["busy"] = False

        if not self._run_in_background:
            돌리기()
        else:
            threading.Thread(target=돌리기, daemon=True).start()
        return {"ok": True, "message": f"{모델} 로 번역을 시작했습니다."}

    def translate_locally_stop(self) -> dict[str, Any]:
        """내 컴퓨터 AI 번역을 그만둔다.

        실수로 눌렀는데 몇 분을 기다려야 하면, 사용자는 창을 껐다 켠다.
        껐다 켜면 처음부터다.
        """
        if not self._local_run.get("busy"):
            return {"ok": True, "message": "도는 것이 없습니다.", **self.state()}
        self._local_cancel.set()
        self._local_run["message"] = "그만두는 중입니다…"
        return {"ok": True, "message": "그만두는 중입니다.", **self.state()}

    # ---- 내 컴퓨터 AI 대기열 ----
    #
    # 한 트랙을 맡기면 끝날 때까지 붙잡혀 있었다. 트랙이 열다섯 개면 열다섯
    # 번을 기다렸다가 눌러야 한다. **걸어 두고 다른 트랙으로 갈 수 있어야
    # 한다.** 넣은 차례대로 돌고, 같은 단추를 다시 누르면 그것만 빠진다.

    def _대기자리(self, group: Group) -> int:
        """이 트랙이 대기열에서 몇 번째인가. 안 걸려 있으면 0."""
        with self._lock:
            줄 = list(self._번역줄)
        return 줄.index(group.key) + 1 if group.key in 줄 else 0

    def queue_local(self, at: int = -1, confirm: bool = False) -> dict[str, Any]:
        """이 트랙을 대기열에 넣거나 뺀다. **같은 단추가 둘 다 한다.**

        걸어 둔 것을 빼려고 다른 화면을 찾아 들어가게 만들지 않는다. 넣을 때
        누른 그 자리에서 다시 누르면 빠진다.
        """
        남은것 = self.pending_groups()
        if not 남은것:
            return {"ok": False, "message": "번역할 것이 없습니다."}
        자리 = self._at if at is None or int(at) < 0 else int(at)
        자리 = max(0, min(자리, len(남은것) - 1))
        group = 남은것[자리]

        with self._lock:
            이미걸림 = group.key in self._번역줄
        # **걸기 전에** 묻는다. 이쪽은 일꾼이 배경에서 자막까지 써 버려서,
        # 걸고 나면 물어볼 틈이 없다. 빼는 것은 안 묻는다
        if not 이미걸림:
            물음 = self._덮어쓰기물음(group, confirm)
            if 물음 is not None:
                return 물음

        with self._lock:
            if group.key in self._번역줄:
                self._번역줄.remove(group.key)
                뺐다 = True
            elif self._도는트랙 == group.key:
                return {"ok": False,
                        "message": "지금 이 트랙을 번역하는 중입니다. 「멈추기」 를 쓰세요."}
            else:
                self._번역줄.append(group.key)
                뺐다 = False
            남았나 = len(self._번역줄)

        if 뺐다:
            return {"ok": True, "queued": False,
                    "message": f"대기열에서 뺐습니다. {남았나}개 남음"}

        상태 = self.local_helper()
        if 상태.get("next") != "ready":
            with self._lock:
                if group.key in self._번역줄:
                    self._번역줄.remove(group.key)
            return {"ok": False, "message": 상태.get("note") or "내 컴퓨터 AI 가 준비되지 않았습니다."}

        self._줄일꾼시작()
        return {"ok": True, "queued": True,
                "message": f"대기열에 넣었습니다. {남았나}번째"}

    def queue_state(self) -> dict[str, Any]:
        """대기열이 지금 어떤지. 화면이 짧은 간격으로 물어본다."""
        남은것 = self.pending_groups()
        이름 = {g.key: f"{g.track_no}. {g.track_name}" for g in 남은것}
        with self._lock:
            줄 = list(self._번역줄)
            도는것 = self._도는트랙
        # **지금 보고 있는 트랙**이 몇 번째인지도 여기서 센다. 화면이 트랙
        # 열쇠를 들고 다니며 맞춰 보게 만들 이유가 없다
        지금 = self.current_group()
        여기 = (줄.index(지금.key) + 1) if 지금 is not None and 지금.key in 줄 else 0
        return {
            "queue": [{"key": 열쇠, "name": 이름.get(열쇠, 열쇠)} for 열쇠 in 줄],
            "running": 도는것,
            "running_name": 이름.get(도는것, ""),
            "busy": bool(도는것) or bool(줄),
            "here": 여기,
            "here_running": 지금 is not None and 도는것 == 지금.key,
        }

    def clear_queue(self) -> dict[str, Any]:
        with self._lock:
            self._번역줄 = []
        return {"ok": True, "message": "대기열을 비웠습니다."}

    def _줄일꾼시작(self) -> None:
        with self._lock:
            if self._줄일꾼돎:
                return
            self._줄일꾼돎 = True
        if not self._run_in_background:
            self._줄일꾼()
        else:
            threading.Thread(target=self._줄일꾼, daemon=True).start()

    def _줄일꾼(self) -> None:
        """대기열을 앞에서부터 하나씩 끝낸다.

        트랙 하나를 통째로 끝내고 다음으로 간다. 도중에 사용자가 그 트랙을
        빼면 다음 트랙부터 적용된다 — 이미 시작한 것을 중간에 자르면 묶음이
        반만 담긴 채로 남는다.
        """
        try:
            while True:
                with self._lock:
                    if not self._번역줄:
                        return
                    열쇠 = self._번역줄.pop(0)
                    self._도는트랙 = 열쇠
                try:
                    self._트랙하나번역(열쇠)
                except Exception as error:      # noqa: BLE001
                    log.error("대기열 번역 실패", error, 트랙=열쇠)
                finally:
                    with self._lock:
                        self._도는트랙 = ""
                if self._stop.is_set():
                    return
        finally:
            with self._lock:
                self._줄일꾼돎 = False

    def _트랙하나번역(self, 열쇠: str) -> None:
        group = next((g for g in self.groups if g.key == 열쇠), None)
        if group is None or group.session is None or group.done:
            return
        상태 = self.local_helper()
        if 상태.get("next") != "ready":
            log.write("대기열", "준비가 안 돼 건너뜀", 트랙=열쇠, 까닭=상태.get("note"))
            return

        모델 = 상태["model"]
        번역설정 = self.settings.get("translation", {})
        provider = None
        try:
            self._그래픽카드_비우기()
            provider = providers.create(
                "ollama", model=모델, url=str(번역설정.get("url") or "")
            )
            while not group.done:
                if self._stop.is_set():
                    return
                if not group.session.run_one(provider):
                    break
                self._absorb(group)
        finally:
            if provider is not None:
                provider.unload()

    def local_progress(self) -> dict[str, Any]:
        """번역이 어디까지 왔는지. 화면이 짧은 간격으로 물어본다."""
        return dict(self._local_run)

    def compare_preset(self, index: int, preset_id: str) -> dict[str, Any]:
        """지금 받아쓴 것과, 다른 강도로 받아쓴 것을 견준다.

        강도를 다섯 개 만들어 놓고 **어느 것이 실제로 나은지 잰 적이 없었다.**
        만드는 쪽에는 GPU 도 음원도 없어 잴 방법이 없었고, 사용자는 "잘 안
        잡히는 것 같다" 는 느낌밖에 말할 수 없었다. 그러면 계속 추측으로 값을
        만지게 된다.

        따로 도는 일꾼에게 맡긴다. 2시간짜리를 다시 받아쓰는 동안 창이 얼어
        있으면 안 된다.
        """
        job = self._job(index)
        if job is None or not job.transcribed:
            return {"ok": False, "message": "먼저 한 번 받아써야 견줄 수 있습니다."}
        if self.busy:
            return {"ok": False, "message": "돌아가는 중에는 견줄 수 없습니다."}

        새강도 = presets.get(preset_id)
        지금강도 = presets.get(self.settings.get("asr", {}).get("preset", presets.DEFAULT))
        if 새강도.id == 지금강도.id:
            return {"ok": False, "message": "같은 강도끼리는 견줄 것이 없습니다."}

        with self._lock:
            if self._comparing:
                return {"ok": True, "already": True, "message": "이미 견주는 중입니다."}
            self._comparing = True
            # 멈춤 깃발을 내린다.
            #
            # `stop()` 이 세운 깃발은 `start()` 에서만 내려간다. 그래서 멈추기를
            # 한 번이라도 누른 뒤에 견주면, 다시 받아쓰기가 **시작하자마자 멈춰서**
            # 오른쪽이 0줄이 된다. 그러면 "새로 잡은 곳 0군데, 잡힌 시간 -100%p" 가
            # 나와서, 더 센 강도가 아무것도 못 잡는 것처럼 보인다.
            # 여기 오는 길은 `busy` 가 아닐 때뿐이라 내려도 안전하다
            self._stop.clear()
            self._compare = {
                "done": False, "ok": True, "message": f"{새강도.name} 로 다시 받아쓰는 중",
                "result": None, "left": 지금강도.name, "right": 새강도.name,
            }

        원래줄 = list(job.segments)
        길이 = job.duration_sec

        def 재보기() -> None:
            try:
                설정 = {**self.settings, "asr": {**self.settings.get("asr", {}), "preset": 새강도.id}}
                흐름 = self._make_pipeline(설정)
                딴것 = Job(audio=job.audio)
                # 담아 둔 것을 읽으면 두 강도가 같은 결과를 내놓는다.
                # 그러면 "차이 없음" 만 나와서 견주는 뜻이 없어진다
                흐름.transcribe(딴것, should_stop=self._stop.is_set, use_cache=False)
                if not 딴것.segments:
                    # 도중에 멈췄거나 아무것도 못 받아썼다. 이대로 견주면
                    # "새로 잡은 곳 0군데" 가 나와서 더 센 강도가 쓸모없어 보인다
                    self._compare.update({
                        "done": True, "ok": False,
                        "message": "견주다 멈췄습니다. 견줄 것이 없습니다.",
                    })
                    return
                결과 = 견주기.compare(지금강도.name, 원래줄, 새강도.name, 딴것.segments, 길이 or 딴것.duration_sec)
                self._compare.update({
                    "done": True, "ok": True,
                    "message": 결과.summary(), "result": 결과.to_view(),
                })
                log.write("견주기", 결과.summary(), 파일=job.audio.name,
                          왼쪽=지금강도.id, 오른쪽=새강도.id)
            except Exception as error:
                log.error("견주기 실패", error, 파일=job.audio.name)
                self._compare.update({
                    "done": True, "ok": False, "message": f"견주지 못했습니다: {error}",
                })
            finally:
                self._comparing = False
                try:
                    흐름.release_model()
                except Exception:
                    pass

        if not self._run_in_background:
            재보기()
        else:
            threading.Thread(target=재보기, daemon=True).start()
        return {"ok": True, "message": f"{새강도.name} 로 다시 받아쓰고 있습니다."}

    def compare_progress(self) -> dict[str, Any]:
        return dict(self._compare)

    # ---- 앞 2분만 미리 받아쓰기 ----
    #
    # 3시간짜리를 20분 돌리고 나서야 강도가 안 맞는 것을 알면 그 20분이
    # 통째로 아깝다. 앞 2분만 잘라 받아쓰면 몇십 초 만에 「이 강도로 이
    # 트랙이 어떻게 받아지는지」 를 눈으로 본다.

    미리듣기_초 = 120.0

    def preview_transcribe(self, index: int) -> dict[str, Any]:
        """앞 2분만 잘라 지금 강도로 받아써 본다. 담아 두지 않는다."""
        job = self._job(index)
        if job is None:
            return {"ok": False, "message": "그 파일을 찾지 못했습니다."}
        if not job.audio.is_file():
            return {"ok": False, "message": "음원 파일이 그 자리에 없습니다."}
        if self.busy:
            return {"ok": False, "message": "받아쓰는 중에는 못 합니다."}

        with self._lock:
            if self._previewing:
                return {"ok": True, "already": True, "message": "이미 미리 받아쓰는 중입니다."}
            self._previewing = True
            self._stop.clear()
            강도 = presets.get(self.settings.get("asr", {}).get("preset", presets.DEFAULT))
            self._preview = {
                "done": False, "ok": True, "index": int(index),
                "message": f"앞 2분을 「{강도.name}」 로 받아쓰는 중…", "lines": [],
            }

        def 해보기() -> None:
            잘린것 = settings_store.config_dir() / "preview.wav"
            try:
                소리, _ = clip.extract_wav(
                    job.audio, 0.0, self.미리듣기_초, pad_sec=0.0, max_sec=self.미리듣기_초
                )
                잘린것.parent.mkdir(parents=True, exist_ok=True)
                잘린것.write_bytes(소리)

                흐름 = self._make_pipeline(self.settings)
                딴것 = Job(audio=잘린것)
                try:
                    # 담아 둔 것을 쓰지도, 남기지도 않는 길이다. preview.wav 는
                    # 매번 새로 쓰여서 열쇠(크기·시각)가 달라진다
                    흐름.transcribe(딴것, should_stop=self._stop.is_set, use_cache=False)
                finally:
                    try:
                        흐름.release_model()
                    except Exception:
                        pass

                if not 딴것.segments:
                    self._preview.update({
                        "done": True, "ok": False,
                        "message": "앞 2분에서 말을 하나도 못 잡았습니다. "
                                   "인트로가 무음일 수 있습니다 — 그것만으로 강도를 판단하지 마세요.",
                    })
                    return
                from app.core.job import _clock
                self._preview.update({
                    "done": True, "ok": True,
                    "message": f"앞 2분에서 {len(딴것.segments)}줄을 받아썼습니다.",
                    "lines": [
                        {"at": _clock(s["start"]), "ja": s["ja"]}
                        for s in 딴것.segments[:100]
                    ],
                })
                log.write("미리받아쓰기", "끝", 파일=job.audio.name, 줄수=len(딴것.segments))
            except Exception as error:
                log.error("미리 받아쓰기 실패", error, 파일=job.audio.name)
                self._preview.update({
                    "done": True, "ok": False, "message": f"미리 받아쓰지 못했습니다: {error}",
                })
            finally:
                self._previewing = False
                try:
                    잘린것.unlink(missing_ok=True)
                except OSError:
                    pass

        if not self._run_in_background:
            해보기()
        else:
            threading.Thread(target=해보기, daemon=True).start()
        return {"ok": True, "message": "앞 2분을 받아쓰고 있습니다."}

    def preview_progress(self) -> dict[str, Any]:
        return dict(self._preview)

    # ---- 파형 ----

    def waveform(self, index: int) -> dict[str, Any]:
        """트랙 전체의 소리 크기 곡선. 검수 화면의 파형 띠가 쓴다.

        처음 부르면 파일을 통째로 읽어 몇 초쯤 걸린다. 한 번 잰 것은 담아
        두므로 두 번째부터는 바로 온다. 화면은 오는 동안 띠를 숨겨 두면 된다.
        """
        job = self._job(index)
        if job is None:
            return {"ok": False, "message": "그 파일을 찾지 못했습니다."}
        if not job.audio.is_file():
            return {"ok": False, "message": "음원 파일이 그 자리에 없습니다."}
        from app.core import waveform as 파형
        try:
            난것, 길이 = 파형.peaks_cached(job.audio)
        except 파형.WaveUnavailable as error:
            return {"ok": False, "message": str(error)}
        except Exception as error:
            log.error("파형 재기 실패", error, 파일=job.audio.name)
            return {"ok": False, "message": f"파형을 재지 못했습니다: {error}"}
        return {"ok": True, "peaks": 난것, "duration": 길이,
                "bucket_sec": 파형.BUCKET_SEC}

    # ---- 내 컴퓨터에서 도는 번역 모델 ----

    def local_status(self, url: str = "", model: str = "") -> dict[str, Any]:
        """Ollama 가 지금 어디까지 됐는지. 화면은 이것만 보고 그린다."""
        번역 = self.settings.get("translation", {})
        고른것 = model or str(번역.get("model", "")) or "qwen2.5:14b"
        try:
            상태 = ollama.status(url or str(번역.get("url", "")), 고른것)
        except Exception as error:
            log.error("Ollama 상태 확인 실패", error)
            return {"installed": False, "running": False, "has_model": False,
                    "models": [], "next": "install", "message": f"확인하지 못했습니다: {error}"}
        상태.update(self._그래픽카드_안내(상태, 고른것))
        상태.update(self._모델카드(상태, 고른것))
        return 상태

    def _모델카드(self, 상태: dict[str, Any], 고른것: str) -> dict[str, Any]:
        """모델을 **이름 말고 성격으로** 고르게 한다.

        예전에는 이름만 늘어놓았다. `qwen2.5:14b` 와
        `huihui_ai/qwen2.5-vl-abliterated:7b` 를 보고 무엇을 골라야 하는지
        알 수가 없다. 알고 싶은 것은 「한국어가 자연스러운 것」이지 이름이 아니다.

        실제로 이름만 보고 `-vl`(그림 보는 판)을 골랐고, 그 모델이 우리
        지시문을 번역해서 되돌려 줬다.
        """
        try:
            vram = gpu.total_vram_gb()
            크기 = ollama.model_sizes(ollama.base_from(str(
                self.settings.get("translation", {}).get("url", ""))))
        except Exception as error:
            log.error("모델 목록 읽기 실패", error)
            return {"cards": [], "missing": []}

        깔린것 = sorted(크기)

        # **실제로 무엇이 도는지.** 고른 것이 그래픽카드에 안 들어가면
        # `pick_local_model` 이 말없이 다른 것으로 바꾼다. 그 판단은 옳지만,
        # 설정 화면은 여전히 고른 것을 골라진 채로 보여 줬다. 그래서 설정에는
        # `qwen2.5:14b`, 번역 화면 단추 밑에는 `exaone3.5:7.8b` 이 떴다.
        # **두 화면이 다른 말을 하면 어느 쪽도 못 믿는다.**
        쓸것 = providers.pick_local_model(크기, vram, 고른것)

        카드 = []
        for 이름 in 깔린것:
            하나 = model_notes.설명하기(이름, 크기.get(이름, 0.0))
            들어감 = None
            if vram and 크기.get(이름):
                들어감 = providers.그래픽카드에_들어가나(
                    크기[이름], providers.MAX_NUM_CTX, vram
                )[0]
            고름 = ollama._풀이름(이름) == ollama._풀이름(고른것)
            하나.update({
                "fits": 들어감,
                "chosen": 고름,
                # 눌러 둔 것과 **정말 도는 것**은 다를 수 있다
                "using": bool(쓸것) and ollama._풀이름(이름) == ollama._풀이름(쓸것),
                # 그림 모델·임베딩은 번역에 못 쓴다. 고를 수 있게 두면 또 고른다
                "usable": providers.번역용인가(이름),
            })
            if 고름 and not 하나["using"] and 쓸것:
                하나["instead"] = 쓸것
            카드.append(하나)

        # 번역에 쓸 수 있는 것을 먼저, 그중 아는 것을 먼저 보여 준다
        카드.sort(key=lambda c: (not c["usable"], not c["known"], c["id"]))
        return {"cards": 카드, "missing": model_notes.아직_없는것(깔린것)}

    def _그래픽카드_안내(self, 상태: dict[str, Any], 고른것: str) -> dict[str, Any]:
        """고른 모델이 그래픽카드에 들어가는지. 안 들어가면 그렇게 말해 준다.

        안 들어가면 Ollama 는 **오류를 내지 않는다.** 일부 층을 조용히 CPU 로
        내리고 몇 배 느려질 뿐이라, 사용자는 원래 이런 건 줄 안다.

        추측하지 않는다. 모델 크기는 Ollama 가 알려 준 실제 파일 크기를 쓰고,
        VRAM 은 `nvidia-smi` 로 잰다. 둘 중 하나라도 못 구하면 안내를 접는다.
        """
        비어있음 = {"fit": None, "fit_note": "", "better": []}
        try:
            vram = gpu.total_vram_gb()
            if not vram:
                return 비어있음
            크기 = ollama.model_sizes(ollama.base_from(str(
                self.settings.get("translation", {}).get("url", ""))))
            if not 크기:
                return 비어있음

            def 무게(이름: str) -> float:
                풀 = ollama._풀이름(이름)
                for k, v in 크기.items():
                    if ollama._풀이름(k) == 풀:
                        return v
                return 0.0

            내것 = 무게(고른것)
            if 내것 <= 0:
                return 비어있음  # 아직 안 받은 모델은 크기를 알 수 없다

            됨, 필요 = providers.그래픽카드에_들어가나(내것, providers.MAX_NUM_CTX, vram)
            if 됨:
                return {"fit": True,
                        "fit_note": f"{고른것} 은 그래픽카드({vram:.0f}GB)에 들어갑니다.",
                        "better": []}

            # 안 들어간다. 가진 것 중 들어가는 것을 큰 것부터 알려 준다
            들어가는것 = sorted(
                (이름 for 이름, GB in 크기.items()
                 if providers.그래픽카드에_들어가나(GB, providers.MAX_NUM_CTX, vram)[0]),
                key=lambda 이름: 크기[이름],
                reverse=True,
            )
            return {
                "fit": False,
                "fit_note": (
                    f"{고른것} 은 {필요:.1f}GB 가 필요한데 그래픽카드는 {vram:.0f}GB 입니다. "
                    "이러면 Ollama 가 말없이 일부를 CPU 로 내려서 몇 배 느려집니다."
                ),
                "better": 들어가는것[:4],
            }
        except Exception as error:
            log.error("그래픽카드 안내 실패", error)
            return 비어있음

    def start_local(self, url: str = "") -> dict[str, Any]:
        """Ollama 를 켠다. 검은 창을 띄우지 않는다."""
        번역 = self.settings.get("translation", {})
        됐나, 한마디 = ollama.start(ollama.base_from(url or str(번역.get("url", ""))))
        log.write("Ollama", "켜기", 됨=됐나, 한마디=한마디)
        self.notice = 한마디
        return {"ok": 됐나, "message": 한마디}

    def pull_model(self, url: str = "", model: str = "") -> dict[str, Any]:
        """모델을 받는다. 따로 도는 일꾼에게 맡기고 바로 돌려준다.

        9GB 를 받는 동안 창이 얼어 있으면 사용자는 죽은 줄 안다.
        """
        번역 = self.settings.get("translation", {})
        이름 = (model or str(번역.get("model", "")) or "qwen2.5:14b").strip()
        base = ollama.base_from(url or str(번역.get("url", "")))

        with self._lock:
            if self._pulling:
                return {"ok": True, "already": True, "message": "이미 받는 중입니다."}
            self._pulling = True
            self._pull = {
                "model": 이름, "ratio": 0.0, "message": "시작합니다",
                "done": False, "ok": True,
            }

        def 받기() -> None:
            """**여기서 터지면 단추가 영영 안 돌아온다.**

            `self._pulling` 이 켜진 채로 남고 `done` 은 끝내 안 서서, 화면은
            받는 중인 막대를 계속 보여 주고 다시 누를 수도 없다. 앱을 껐다
            켜는 것 말고는 길이 없다.

            지금 `ollama.pull` 은 무엇이 터지든 안에서 잡아 두 값으로
            돌려준다. 그래도 감싼다 — **여기 지키개가 있느냐가 「단추가 죽느냐」
            를 가르는데, 그것이 남의 함수 사정에 걸려 있으면 안 된다.**
            """
            def 진행(말: str, 비율: float) -> None:
                self._pull.update({"message": 말, "ratio": 비율})

            try:
                됐나, 한마디 = ollama.pull(이름, base, on_progress=진행)
            except Exception as error:      # noqa: BLE001
                됐나, 한마디 = False, f"받지 못했습니다: {error}"
                log.error("Ollama 모델 받기 실패", error, 모델=이름)
            try:
                self._pull.update({
                    "done": True, "ok": 됐나, "message": 한마디,
                    "ratio": 1.0 if 됐나 else self._pull["ratio"],
                })
                log.write("Ollama", "모델 받기", 모델=이름, 됨=됐나,
                          한마디=str(한마디)[:120])
            finally:
                self._pulling = False

        if not self._run_in_background:
            받기()
        else:
            threading.Thread(target=받기, daemon=True).start()
        return {"ok": True, "message": f"{이름} 을 받기 시작했습니다."}

    def pull_progress(self) -> dict[str, Any]:
        """받는 중인 모델이 어디까지 왔는지."""
        return dict(self._pull)

    def test_provider(self, provider_id: str, url: str = "", model: str = "") -> dict[str, Any]:
        """번역할 곳에 실제로 닿는지 지금 확인한다.

        내 컴퓨터에서 도는 모델은 켜 두지 않으면 아무 일도 안 일어난다. 그것을
        번역을 시작한 뒤에 알면, 왜 안 되는지 모르는 채로 붙여넣기만 하게 된다.
        """
        보낼것 = "1\tこんにちは"
        try:
            provider = providers.create(
                provider_id,
                api_key=settings_store.api_key(self.settings, provider_id),
                model=model,
                url=url,
            )
            답 = provider.translate(
                "다음 한 줄을 한국어로 옮겨라. 번호<탭>문장 형태 그대로 한 줄만 내라.\n"
                + 보낼것
            )
        except providers.ProviderError as error:
            log.write("연결확인", "실패", 공급자=provider_id, 까닭=str(error)[:200])
            return {"ok": False, "message": str(error)}
        except Exception as error:
            log.error("연결 확인 실패", error, 공급자=provider_id)
            return {"ok": False, "message": f"확인하지 못했습니다: {error}"}

        미리 = " ".join(str(답).split())[:60]
        log.write("연결확인", "됨", 공급자=provider_id, 모델=model or "기본")
        return {"ok": True, "message": f"됩니다 — {미리}"}

    def redo_transcribe(self, index: int) -> dict[str, Any]:
        """그 파일을 처음부터 다시 받아쓴다.

        담아 둔 것을 지운다. 받아쓰기 설정을 바꿨을 때 쓴다.
        """
        job = self._job(index)
        if job is None:
            return self.state()
        if self.busy and job.stage in (Stage.받아쓰기, Stage.다시훑기):
            self.notice = "지금 받아쓰는 중인 파일입니다."
            return self.state()

        try:
            (job_cache := cache_dir() / f"{cache_key(job.audio)}.json").unlink(missing_ok=True)
            _ = job_cache
        except OSError:
            pass

        # **지우기 전에 찍어 둔다.** 20분 받아쓴 것이 한 번 눌러서 날아간다
        찍은것 = {
            "segments": list(job.segments), "translations": dict(job.translations),
            "report": job.report, "output": job.output, "error": job.error,
            "progress": job.progress, "message": job.message, "stage": job.stage,
        }

        def 되돌리개(job=job, 찍은것=찍은것) -> None:
            job.segments = list(찍은것["segments"])
            job.translations = dict(찍은것["translations"])
            job.report = 찍은것["report"]
            job.output = 찍은것["output"]
            job.error = 찍은것["error"]
            job.progress = 찍은것["progress"]
            job.message = 찍은것["message"]
            job.stage = 찍은것["stage"]
            save_transcript(job)

        self._release_group_of(job)
        job.segments = []
        job.translations = {}
        job.report = None
        job.output = None
        job.error = ""
        job.progress = 0.0
        job.message = ""
        job.stage = Stage.대기
        self._되돌릴것찍기(f"{job.name} 을 다시 받아씁니다", 되돌리개)
        self.notice = f"{job.name} 을 다시 받아씁니다. 시작하기를 눌러 주세요."
        return {**self.state(), "undo": "다시 받아쓰기"}

    def reset_translation(self, index: int) -> dict[str, Any]:
        """그 트랙의 번역만 지우고 다시 할 수 있게 한다.

        그 트랙 줄만 다시 물어본다. 같은 작품의 다른 트랙은 이미 된 채로 남는다.
        """
        job = self._job(index)
        if job is None:
            return self.state()

        찍은것 = {"translations": dict(job.translations), "output": job.output}

        def 되돌리개(job=job, 찍은것=찍은것) -> None:
            job.translations = dict(찍은것["translations"])
            job.output = 찍은것["output"]

        self._되돌릴것찍기(f"{job.name} 의 번역을 지웠습니다", 되돌리개)
        job.translations = {}
        job.output = None
        # **다시 만들겠다고 누른 것이 곧 허락이다.** 여기서 안 적어 두면,
        # 옆에 남아 있는 옛 자막을 보고 「덮어쓸까요?」 를 또 묻는다 —
        # 방금 지우라고 누른 사람에게 묻는 꼴이다
        self._덮어써도됨.add(self._display_key(job))
        if job.transcribed:
            job.stage = Stage.번역
            save_transcript(job)
        self._release_group_of(job)
        self._rebuild_queue()
        self.notice = f"{job.name} 의 번역을 지웠습니다. 다시 번역할 수 있습니다."
        return {**self.state(), "undo": "번역 지우기"}

    def redo_translate(self, key: str) -> dict[str, Any]:
        """그 작품의 번역을 처음부터 다시 한다.

        받아쓴 것은 그대로 두고 번역만 버린다. 트랙 여럿을 한 묶음으로 합쳐서
        번역하므로 작품 단위로만 다시 한다. 트랙 하나만 되돌리면 번호가 어긋난다.
        """
        대상 = [j for j in self.jobs if self._display_key(j) == key]
        if not 대상:
            return self.state()

        with self._lock:
            self.groups = [
                g for g in self.groups if not any(j in 대상 for j in g.jobs)
            ]
        # 다시 하겠다고 누른 것이 곧 허락이다. `reset_translation` 과 같다
        self._덮어써도됨.add(str(key))

        # **작품 하나의 번역이 통째로 날아간다.** 작품 머리의 늘 보이는 자리에
        # 있고, 바로 옆 「작품 빼기」 는 묻는데 이것은 안 물었다. 찍어 둔다
        찍은것 = [(j, dict(j.translations), j.output, j.stage) for j in 대상]

        def 되돌리개(찍은것=찍은것) -> None:
            for job, 번역, 낸것, 단계 in 찍은것:
                job.translations = dict(번역)
                job.output = 낸것
                job.stage = 단계
                if job.transcribed:
                    save_transcript(job)

        for job in 대상:
            job.translations = {}
            job.grouped = False
            job.output = None
            if job.transcribed:
                job.stage = Stage.번역
                save_transcript(job)
        self._되돌릴것찍기("번역을 다시 합니다", 되돌리개)
        self._rebuild_queue()
        self.notice = f"번역을 다시 합니다. ({len(대상)}트랙)"
        return {**self.state(), "undo": "번역 다시"}

    def _release_group_of(self, job: Job) -> None:
        """그 파일이 든 묶음을 대기열에서 떼어 낸다."""
        with self._lock:
            남길것 = []
            for group in self.groups:
                if job in group.jobs:
                    for 딸린것 in group.jobs:
                        딸린것.grouped = False
                    continue
                남길것.append(group)
            self.groups = 남길것
        job.grouped = False

    def transcript(self, index: int) -> dict[str, Any]:
        """받아적은 일본어를 화면에서 바로 볼 수 있게 넘긴다.

        파일로 뱉으면 사용자가 그걸 열어서 봐야 한다. 받아쓰기가 맞는지 눈으로
        훑는 것이 목적이므로 창 안에서 보는 편이 맞다.
        """
        job = self._job(index)
        if job is None or not job.segments:
            return {"ok": False, "message": "아직 받아쓴 것이 없습니다."}

        from app.core import garbage
        from app.core.job import _clock

        def 빠진자리(s: dict[str, Any]) -> bool:
            """이 줄이 '말은 있는데 자막이 없는' 구간에 걸치는가."""
            시작, 끝 = float(s["start"]), float(s["end"])
            return any(a < 끝 and 시작 < b for a, b in job.uncovered)

        return {
            "ok": True,
            "name": job.name,
            # 번역해 뒀으면 한국어 제목도 준다. 자막을 고치는 중에 지금 무엇을
            # 보고 있는지가 일본어 파일 이름으로만 적혀 있으면 못 읽는다
            "ko": titles_store.가져오기(
                self._display_key(job)).get("tracks", {}).get(job.audio.name, ""),
            "done": job.stage == Stage.완료,
            "lines": [
                {
                    "n": s["index"],
                    "at": _clock(s["start"]),
                    # 그 줄만 들려주려면 초 단위 구간이 있어야 한다
                    "at_sec": round(float(s["start"]), 2),
                    "end_sec": round(float(s["end"]), 2),
                    # **들려줄 때는 제 줄 구간에 여유를 붙인다.** 긴 줄을
                    # 나눌 때 시각을 글자 수로 나눠 주는데 그것은 짐작이라,
                    # 짐작한 1~2초만 잘라 들려주면 숨소리만 나오고 끝난다.
                    #
                    # 그렇다고 원래 덩이를 통째로 들려주면 반대로 망한다 —
                    # 한 덩이에서 나뉜 1~5줄이 **전부 같은 소리**를 냈다.
                    # 어느 줄을 눌러도 덩이 첫머리만 들려서, 시각이 틀린 줄
                    # 알게 된다. lrc 는 멀쩡한데 미리듣기만 이상했던 까닭이다.
                    #
                    # 그래서 제 줄 ± 여유 2초, 원래 덩이 밖으로는 안 나간다.
                    # 안 나뉜 보통 줄은 덩이가 곧 제 구간이라 그대로다
                    "play_sec": round(max(
                        float(s.get("heard_start", s["start"])),
                        float(s["start"]) - 2.0), 2),
                    "play_end": round(min(
                        float(s.get("heard_end", s["end"])),
                        float(s["end"]) + 2.0), 2),
                    "ja": s["ja"],
                    "ko": job.translations.get(s["index"], ""),
                    # 142줄에서 이상한 3줄을 눈으로 찾을 수는 없다. 걸러 보게 한다
                    "broken": bool(garbage.why_broken(str(s.get("ja", "")))),
                    "uncovered": 빠진자리(s),
                }
                for s in job.segments
            ],
            # 자막이 아예 없는 구간은 줄로 존재하지 않는다. 따로 넘겨야 보인다
            "gaps": [
                {"at": _clock(a), "at_sec": round(a, 2), "end_sec": round(b, 2)}
                for a, b in job.uncovered
            ],
        }

    def play_clip(self, index: int, start_sec: float, end_sec: float = 0.0) -> dict[str, Any]:
        """그 자리 소리만 잘라서 화면에 넘긴다.

        자막이 맞는지는 들어 봐야 안다. 글자만 봐서는 받아쓰기가 틀린 것인지
        번역이 어색한 것인지 시각이 밀린 것인지 가릴 수 없다.

        2시간짜리를 통째로 넘기지 않는다. 그 줄 앞뒤 몇 초만 잘라 보낸다.
        """
        job = self._job(index)
        if job is None:
            return {"ok": False, "message": "그 파일을 찾지 못했습니다."}
        if not job.audio.is_file():
            return {"ok": False, "message": "음원 파일이 그 자리에 없습니다."}

        시작 = max(0.0, float(start_sec or 0.0))
        끝 = float(end_sec or 0.0)
        if 끝 <= 시작:
            끝 = 시작 + 3.0

        try:
            소리, 실제시작 = clip.extract_wav(job.audio, 시작, 끝)
        except clip.ClipUnavailable as error:
            return {"ok": False, "message": str(error)}
        except Exception as error:
            log.error("소리 잘라내기 실패", error, 파일=job.audio.name)
            return {"ok": False, "message": f"소리를 꺼내지 못했습니다: {error}"}

        담긴것 = base64.b64encode(소리).decode("ascii")
        return {
            "ok": True,
            "audio": f"data:audio/wav;base64,{담긴것}",
            "start": 실제시작,
            # 요청한 자리가 잘라 낸 것 안에서 몇 초째인지. 화면이 그 자리에
            # 표시를 놓을 수 있다
            "offset": round(max(0.0, 시작 - 실제시작), 3),
            # **얼마나 보냈는지 말해 준다.** 「소리가 짧게 들리고 끝난다」 가
            # 나왔을 때, 앱이 짧게 보낸 것인지 보낸 것은 긴데 브라우저가
            # 중간에 끊은 것인지 가릴 방법이 없었다. 둘은 고칠 곳이 아주
            # 다르다. 화면이 이 값과 제가 받은 길이를 나란히 적는다
            "seconds": clip.wav_seconds(소리),
            "bytes": len(소리),
            "asked": round(max(0.0, 끝 - 시작), 3),
        }

    def transcript_text(self, index: int) -> dict[str, Any]:
        """이 트랙을 **통째로** 고치기 위한 두 덩이.

        줄마다 한 칸씩 고치는 길은 이미 있다. 그런데 번역이 통째로 어색하면
        한 줄씩 고치는 것이 벌이다. 번역 화면과 **같은 형식**으로 왼쪽에
        원문, 오른쪽에 지금 번역을 통째로 놓고 고치게 한다.
        """
        job = self._job(index)
        if job is None or not job.segments:
            return {"ok": False, "message": "아직 받아쓴 것이 없습니다."}
        return {
            "ok": True,
            "name": job.name,
            "lines": len(job.segments),
            "ja": "\n".join(f"{s['index']}\t{s['ja']}" for s in job.segments),
            # **이미 채워 놓는다.** 번역해 둔 것을 고치러 온 것이지 처음부터
            # 다시 하러 온 것이 아니다
            "ko": "\n".join(
                f"{s['index']}\t{job.translations.get(s['index'], '')}"
                for s in job.segments
            ),
        }

    def submit_transcript(self, index: int, pasted: str) -> dict[str, Any]:
        """이 트랙의 번역을 통째로 받는다.

        **번역 화면과 같은 검사를 태운다.** 손으로 한 줄씩 고치는 것과 달리
        여기는 밖에서 통째로 가져온 것이 들어올 수 있다. 일본어가 그대로
        오거나 거절문이 오면 담으면 안 된다.
        """
        job = self._job(index)
        if job is None or not job.segments:
            return {"ok": False, "message": "아직 받아쓴 것이 없습니다."}

        번호들 = [s["index"] for s in job.segments]
        결과 = exchange.parse_response(pasted or "", 번호들)
        if 결과.refused:
            return {"ok": False, "refused": True,
                    "message": "번역이 아니라 거절문이 온 것 같습니다. 담지 않았습니다."}
        # **줄 수를 안 보고 비율만 본다.** 자막 묶음은 긴 트랙의 일부라
        # 여섯 줄이 넘지만, 트랙 하나를 통째로 넣는 것은 세 줄짜리도 흔하다.
        # 묶음용 기준을 그대로 쓰면 짧은 트랙에서는 한 번도 안 걸린다
        if 결과.not_korean or exchange.한국어가_아닌가(결과.translations.values()):
            return {"ok": False, "not_korean": True,
                    "message": "일본어가 그대로 온 것 같습니다. 담지 않았습니다."}
        if not 결과.translations:
            return {"ok": False,
                    "message": "번호가 붙은 줄을 찾지 못했습니다. 「번호<탭>한국어」 형식이어야 합니다."}

        난것 = self.save_lines(
            index, {str(n): 글 for n, 글 in 결과.translations.items()})
        빠진것 = [n for n in 번호들 if not str(결과.translations.get(n, "")).strip()]
        난것["missing"] = len(빠진것)
        if 난것.get("ok") and 빠진것:
            # **조용히 넘기지 않는다.** 번역기가 줄을 합치면 뒤가 통째로
            # 비는데, 「고쳤습니다」 만 뜨면 다 된 줄 안다
            난것["message"] = (
                f"{난것.get('changed', 0)}줄을 넣었습니다. "
                f"{len(빠진것)}줄은 안 왔습니다 — 그 줄은 자막에서 빠집니다."
            )
        return 난것

    def save_lines(self, index: int, 고친것: dict[str, str]) -> dict[str, Any]:
        """손으로 고친 번역을 넣고 자막을 다시 만든다.

        AI 가 어색하게 옮긴 줄이나 받아쓰기가 틀린 줄을 직접 고칠 수 있어야 한다.
        메모장으로 .lrc 를 열어 고치면 시각을 손대다 망가뜨리기 쉽다.
        """
        job = self._job(index)
        if job is None or not job.segments:
            return {"ok": False, "message": "고칠 것이 없습니다."}

        바뀐것 = 0
        for 번호, 글 in (고친것 or {}).items():
            try:
                n = int(번호)
            except (TypeError, ValueError):
                continue
            글 = str(글).strip()
            if job.translations.get(n, "") == 글:
                continue
            if 글:
                job.translations[n] = 글
            else:
                job.translations.pop(n, None)
            바뀐것 += 1

        if not 바뀐것:
            return {"ok": True, "message": "바뀐 것이 없습니다.", "changed": 0}

        save_transcript(job)
        self._release_group_of(job)
        pipeline = self._make_pipeline(self.settings)
        if any(job.translations.get(s["index"], "").strip() for s in job.segments):
            try:
                pipeline.finish(job)
            except Exception as error:
                # 여기서 터지면 창구가 통째로 터진다. 고친 것은 이미 담아 뒀으니
                # 자막만 못 만든 것으로 하고 왜 안 됐는지 알려 준다
                log.error("고친 뒤 자막 만들기 실패", error, 파일=job.audio.name)
                self._rebuild_queue()
                return {
                    "ok": False,
                    "changed": 바뀐것,
                    "message": f"{바뀐것}줄은 저장했지만 자막을 못 만들었습니다: {error}",
                }
        self._rebuild_queue()

        self.notice = f"{바뀐것}줄을 고쳤습니다."
        return {
            "ok": True,
            "changed": 바뀐것,
            "output": str(job.output) if job.output else "",
        }

    def save_ja_lines(self, index: int, 고친것: dict[str, str]) -> dict[str, Any]:
        """손으로 고친 **일본어**를 넣는다.

        받아쓰기가 틀린 줄(발음이 비슷한 다른 말)은 한국어만 고쳐서는 반쪽이다
        — 다음에 그 줄을 다시 번역하면 틀린 일본어로 또 번역한다. 검사표가
        「이상한 줄」 이라고 짚어만 주고 고칠 손이 없었다.

        번역은 안 건드린다. 일본어를 고쳤다고 한국어를 지우면, 이미 잘 옮겨
        둔 줄까지 다시 번역하게 된다 — 지울지는 사람이 정한다.
        """
        job = self._job(index)
        if job is None or not job.segments:
            return {"ok": False, "message": "고칠 것이 없습니다."}

        줄찾기 = {int(s["index"]): s for s in job.segments}
        바뀐것 = 0
        for 번호, 글 in (고친것 or {}).items():
            try:
                n = int(번호)
            except (TypeError, ValueError):
                continue
            줄 = 줄찾기.get(n)
            글 = str(글).strip()
            # 빈 글로는 못 고친다 — 줄을 없애고 싶으면 지우기가 따로 있다
            if 줄 is None or not 글 or str(줄.get("ja", "")) == 글:
                continue
            줄["ja"] = 글
            바뀐것 += 1

        if not 바뀐것:
            return {"ok": True, "message": "바뀐 것이 없습니다.", "changed": 0}

        save_transcript(job)
        self.notice = f"일본어 {바뀐것}줄을 고쳤습니다."
        return {"ok": True, "changed": 바뀐것}

    def delete_line(self, index: int, n: int) -> dict[str, Any]:
        """받아쓴 줄 하나를 지운다. 같은 말이 두 번 잡혔을 때 쓴다.

        **번호를 다시 매기지 않는다.** 번역이 줄 번호로 붙어 있어서, 당기면
        뒤 줄 전부가 엉뚱한 번역을 물게 된다. 빈 번호는 그냥 빈 채로 둔다.
        """
        job = self._job(index)
        if job is None or not job.segments:
            return {"ok": False, "message": "지울 것이 없습니다."}
        try:
            n = int(n)
        except (TypeError, ValueError):
            return {"ok": False, "message": "번호가 이상합니다."}

        남는것 = [s for s in job.segments if int(s["index"]) != n]
        if len(남는것) == len(job.segments):
            return {"ok": False, "message": "그 번호의 줄이 없습니다."}
        찍은줄 = next(s for s in job.segments if int(s["index"]) == n)
        찍은번역 = job.translations.get(n)
        찍은차례 = list(job.segments)

        def 되돌리개(job=job, 줄들=찍은차례, n=n, 번역=찍은번역) -> None:
            job.segments = list(줄들)
            if 번역 is not None:
                job.translations[n] = 번역
            save_transcript(job)

        job.segments = 남는것
        job.translations.pop(n, None)
        self._되돌릴것찍기(f"{n}번 줄을 지웠습니다", 되돌리개)
        _ = 찍은줄

        save_transcript(job)
        self._release_group_of(job)
        # 자막에서도 그 줄이 빠져야 한다. 번역이 하나라도 있으면 다시 쓴다
        if any(job.translations.get(s["index"], "").strip() for s in job.segments):
            try:
                self._make_pipeline(self.settings).finish(job)
            except Exception as error:
                log.error("줄 지운 뒤 자막 만들기 실패", error, 파일=job.audio.name)
                self._rebuild_queue()
                return {"ok": False,
                        "message": f"줄은 지웠지만 자막을 못 만들었습니다: {error}"}
        self._rebuild_queue()
        self.notice = f"{n}번 줄을 지웠습니다."
        return {"ok": True, "deleted": n, "undo": "줄 지우기"}

    def save_japanese(self, index: int) -> dict[str, Any]:
        """받아적은 일본어를 시각과 함께 파일로 낸다.

        받아쓰기가 정확한지는 사람이 들어 보는 수밖에 없다. 시각이 붙어 있으면
        음원을 그 지점으로 돌려 놓고 맞는지 볼 수 있다.
        """
        job = self._job(index)
        if job is None or not job.segments:
            return {"ok": False, "message": "아직 받아쓴 것이 없습니다."}

        try:
            path = self._make_pipeline(self.settings).save_japanese(job)
        except OSError as error:
            return {"ok": False, "message": f"저장하지 못했습니다: {error}"}

        if path is None:
            return {"ok": False, "message": "아직 받아쓴 것이 없습니다."}
        self.notice = f"받아쓴 일본어를 저장했습니다: {path}"
        return {"ok": True, "path": str(path)}

    def reset_all(self, confirm: bool = False, keep_settings: bool = True) -> dict[str, Any]:
        """담아 둔 것을 지운다.

        받아쓴 결과를 담아 두기 때문에 같은 파일을 다시 넣으면 건너뛴다. 그것이
        "예전 것을 이어서 하는 것처럼" 보인다. 여기서 지우면 처음부터 다시 한다.

        설정과 키는 기본으로 남긴다. 키를 다시 받아 오게 만드는 것은 지나치다.
        """
        if self.busy:
            self.notice = "돌아가는 중에는 초기화할 수 없습니다. 먼저 멈춰 주세요."
            return self.state()
        if not confirm:
            self.notice = "정말 초기화할까요? 한 번 더 누르면 지웁니다."
            return {**self.state(), "confirm": True}

        지운것 = []
        뿌리 = settings_store.config_dir()
        for 이름 in ("transcripts", "works", "logs"):
            지운것.append(f"{이름} {_지우기(뿌리 / 이름)}개")
        if not keep_settings:
            for 파일 in (settings_store.settings_path(), 뿌리 / "진단.txt"):
                try:
                    파일.unlink(missing_ok=True)
                except OSError:
                    pass
            self.settings = settings_store.load()
            지운것.append("설정과 키")

        with self._lock:
            self.jobs = []
            self.groups = []
            self.works = {}

        log.write("초기화", "담아 둔 것을 지움", 내용=", ".join(지운것))
        self.notice = "초기화했습니다: " + ", ".join(지운것)
        return self.state()

    def export_diagnostics(self) -> dict[str, Any]:
        """무슨 일이 있었는지 한 파일로 묶어 낸다.

        만드는 쪽에는 GPU 도 음원도 없어서, 이것이 없으면 "이상하다"는 말을
        들었을 때 추측밖에 할 것이 없다. API 키는 넣지 않는다.
        """
        from app import __version__

        줄 = [
            f"trans-text 진단  판 {__version__}",
            f"만든 때 {log._now()}",
            # 재현하려면 어느 윈도우·어느 파이썬인지가 먼저다. 이게 없어서
            # 「저는 되는데요」 로 한 바퀴 돌았다
            f"운영체제 {platform.platform()} · 파이썬 {sys.version.split()[0]} · {platform.machine()}",
            "",
            "── 설정 ──",
        ]
        보일설정 = settings_store.for_display(self.settings)
        for 갈래, 값 in 보일설정.items():
            줄.append(f"{갈래}: {값}")

        줄 += ["", "── 그래픽카드 ──"]
        for 이름, 값 in log.gpu_state().items():
            줄.append(f"{이름}: {값}")

        줄 += ["", "── 파일 ──"]
        for job in self.jobs:
            보고 = job.report
            줄.append(
                f"{job.name} | {job.stage.value} | {len(job.segments)}줄 | "
                f"{job.duration_sec:.0f}초 | "
                f"잡힌비율 {보고.coverage:.0%}" if 보고 else
                f"{job.name} | {job.stage.value} | {len(job.segments)}줄"
            )
            if job.error:
                줄.append(f"    오류: {job.error}")
            if 보고:
                for 짚은것 in 보고.findings:
                    줄.append(f"    {짚은것.at} {짚은것.kind}: {짚은것.message}")

        줄 += ["", "── 기록 ──"] + log.read_tail()

        # **다 만든 뒤에 한 번 더 훑는다.** 위에서 설정 칸의 키는 가렸지만,
        # 기록(로그)은 통째로 실어 나른다 — 밖의 AI 가 401 을 주면서 보낸 키를
        # 되비추면 그것이 기록에 남고 그대로 나간다. 화면은 「API 키는 들어가지
        # 않습니다」 라고 적혀 있고, 사용자는 그 말을 믿고 파일을 보낸다
        글 = settings_store.비밀지우기("\n".join(줄) + "\n", self.settings)

        target = settings_store.config_dir() / "진단.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(글, encoding="utf-8")
        except OSError as error:
            return {"ok": False, "message": f"저장하지 못했습니다: {error}"}

        self.notice = f"진단 파일을 만들었습니다: {target}"
        return {"ok": True, "path": str(target)}

    # 붙여넣을 글의 길이 한도.
    #
    # **파일로 주면 사용자가 그것을 찾아 첨부해야 한다.** 그게 성가시다는
    # 말을 들었다 — 「로그 다운받고 지랄」. 채팅에 그대로 붙일 수 있어야
    # 알려 준다. 그런데 기록이 몇천 줄이면 붙여넣기가 또 성가시다
    알릴글최대 = 12000

    def problem_report(self) -> dict[str, Any]:
        """**한 번 눌러서 복사하고 그대로 붙여넣을 것.**

        `export_diagnostics` 와 같은 내용인데 파일 대신 글로 준다. 파일은
        사용자가 폴더를 찾아 들어가 첨부해야 해서, 「이상하다」 고 말하려던
        사람이 거기서 그만둔다.

        길면 **뒤를 자른다.** 앞쪽(판·설정·그래픽카드·파일)이 문제를 가리는
        데 더 쓸모 있고, 기록은 뒤로 갈수록 오래된 것이다.
        """
        from app import __version__

        난것 = self.export_diagnostics()
        글 = ""
        길 = 난것.get("path")
        if 길:
            try:
                글 = Path(길).read_text(encoding="utf-8")
            except OSError:
                글 = ""

        if not 글:
            # 파일을 못 썼어도 알릴 수는 있어야 한다. 못 쓰는 그 사실이
            # 오히려 알려야 할 문제일 수도 있다
            글 = (f"trans-text {__version__}\n"
                  f"(진단을 만들지 못했습니다: {난것.get('message', '')})\n")

        잘렸나 = len(글) > self.알릴글최대
        if 잘렸나:
            글 = 글[: self.알릴글최대] + "\n…(뒤를 잘랐습니다)\n"

        self.notice = "복사했습니다. 채팅이나 이슈에 그대로 붙여넣으세요."
        return {"ok": True, "text": 글, "clipped": 잘렸나,
                "message": self.notice}

    # ---- 판올림 · 껐다 켜면서 하는 일 ----
    #
    # 배치 파일을 찾아 띄우는 것이 귀찮다는 말이 여러 번 나왔다. 하는 일은
    # 전부 이 앱과 **같은 파이썬**이 하는 것이라 안에 넣지 못할 이유가 없다.
    #
    # 다만 업데이트와 그래픽카드 고치기는 **지금 돌고 있는 파일을 건드린다.**
    # 그래서 앱 안에서 하지 않고, 배치를 띄우고 앱은 죽는다
    # ([relaunch.py](../core/relaunch.py)).

    def checkup(self) -> dict[str, Any]:
        """설정 화면이 쓰는 것들. 재 보고 그대로 돌려준다."""
        뿌리 = relaunch.뿌리()
        일들 = []
        for 것 in relaunch.할일들.values():
            막힘 = relaunch.할수있나(것, 뿌리)
            일들.append({"id": 것.id, "can": not 막힘, "why": 막힘})
        기록 = self._껐다켠기록()
        return {
            "version": self._버전(),
            "jobs": 일들,
            "gpu": log.gpu_state(),
            "log": str(relaunch.적은것()),
            # 됐는지 안 됐는지. 표시가 없으면 아무 일도 안 일어난 것처럼 보인다
            "last": relaunch.마지막결과(기록),
        }

    def _껐다켠기록(self, 줄수: int = 40) -> str:
        """껐다 켜는 동안 배치가 적어 둔 것. 뒤에서부터 몇 줄만."""
        자리 = relaunch.적은것()
        try:
            if not 자리.is_file():
                return ""
            글 = 자리.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(글.splitlines()[-줄수:])

    def _버전(self) -> str:
        """지금 어느 판인지. git 이 없으면 모른다고 한다.

        「모르면 안 보여 줍니다」 — 못 재는 것을 0 으로 띄우지 않는다.
        """
        try:
            난것 = subprocess.run(
                ["git", "log", "-1", "--format=%h · %cd", "--date=format:%Y-%m-%d"],
                cwd=str(relaunch.뿌리()), capture_output=True, text=True, timeout=5,
            )
            return 난것.stdout.strip() if 난것.returncode == 0 else ""
        except Exception:
            return ""

    def update_now(self) -> dict[str, Any]:
        """**앱 안에서 판올림한다.** 무엇이 됐는지 화면에 그대로 돌려준다.

        여태는 배치가 받았다. 앱이 꺼진 뒤에 벌어지는 일이라 화면에 띄울 데가
        없었고, 배치가 뜨다 말면 사용자가 보는 것은 「앱만 꺼짐」 이 전부였다.
        판이 그대로인 까닭은 알 길이 없다 — **실제로 그렇게 됐다.**

        여기서는 창을 안 닫는다. 받은 뒤에 무엇이 됐는지 읽을 시간을 준다.
        다시 켜는 것은 사용자가 그다음에 누른다.
        """
        도는중 = self._도는중()
        if 도는중:
            return {"ok": False, "message": f"{도는중} 끝나면 하세요."}

        난것 = relaunch.판올림하기()
        log.write("판올림", "앱 안에서 판올림함",
                  됨=난것.get("ok"), 바뀜=난것.get("바뀜"), 말=난것.get("말"))
        if 난것.get("ok"):
            # 새로 받았으면 판 수를 다시 센다. 안 그러면 「새 판 있음」 딱지가
            # 남아서 방금 받은 사람이 또 누른다
            self._새판수 = 0
        return {
            "ok": bool(난것.get("ok")),
            "changed": bool(난것.get("바뀜")),
            "message": 난것.get("말") or "",
            # git 이 한 말을 **그대로** 보여 준다. 「손댄 파일이 있어서 못
            # 받았다」 같은 것은 이 글에만 나온다
            "detail": (난것.get("글") or "")[-1500:],
            **self.state(),
        }

    def restart_with(self, job_id: str) -> dict[str, Any]:
        """할 일을 배치에 맡기고 앱을 닫는다.

        **닫는 것이 곧 시작 신호다.** 배치는 이 프로세스가 사라질 때까지
        기다렸다가 일을 한다. 그래서 띄우기가 성공했더라도 창을 못 닫으면
        아무 일도 안 일어난다 — 그때는 배치가 스스로 물러난다.
        """
        무엇 = relaunch.할일들.get(str(job_id or ""))
        if 무엇 is None:
            return {"ok": False, "message": "그런 것이 없습니다."}

        # **도는 중이면 안 한다.** 3시간짜리를 20분 받아쓰던 중에 앱이 꺼지면
        # 그 20분이 통째로 날아간다. 담아 두는 것은 트랙을 다 받아쓴 뒤라서,
        # 도중에 죽으면 남는 것이 없다
        도는중 = self._도는중()
        if 도는중:
            return {"ok": False, "message": f"{도는중} 끝나면 하세요."}

        if self.close_window is None:
            # 창이 없으면 띄워 봐야 배치가 영영 기다린다. 아예 시작하지 않는다
            return {"ok": False, "message": "창을 닫을 수 없어서 하지 않았습니다."}

        try:
            배치 = relaunch.띄우기(무엇)
        except Exception as error:
            log.error("껐다 켜기 배치를 띄우지 못했다", error)
            return {"ok": False, "message": f"시작하지 못했습니다. ({error})"}

        log.write("판올림", "껐다 켜면서 할 일을 맡겼다", 무엇=무엇.id, 배치=str(배치))
        try:
            self.close_window()
        except Exception as error:
            # 배치는 이미 떠 있지만 60번 세다 스스로 물러난다. 아무 일도
            # 안 일어나므로 **그렇다고 말한다.** 「닫는 중」 으로 굳어 있으면
            # 사용자는 기다리기만 한다
            log.error("창을 닫지 못했다", error)
            return {"ok": False,
                    "message": f"창을 닫지 못해서 아무것도 하지 않았습니다. ({error})"}
        return {"ok": True, "message": "앱을 닫습니다. 끝나면 다시 켜집니다."}

    def _도는중(self) -> str:
        """지금 오래 걸리는 일을 하고 있으면 그 이름. 아니면 빈 글."""
        if self.busy:
            return "받아쓰는 중입니다."
        if self._local_run.get("busy"):
            return "내 컴퓨터 AI 가 번역하는 중입니다."
        if self._pulling:
            return "번역 모델을 받는 중입니다."
        return ""

    def open_folder(self, path: str) -> None:
        """자막이 만들어진 폴더를 연다."""
        target = Path(path)
        folder = target if target.is_dir() else target.parent
        if not folder.is_dir():
            return
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            pass  # 폴더를 못 여는 것으로 프로그램이 죽으면 안 된다

    def _job자리(self, job: Job) -> int:
        """이 트랙이 목록에서 몇 번째인가. 없으면 -1.

        화면이 쓰는 `index` 와 같은 값이다(`to_view` 가 위치를 그대로 넣는다).
        """
        with self._lock:
            for 자리, 것 in enumerate(self.jobs):
                if 것 is job:
                    return 자리
        return -1

    def _끝났다고_알리기(self) -> None:
        """작업 표시줄 깜빡임. 걸려 있지 않으면(시험·창 없음) 아무 일도 없다."""
        if self.flash_window is None:
            return
        try:
            self.flash_window()
        except Exception as error:      # noqa: BLE001
            log.error("끝났다고 알리지 못함", error)

    def _다끝났나_알리며(self, 다끝났나: bool) -> bool:
        if 다끝났나:
            self._끝났다고_알리기()
        return 다끝났나

    def _job(self, index: int) -> Job | None:
        with self._lock:
            if 0 <= index < len(self.jobs):
                return self.jobs[index]
        return None


# ---- 낱말 목록을 글로, 글을 목록으로 ----
#
# **이 앱은 원래 붙여넣기 중심이다.** 낱말도 같은 결로 간다 — 글상자 하나에
# 한 줄씩 적어 두면 추가·삭제·수정·끄기가 한 번에 된다. 통째로 복사해서
# 주고받을 수도 있다.
#
#     ちんぽ → 자지
#     んちゅ →              (오른쪽이 비면 일본어 그대로)
#     # まんこ → 보지        (# 이 붙으면 꺼진 것)
#
# **끈 것을 지우지 않는다.** 헛걸림이 나서 잠깐 뺐을 뿐이라 목록에 남아
# 있어야 다시 켤 수 있다.

_화살표 = "→"
_끔표 = "#"
# 설명 줄. **`#` 은 못 쓴다** — 그것은 「끄기」라서, 설명을 그렇게 적으면
# 그 문장이 통째로 꺼진 낱말이 되어 목록에 쌓인다
_설명표 = "//"


def _한줄읽기(줄: str) -> tuple[bool, str, str] | None:
    """`(켰나, 왼쪽, 오른쪽)`. 빈 줄이거나 설명 줄이면 `None`.

    화살표가 없으면 왼쪽만 있는 것으로 친다 — 위험낱말은 오른쪽이 없다.
    """
    글 = 줄.strip()
    if 글.startswith(_설명표):
        return None
    켰나 = True
    if 글.startswith(_끔표):
        켰나 = False
        글 = 글[len(_끔표):].strip()
    if not 글:
        return None
    # `->` 로 적어도 받는다. 화살표를 키보드로 치기 번거롭다.
    #
    # **다른 화살표(⇒·➡)와 `=` 도 받는다.** 안 받으면 그 줄이 통째로
    # 「ちんぽ ⇒ 자지」 라는 낱말이 되어 아무 데도 안 걸리는데, 사용자는
    # 넣었다고 믿는다 — 넣었는데 안 걸리는 것이 제일 나쁘다. `=` 는 호칭
    # 칸이 그 모양(`お兄さん = 오빠`)이라 헷갈려서 그대로 칠 수 있다.
    # `=>` 가 `=` 보다 먼저다 — 순서를 바꾸면 오른쪽에 `>` 가 남는다
    for 표 in (_화살표, "->", "=>", "⇒", "➡", "⟹", "⇨", "=", "\t"):
        if 표 in 글:
            왼, _, 오른 = 글.partition(표)
            return 켰나, 왼.strip(), 오른.strip()
    return 켰나, 글, ""


def 내낱말글(목록: dict) -> str:
    끈것 = set(목록.get("끈말") or [])
    줄들 = []
    for ja, ko in (목록.get("낱말") or {}).items():
        앞 = "" if ja not in 끈것 else _끔표 + " "
        줄들.append(f"{앞}{ja} {_화살표} {ko}".rstrip())
    return "\n".join(줄들)


def 내낱말읽기(글: str) -> dict:
    """같은 낱말이 여러 줄이면 **마지막 줄이 이긴다** — 켬·끔도, 뜻도.

    예전에는 `#` 줄이 하나라도 있으면 켠 줄이 있어도 꺼졌다. 줄을 복사해
    고치다 옛 줄을 지우는 것을 깜빡하면, 켰다고 믿는 낱말이 꺼져 있었다.
    """
    낱말: dict[str, str] = {}
    끈것: set[str] = set()
    for 줄 in (글 or "").splitlines():
        읽은것 = _한줄읽기(줄)
        if 읽은것 is None:
            continue
        켰나, ja, ko = 읽은것
        if not ja:
            continue
        낱말[ja] = ko
        if 켰나:
            끈것.discard(ja)
        else:
            끈것.add(ja)
    return {"낱말": 낱말, "끈말": [ja for ja in 낱말 if ja in 끈것]}


def 위험낱말글(목록: dict) -> str:
    """강한말과 약한말을 칸을 갈라서 보여 준다.

    **머리말만 있으면 뭘 잡는지 알 수가 없다.** `[나이] 18` 만 보고는 그것이
    한자를 잡는지 히라가나를 잡는지, 18살은 걸리는지 안 걸리는지 모른다.
    그래서 칸마다 무엇을 하는지 적어 준다.
    """
    끈것 = set(목록.get("끈말") or [])
    나이 = 목록.get("나이") or minor_terms.기본나이
    조각 = [
        f"[나이] {나이}",
        f"// 이보다 어리다고 말하면 짚습니다. {나이}살은 안 짚습니다.",
        f"// 숫자로 쓴 것: {나이 - 1}歳 · {나이 - 1}才 · {나이 - 1}さい"
        f" · {나이 - 1}サイ · {나이 - 1}ｻｲ · {나이 - 1}歲",
        f"//   전각으로 써도({minor_terms.전각으로(나이 - 1)}歳), 사이가 떠 있어도"
        f"({나이 - 1} 歳) 잡습니다.",
    ]
    # 나이를 낮춰 잡으면 보기가 0 이나 음수로 내려간다. 있는 것만 쓴다
    한자보기 = [minor_terms.한자로(수) + 꼬리
             for 수, 꼬리 in ((나이 - 1, "歳"), (나이 - 2, "才"), (나이 - 3, "さい"))
             if 수 >= 1]
    if 한자보기:
        조각.append("// 한자로 쓴 것: " + " · ".join(한자보기))
    가나전 = minor_terms.가나로(나이 - 1)
    if 가나전:
        # 나이를 낮춰 잡으면 보기가 모자랄 수 있다. 있는 것만 쓴다
        보기 = [것 for 것 in (가나전, minor_terms.가나로(나이 - 2),
                            minor_terms.가나로(나이 - 11)) if 것]
        조각 += [
            f"// 가나로만 쓴 것: {' · '.join(보기)}"
            " — 1~99 를 다 만들어 뒀습니다.",
            f"//   가타카나로 써도({minor_terms.가타카나로(가나전)}) 잡습니다.",
            "//   にさい·さんさい·ごさい·きゅうさい 는 「〜に最高」 같은 딴 말",
            "//   속에 묻어 나옵니다. 그래서 혼자서는 안 짚고, 딴 것과 겹칠",
            "//   때만 짚습니다.",
        ]
    조각 += [
        f"// 안 잡는 것: {나이}歳 · {minor_terms.한자로(나이)}歳 · {나이 + 2}才"
        + (f" · {minor_terms.가나로(나이)}" if minor_terms.가나로(나이) else "")
        + " · 100歳 (세 자리는 어른으로 봅니다)",
        "// 못 잡는 것: いっさい(1살) 은 「一切」 와 소리가 같아서 뺐습니다.",
        "//   二百歳 처럼 百·千 이 든 한자도 못 잡습니다.",
        "",
        "[강한말] — 하나만 걸려도 ⚠ 가 뜹니다",
        "// 혼자서도 거의 확정인 말. 초등·중등·유아·갈래말이 여기 있습니다.",
        "// 줄 앞에 # 을 붙이면 지우지 않고 끕니다.",
    ]
    for 말 in 목록.get("강한말") or []:
        조각.append(("" if 말 not in 끈것 else _끔표 + " ") + 말)
    조각 += [
        "",
        "[약한말] — 둘 이상 겹쳐야 ⚠ 가 뜹니다",
        "// 혼자서는 아무 뜻이 없는 말. 少年 은 성인한테도 쓰고,",
        "// 教室 하나로는 아무것도 아니지만 放課後 와 겹치면 학교 배경입니다.",
        "// 같은 말이 열 번 나와도 하나로 셉니다.",
    ]
    for 말 in 목록.get("약한말") or []:
        조각.append(("" if 말 not in 끈것 else _끔표 + " ") + 말)
    return "\n".join(조각).rstrip() + "\n"


def 위험낱말읽기(글: str) -> dict:
    """`[강한말]`·`[약한말]`·`[나이]` 로 갈라 읽는다.

    **머리말이 없으면 강한말로 친다.** 사용자가 낱말만 죽 붙여넣었을 때 그것이
    통째로 무시되면 안 된다 — 넣었는데 안 걸리는 것이 제일 나쁘다.
    """
    난것 = {"강한말": [], "약한말": [], "끈말": [], "나이": minor_terms.기본나이}
    지금칸 = "강한말"
    for 줄 in (글 or "").splitlines():
        벗긴것 = 줄.strip()
        if 벗긴것.startswith("[") and "]" in 벗긴것:
            머리 = 벗긴것[1:벗긴것.index("]")].strip()
            뒤 = 벗긴것[벗긴것.index("]") + 1:].strip()
            if 머리 == "나이":
                # **뒤에 설명이 붙어 있어도 읽는다.** 「18 · 이보다 어리면…」
                # 처럼 적어 두면 `isdigit()` 으로는 통째로 놓친다
                숫자 = re.search(r"\d+", 뒤)
                if 숫자:
                    난것["나이"] = int(숫자.group())
                continue
            if 머리 in ("강한말", "약한말"):
                지금칸 = 머리
                continue
        읽은것 = _한줄읽기(줄)
        if 읽은것 is None:
            continue
        켰나, 말, _ = 읽은것
        if not 말:
            continue
        if 말 not in 난것[지금칸]:
            난것[지금칸].append(말)
        # 같은 말이 여러 줄이면 **마지막 줄의 켬·끔이 이긴다.** 내낱말과
        # 같은 규칙이다 — 두 목록이 다르게 굴면 하나를 익힌 것이 소용없다
        if 켰나:
            if 말 in 난것["끈말"]:
                난것["끈말"].remove(말)
        elif 말 not in 난것["끈말"]:
            난것["끈말"].append(말)
    return 난것
