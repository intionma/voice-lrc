"""음원 하나가 자막이 되기까지의 흐름.

    받아쓰기 → 빈 구간 다시 훑기 → 토막 합치기 → 번역 → 자막 저장 → 검사표

받아쓰기 결과는 따로 저장해 둔다. 번역하다 프로그램을 닫아도 다시 받아쓰지
않는다. 2시간짜리를 다시 돌리는 것은 12분이고, 그걸 두 번 겪게 하면 안 된다.

받아쓰기와 번역은 서로 다른 무게라 나눠 두었다. 받아쓰기는 GPU가 혼자 오래 돌고,
번역은 사람이 붙여넣기를 기다린다.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from app.core import (
    coverage,
    dlsite,
    garbage,
    gpu,
    log,
    lrc,
    preprocess,
    preset as presets,
    route,
    quality,
    settings as settings_store,
    transcribe as asr,
)
from app.core.segments import merge_segments


class Stage(str, Enum):
    대기 = "대기"
    받아쓰기 = "받아쓰기"
    다시훑기 = "다시훑기"
    번역 = "번역"
    자막 = "자막"
    완료 = "완료"
    건너뜀 = "건너뜀"   # 받아적을 말이 없었다. 고장이 아니다
    실패 = "실패"


@dataclass
class Job:
    audio: Path
    stage: Stage = Stage.대기
    progress: float = 0.0
    message: str = ""
    duration_sec: float = 0.0
    segments: list[dict[str, Any]] = field(default_factory=list)
    # 번역은 작품 단위로 모아서 하고, 그 결과가 여기로 나뉘어 담긴다
    translations: dict[int, str] = field(default_factory=dict)
    report: quality.Report | None = None
    output: Path | None = None
    error: str = ""
    work: Any = None  # dlsite.Work. 품번으로 가져온 작품 정보
    work_context: str = ""
    # 사용자가 직접 정해 준 품번. 파일 이름에 없거나 조회가 안 될 때 쓴다
    work_id: str = ""
    # 이미 번역 묶음에 들어갔는지. 한 번 들어간 파일은 다시 묶지 않는다
    grouped: bool = False
    # 소리는 잡혔는데 받아쓴 줄이 없는 자리. 찾기만 하고 다시 받아쓰지는 않는다
    uncovered: list[tuple[float, float]] = field(default_factory=list)
    # VAD 를 통과한 길이. 전체와 견주면 얼마나 잘려 나갔는지 보인다
    duration_after_vad: float = 0.0
    # 담아 둔 것을 읽었다면 그것을 **어느 강도로** 받아썼는지.
    # 빈 값은 강도를 적기 전에 담아 둔 옛 파일이다
    cached_preset: str = ""
    # 이 트랙을 가릴 때 쓴 낱말 사전. **내준 표는 바뀌면 안 된다.**
    #
    # 이것이 없으면 앱을 껐다 켤 때마다 지금 목록으로 표를 다시 매긴다. 그
    # 사이에 낱말 하나를 끄면 번호가 밀려서, 아까 복사해 둔 프롬프트로 받은
    # 답의 `KW01` 이 **딴 낱말로 조용히 되돌아온다.** 「젖가슴」 자리에
    # 「자지」 가 들어간 자막이 아무 오류 없이 나간다
    # **이 트랙의 번호표.** 번호 칸을 떼는 열쇠다.
    #
    # 목록에서 몇 번째인지로 칸을 정하면, 트랙을 지우거나 순서를 바꿀 때 칸이
    # 통째로 밀린다. 그러면 브라우저에 남은 옛 답이 옆 트랙으로 들어간다.
    # 그래서 트랙에 붙여 두고 받아쓴 것과 함께 담는다.
    # 이 트랙을 받아쓸 때 그래픽카드를 못 써서 CPU 로 내려갔나.
    # **앱이 아는 것을 사용자가 진단하게 만들지 않으려고** 화면이 이것을 본다
    gpu문제: bool = False
    track_id: int = 0
    # **내준 프롬프트가 무슨 말이었나.** `{번호: 원문}`.
    #
    # 다시 받아쓰면 번호는 그대로인데 그 번호가 가리키는 말이 달라진다.
    # 이것이 없으면 그새 바뀐 줄에 옛 답이 조용히 들어간다
    내준원문: dict[int, str] = field(default_factory=dict)
    가림사전: dict[str, str] = field(default_factory=dict)
    # 그 사전을 이미 담아 뒀나. 묶음을 다시 짤 때마다 파일을 쓰지 않으려고 본다
    사전담김: bool = False
    # 사용자에게 계속 보여야 하는 한마디. `message` 는 단계가 넘어갈 때마다
    # 덮어써지므로, 지워지면 안 되는 것은 여기에 둔다
    hint: str = ""
    # 받아쓰기를 시작한 때(`time.monotonic`). 남은 시간을 재는 데 쓴다.
    # 0 이면 아직 시작하지 않았거나 이미 끝났다
    started_at: float = 0.0
    # 어림한 남은 초. 0 이면 아직 잴 수 없다 — **모르면 아무 말도 하지 않는다.**
    # 근거 없는 「1분 남음」이 20분이 되면 다시는 안 믿게 된다
    eta_sec: float = 0.0
    # 음원 길이 ÷ 훑는 데 걸린 시간. 0 이면 아직 안 재 봤다.
    #
    # 「느리다」 는 말만으로는 CPU 로 떨어진 것인지, 묶음이 안 걸린 것인지,
    # 원래 이만큼인지 가릴 수 없다. 눈금 — CPU 는 0.5배속 아래, 그래픽카드에
    # 하나씩 넣으면 2~5배속, 묶어서 넣으면 10배속 위
    배속: float = 0.0

    @property
    def name(self) -> str:
        return self.audio.name

    @property
    def elapsed_sec(self) -> float:
        """받아쓰기를 시작하고 여기까지 걸린 시간."""
        if not self.started_at:
            return 0.0
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def transcribed(self) -> bool:
        return bool(self.segments)

    @property
    def translated_ratio(self) -> float:
        if not self.segments:
            return 0.0
        찬것 = sum(1 for s in self.segments if self.translations.get(s["index"], "").strip())
        return 찬것 / len(self.segments)

    def to_view(self) -> dict[str, Any]:
        """화면에 그릴 만큼만 추린다."""
        return {
            "name": self.name,
            "path": str(self.audio),
            "stage": self.stage.value,
            "progress": round(self.progress, 4),
            "message": self.message,
            "lines": len(self.segments),
            "duration_sec": round(self.duration_sec, 1),
            "translated": round(self.translated_ratio, 3),
            "output": str(self.output) if self.output else "",
            "error": self.error,
            "hint": self.hint,
            "eta_sec": round(self.eta_sec),
            "elapsed_sec": round(self.elapsed_sec),
            "배속": self.배속,
            "report": _report_view(self.report),
            "work": self.work.to_view() if self.work is not None else None,
        }


# 남은 시간을 어림할 만큼 훑기 전에는 아무 말도 하지 않는다.
# 처음 몇 초는 모델을 올리고 음원을 여는 시간이 섞여 있어서, 그것으로 재면
# 20분짜리를 「3시간 남음」이라고 말한다. 한 번 그러면 다시는 안 믿는다
ETA_MIN_RATIO = 0.03
ETA_MIN_SEC = 8.0


def _남은시간(지난: float, 진행: float, 뒷일_몫: float) -> float:
    """재어 본 속도로 남은 시간을 어림한다. 못 재겠으면 0.

    강도마다 「2시간에 22분」 같은 어림값이 적혀 있지만 그것을 쓰지 않는다.
    그 값은 만드는 쪽에서 재 본 적이 없는 숫자다. 그래픽카드가 무엇인지,
    지금 다른 것이 돌고 있는지에 따라 몇 배씩 달라진다.

    **여기서는 그 PC 에서 실제로 나온 속도만 쓴다.** 1차 훑기를 이만큼 하는 데
    이만큼 걸렸으면, 남은 것도 그 속도로 걸린다고 본다.

    `뒷일_몫` 은 1차 훑기 뒤에 남은 일의 비율이다. 「극한」은 여기서부터 다른
    모델로 한 번을 더 훑는다. 그것을 안 세면 「곧 끝납니다」라고 해 놓고 그만큼
    더 기다리게 만든다. 그것은 아예 안 알려 주는 것보다 나쁘다.
    """
    if 진행 <= ETA_MIN_RATIO or 지난 < ETA_MIN_SEC:
        return 0.0
    이번훑기 = 지난 / 진행          # 1차 훑기를 끝까지 하는 데 걸릴 시간
    남은 = 이번훑기 - 지난 + 이번훑기 * 뒷일_몫
    return max(0.0, 남은)


def 강도가_다르다는_말(옛강도, 새강도) -> str:
    """담아 둔 것과 고른 강도가 다른데 붙여넣은 번역이 있을 때 하는 말.

    **한 곳에만 적어 둔다.** 이 말을 띄우는 자리가 둘이다 — 받아쓰기가 도는
    길과, 앱을 켜면서 목록을 되살리는 길. 두 곳에 따로 적어 두면 한쪽만
    고쳐져서 같은 상황에 다른 말이 나온다.
    """
    return (
        f"「{옛강도.name}」로 받아쓴 것입니다. "
        f"「{새강도.name}」로 다시 받으려면 '다시 받아쓰기' 를 누르세요 "
        "(붙여넣은 번역은 사라집니다)"
    )


def _replace_spans(
    raw: list[dict[str, Any]],
    구간: list[tuple[float, float]],
    새것: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """망가진 구간의 줄을 새로 받은 것으로 갈아 끼운다.

    원본을 건드리지 않는다. 새로 받은 것이 더 나쁘면 원본을 그대로 쓰는데,
    번호를 원본 줄에 직접 덮어썼더니 그때 번호가 뒤죽박죽인 채로 남았다.
    """

    def 구간안(줄: dict[str, Any]) -> bool:
        가운데 = (float(줄["start"]) + float(줄["end"])) / 2
        return any(시작 <= 가운데 <= 끝 for 시작, 끝 in 구간)

    남길것 = [dict(줄) for 줄 in raw if not 구간안(줄)]
    합침 = 남길것 + [dict(줄) for 줄 in 새것 if 구간안(줄)]
    합침.sort(key=lambda 줄: float(줄["start"]))
    for 자리, 줄 in enumerate(합침, start=1):
        줄["index"] = 자리
    return 합침


def _clock(seconds: float) -> str:
    """`01:23.4`. 플레이어에서 그 자리를 찾을 수 있을 만큼만."""
    total = max(0.0, float(seconds))
    minutes, rest = divmod(total, 60)
    return f"{int(minutes):02d}:{rest:04.1f}"


def _report_view(report: quality.Report | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "summary": report.summary(),
        "ok": report.ok,
        "coverage": round(report.coverage, 3),
        # kind 를 빠뜨리면 화면이 미성년 경고를 다른 것과 구분하지 못한다
        "findings": [
            {
                "kind": f.kind,
                "at": f.at,
                # 초 단위도 함께 준다. 화면이 그 자리로 가서 들려주려면 필요하다
                "at_sec": round(f.at_sec, 2),
                "message": f.message,
                "severity": f.severity,
            }
            for f in report.findings
        ],
    }


# ---- 받아쓴 것을 담아 두기 ----


def cache_dir() -> Path:
    return settings_store.config_dir() / "transcripts"


def cache_key(audio: Path) -> str:
    """같은 파일인지 알아보는 열쇠.

    경로와 크기와 고친 시각을 함께 본다. 파일을 바꿔치기했으면 다시 받아써야 한다.
    """
    try:
        stat = audio.stat()
        seed = f"{audio.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        seed = str(audio)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def save_transcript(job: Job) -> Path:
    path = cache_dir() / f"{cache_key(job.audio)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_file": job.audio.name,
        "source_path": str(job.audio),
        "duration_sec": job.duration_sec,
        "language": "ja",
        # 어느 강도로 받아쓴 것인지 적어 둔다. 이것이 없으면 「빠르게」로 받아쓴
        # 것을 나중에 「속삭임」을 골라도 그대로 다시 써 버린다
        "preset": job.cached_preset,
        "segments": job.segments,
        # 붙여넣은 번역도 함께 담는다. 창을 닫으면 날아가던 것이다.
        # 긴 트랙을 네댓 번 나눠 붙여넣다 닫으면 그때까지 한 것이 통째로 없어졌다
        "translations": {str(k): v for k, v in job.translations.items()},
        # **우리가 만든 자막이 어디 있는지.** 이것이 없으면 앱을 껐다 켠 뒤에
        # 제가 쓴 자막도 남의 것으로 보여서, 이어서 넣을 때마다
        # 「덮어쓸까요?」 를 묻게 된다
        "output": str(job.output) if job.output else "",
        # 이 트랙을 가릴 때 쓴 사전. **내준 표가 바뀌면 안 된다** — 없으면
        # 다시 켤 때 지금 목록으로 번호를 다시 매겨서, 아까 복사해 둔
        # 프롬프트로 받은 답이 딴 낱말로 조용히 되돌아온다
        "track_id": job.track_id,
        "내준원문": {str(k): v for k, v in job.내준원문.items()},
        "가림사전": job.가림사전,
    }
    try:
        # 쓰다 만 파일이 남으면 다음에 켤 때 그것을 읽고 터진다.
        # 임시 이름으로 다 쓰고 한 번에 바꿔 놓는다
        임시 = path.with_suffix(".json.tmp")
        임시.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        임시.replace(path)
    except OSError as error:
        # 디스크가 꽉 찼을 수 있다. 담아 두지 못해도 이번 작업은 이어져야 한다
        log.error("받아쓴 것을 담아 두지 못함", error, 파일=job.audio.name)
    return path


def load_transcript(job: Job) -> bool:
    """담아 둔 받아쓰기가 있으면 되살린다."""
    path = cache_dir() / f"{cache_key(job.audio)}.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    segments = data.get("segments") or []
    if not segments:
        return False
    job.segments = segments
    job.duration_sec = float(data.get("duration_sec") or 0.0)
    job.cached_preset = str(data.get("preset") or "")

    담아둔길 = str(data.get("output") or "")
    if 담아둔길:
        job.output = Path(담아둔길)

    담아둔번역 = data.get("translations") or {}
    if isinstance(담아둔번역, dict):
        for 번호, 글 in 담아둔번역.items():
            try:
                job.translations[int(번호)] = str(글)
            except (TypeError, ValueError):
                continue

    담긴것 = data.get("내준원문")
    if isinstance(담긴것, dict):
        job.내준원문 = {int(k): str(v) for k, v in 담긴것.items() if str(k).isdigit()}
    담긴번호표 = data.get("track_id")
    if isinstance(담긴번호표, int) and 담긴번호표 > 0:
        job.track_id = 담긴번호표
    담아둔사전 = data.get("가림사전")
    if isinstance(담아둔사전, dict) and 담아둔사전:
        job.가림사전 = {str(ja): str(ko) for ja, ko in 담아둔사전.items()}
        job.사전담김 = True
    return True


def _받은크기(모델이름: str) -> int:
    """이 모델을 받아 둔 폴더가 지금 몇 바이트인가. 못 찾으면 0.

    HuggingFace 는 `models--조직--이름` 꼴로 담아 둔다. `large-v3` 처럼 짧은
    이름은 faster-whisper 가 제 저장소로 바꿔 주므로, 마지막 토막이 들어간
    폴더를 찾는다.
    """
    꼬리 = 모델이름.replace("\\", "/").split("/")[-1].lower()
    if not 꼬리:
        return 0
    자리 = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.environ.get("HF_HUB_CACHE")
    뿌리 = Path(자리) if 자리 else settings_store.models_dir() / "hub"
    try:
        후보 = [p for p in 뿌리.glob("models--*") if 꼬리 in p.name.lower()]
    except OSError:
        return 0
    합 = 0
    for 폴더 in 후보:
        try:
            for 파일 in 폴더.rglob("*"):
                try:
                    if 파일.is_file():
                        합 += 파일.stat().st_size
                except OSError:
                    continue
        except OSError:
            continue
    return 합


def _한번에쓰기(target: Path, 글: str) -> None:
    """**쓰다 죽어도 반쪽이 남지 않게.** 옆에 임시로 쓰고 한 번에 바꾼다.

    자막(`lrc.write`)은 이미 이렇게 했는데 `.ja.txt` 는 바로 썼다. 정전이나
    강제 종료가 쓰는 도중에 오면 음원 옆에 반만 적힌 파일이 남고, 다음에 켜면
    그것이 진짜인 줄 안다.
    """
    임시 = target.with_name(target.name + ".tmp")
    임시.write_text(글, encoding="utf-8")
    임시.replace(target)


def _대체이름(job: Job, 파일이름: str) -> str:
    """대체 폴더에 놓을 때 쓸 이름.

    음원 옆에 못 쓰면 한곳(바탕화면 등)에 모아 놓는데, **파일 이름만 쓰면
    작품끼리 부딪친다.** 동인음성은 트랙 이름이 `01.wav`, `Tr01.wav` 처럼
    비슷비슷해서 두 작품을 넣으면 뒤엣것이 앞엣것을 덮어썼다. 12분씩 받아쓰고
    번역까지 한 자막이 조용히 사라진다.

    앞에 어느 작품인지 붙인다. 품번을 알면 품번, 모르면 폴더 이름이다.
    """
    앞 = (job.work_id or "").strip() or dlsite.extract_work_id(job.audio)
    if not 앞:
        앞 = job.audio.parent.name
    앞 = "".join(글 for 글 in 앞 if 글 not in '\\/:*?"<>|').strip()
    return f"{앞}_{파일이름}" if 앞 else 파일이름


# ---- 흐름 ----

Emit = Callable[[Job], None]


@dataclass
class Pipeline:
    """받아쓰기부터 자막까지를 순서대로 돈다.

    바깥에서 부품을 갈아 끼울 수 있게 해 두었다. 시험에서는 가짜 모델을 넣는다.
    """

    settings: dict[str, Any] = field(default_factory=settings_store.load)
    on_event: Emit | None = None
    load_model: Callable[[asr.TranscribeOptions], Any] = asr.load_model
    transcribe_fn: Callable[..., asr.Transcript] = asr.transcribe
    rescan_fn: Callable[..., list[dict[str, Any]]] = asr.rescan_gaps
    detect_speech_fn: Callable[..., list[tuple[float, float]]] = coverage.detect_speech
    fetch_work: Callable[..., Any] = dlsite.fetch
    _model: Any = None
    _model_options: asr.TranscribeOptions | None = None

    # ---- 설정 ----

    def preset(self) -> presets.Preset:
        """지금 고른 받아쓰기 강도."""
        return presets.get(self.settings.get("asr", {}).get("preset", presets.DEFAULT))

    def options(self, model: str = "") -> asr.TranscribeOptions:
        asr_settings = self.settings.get("asr", {})
        강도 = self.preset()
        device = asr_settings.get("device", "cuda")
        return asr.TranscribeOptions(
            model=model or 강도.model,
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
            keep_nonverbal=bool(asr_settings.get("keep_nonverbal", True)),
            beam_size=강도.beam_size,
            no_repeat_ngram_size=강도.no_repeat_ngram_size,
            repetition_penalty=강도.repetition_penalty,
            use_primer=강도.use_primer,
        )

    def _모델받는동안(self, job: Job, 모델이름: str):
        """모델을 올리는 동안 화면에 무엇이 되고 있는지 띄운다.

        처음 켜면 여기서 3GB 를 내려받는다. 「극한」이면 두 개라 6GB 다.
        그동안 아무 표시가 없으면 사용자는 **눌렀는데 아무 일도 안 일어난다**
        고 본다. 실제로 그렇게 겪었다.

        받은 만큼 폴더가 커지므로 그것을 재서 보여 준다. 진짜 내려받기 진행률을
        가로채는 것보다 성기지만, 판마다 달라지는 남의 속을 뒤지지 않아도 된다.
        """
        멈춤 = threading.Event()

        def 지켜보기() -> None:
            """**여기서 터지면 진행 표시가 그 자리에 굳는다.**

            일꾼이 죽어도 받아쓰기는 그대로 도는데 화면만 안 움직인다. 그러면
            사용자는 멈춘 줄 알고 앱을 껐다 켠다 — 3시간짜리를 20분 받아쓰던
            중이었으면 그 20분이 통째로 날아간다.

            「모델을 올리는 중」 이 10분간 거짓말하던 것과 같은 종류다. 그때는
            문구가 틀렸고, 이번엔 문구가 아예 안 바뀐다.
            """
            try:
                _지켜보기()
            except Exception as error:      # noqa: BLE001
                log.error("모델 준비 지켜보기가 죽음", error,
                          파일=job.audio.name)

        def _지켜보기() -> None:
            처음 = _받은크기(모델이름)
            큰것 = 처음
            안커진지 = 0.0          # 크기가 안 늘어난 채 흐른 시간(초)
            흐른시간 = 0.0

            while not 멈춤.wait(1.5):
                흐른시간 += 1.5
                지금 = _받은크기(모델이름)
                if 지금 > 큰것 + 1024 * 1024:      # 1MB 넘게 늘었다
                    큰것 = 지금
                    안커진지 = 0.0
                else:
                    안커진지 += 1.5

                받은것 = f"{지금 / (1024 ** 3):.1f}GB"

                if 지금 <= 0:
                    # 폴더가 아직 없다. 여기서 오래 머무는 것이 제일 나쁘다 —
                    # 받고 있는 것인지 인터넷이 막힌 것인지 알 수가 없다
                    job.message = (
                        "받아쓰기 모델을 준비하는 중…"
                        if 안커진지 < 60 else
                        f"받아쓰기 모델 폴더가 {int(안커진지)}초째 안 생깁니다. "
                        "인터넷이 막혔을 수 있습니다"
                    )
                elif 안커진지 < 12:
                    # 방금도 자랐다. 받는 중이 맞다
                    job.message = (
                        f"받아쓰기 모델을 내려받는 중… {받은것} (처음 한 번만 받습니다)"
                    )
                elif 안커진지 < 90:
                    # **얼마나 받았는지 같이 말한다.** 예전에는 크기가 안 늘면
                    # 무조건 「올리는 중」 이라고 했다. 다 받고 올리는 것인지
                    # 받다가 멈춘 것인지 구별을 못 해서, 10분을 그 문구만 보고
                    # 기다리는 일이 생겼다
                    job.message = f"받아쓰기 모델을 올리는 중… ({받은것} 받아 둠)"
                else:
                    # 90초 넘게 한 바이트도 안 늘었다. 말해 줘야 한다
                    job.message = (
                        f"{받은것} 에서 {int(안커진지)}초째 안 늘고 있습니다. "
                        "받다 멈췄거나 모델을 올리는 중입니다"
                    )
                self._emit(job)
                if 흐른시간 in (60.0, 300.0):
                    log.write("받아쓰기", "모델 준비가 길어짐",
                              파일=job.audio.name, 초=int(흐른시간),
                              받은바이트=지금, 안커진초=int(안커진지))

        일꾼 = threading.Thread(target=지켜보기, daemon=True)

        class 감싸개:
            def __enter__(그거):
                일꾼.start()
                return 그거

            def __exit__(그거, *버릴것):
                멈춤.set()
                일꾼.join(timeout=3)
                return False

        return 감싸개()

    def _그래픽카드_비우기(self) -> None:
        """받아쓰기 전에 번역 모델이 붙잡고 있는 VRAM 을 돌려받는다.

        Ollama 는 한 번 쓴 모델을 한동안 붙잡고 있다. 우리가 `keep_alive` 를
        30분으로 넣어 두기까지 했다. 7B 면 5~6GB 다. 12GB 카드에서 그것이
        앉아 있는 채로 `large-v3`(약 4.7GB)를 올리면 자리가 모자란다.

        **그때 ctranslate2 는 파이썬 오류를 내지 않고 프로세스를 죽인다.**
        자취도 안 남는다. 그래서 미리 비운다.

        연결 확인만 해도 모델이 올라간다. 번역을 한 번도 안 돌렸어도 그렇다.
        실패해도 넘어간다 — 못 비웠다고 받아쓰기를 멈추면 안 된다.
        """
        설정 = self.settings.get("translation", {})
        if route.정해진값(self.settings)["보내는길"] != "ollama":
            return
        try:
            from app.core import providers

            앞 = gpu.free_vram_gb()
            providers.create(
                "ollama",
                model=str(설정.get("model") or ""),
                url=str(설정.get("url") or ""),
            ).unload()
            뒤 = gpu.free_vram_gb()
            log.write(
                "그래픽카드", "번역 모델을 내려 자리를 비움",
                전=None if 앞 is None else round(앞, 1),
                후=None if 뒤 is None else round(뒤, 1),
            )
        except Exception as error:
            log.write("그래픽카드", "번역 모델을 못 내림", 까닭=str(error))

    def _묶을만큼(self, job: Job, options: asr.TranscribeOptions,
                남은: float) -> asr.TranscribeOptions:
        """남은 자리를 보고 몇 조각씩 묶어 넣을지 정해서 얹는다.

        묶지 않으면 30초 창을 하나씩 도느라 그래픽카드가 대부분 논다. 카드는
        미지근하고 VRAM 도 모델 크기에서 안 움직이는데 몇 시간씩 걸린다 —
        「느린데 VRAM 은 적게 쓴다」 가 그 모양이었다.

        **모델이 앉을 자리는 이미 빼고 센다**(`asr.묶음크기`). 자리가 빠듯하면
        0 이 나오고, 그러면 예전처럼 하나씩 넣는다. 죽는 것보다 느린 것이 낫다.
        """
        if options.device != "cuda":
            return options
        if not asr.묶어넣기가_되나():
            # 옛 faster-whisper 다. 판올림하면 빨라진다는 것을 알려 준다
            log.write("받아쓰기", "묶어 넣기를 못 씀 — faster-whisper 가 옛 판이다",
                      파일=job.audio.name)
            return options
        칸 = asr.묶음크기(남은, options.model, options.beam_size,
                      options.word_timestamps)
        if 칸 < 2:
            return options
        log.write("받아쓰기", "묶어서 넣는다", 파일=job.audio.name,
                  모델=options.model, 남은=f"{남은:.1f}GB", 묶음=칸)
        return replace(options, batch_size=칸)

    def _자리가_되는지(self, job: Job, options: asr.TranscribeOptions) -> asr.TranscribeOptions:
        """그래픽카드에 자리가 없으면 CPU 설정으로 바꿔서 돌려준다.

        자리가 모자란 채로 모델을 밀어 넣으면 ctranslate2 는 **파이썬 오류를
        내지 않고 프로세스를 통째로 죽인다.** 창이 그냥 닫히고 자취도 안 남는다.
        사용자가 밤새 겪은 것이 이것이다.

        느린 것이 죽는 것보다 낫다. 재 보고 안 되겠으면 내려간다.

        못 재면(`nvidia-smi` 가 없거나 카드가 없으면) 그대로 둔다. 못 쟀다고
        멀쩡한 그래픽카드를 포기하면 안 된다.
        """
        남은 = gpu.free_vram_gb()
        if 남은 is None:
            return options

        필요 = asr.vram_needed_gb(
            options.model, options.beam_size, options.word_timestamps)
        if 남은 >= 필요:
            return self._묶을만큼(job, options, 남은)

        # **CPU 로 내려가기 전에 빔부터 낮춘다.**
        #
        # 빔이 자리를 제일 많이 먹는다. 12GB 카드에서 「아주 정확하게」
        # (beam=10)가 안 들어간다고 곧바로 CPU 로 내려가면, 20분짜리가
        # 수십 분이 된다. 빔을 5나 1로 낮추면 대개 들어가고, 그래도
        # 그래픽카드가 CPU 보다 훨씬 빠르다.
        #
        # 낮췄다는 것은 **말해 준다.** 말없이 낮추면 「아주 정확하게」 를
        # 골라 놓고 그것이 안 먹은 채로 자막이 나온다
        for 낮춘빔 in (5, 1):
            if 낮춘빔 >= options.beam_size:
                continue
            들어가나 = asr.vram_needed_gb(
                options.model, 낮춘빔, options.word_timestamps)
            if 남은 < 들어가나:
                continue
            log.write(
                "그래픽카드", "자리가 모자라 빔을 낮춤 — CPU 보다 낫다",
                파일=job.audio.name, 모델=options.model,
                남은=f"{남은:.1f}GB", 필요=f"{필요:.1f}GB",
                빔=f"{options.beam_size}→{낮춘빔}",
            )
            job.message = (
                f"그래픽카드 자리가 {남은:.1f}GB 뿐이라 "
                f"넓게 훑기를 {options.beam_size}에서 {낮춘빔}로 낮췄습니다"
            )
            self._emit(job)
            self._model = None
            return self._묶을만큼(job, replace(options, beam_size=낮춘빔), 남은)

        log.write(
            "그래픽카드", "자리가 모자라 CPU 로 내려감 — 죽는 것보다 낫다",
            파일=job.audio.name, 모델=options.model,
            남은=f"{남은:.1f}GB", 필요=f"{필요:.1f}GB", 빔=options.beam_size,
        )
        job.message = (
            f"그래픽카드에 자리가 {남은:.1f}GB 뿐입니다 "
            f"(이 설정은 {필요:.1f}GB 필요). CPU 로 처리합니다. 느립니다"
        )
        self._emit(job)
        self._model = None
        return options.for_cpu()

    def _ensure_model(self, options: asr.TranscribeOptions):
        """모델은 한 번만 올린다. 파일마다 올리면 대기열이 훨씬 느려진다."""
        if self._model is not None and self._model_options == options:
            return self._model
        if self._model is not None:
            # 다른 모델로 바꾸는 길이다. 먼저 내리지 않으면 새것을 올리는 동안
            # 둘이 함께 VRAM 에 앉아 있게 된다. 12GB 에서는 위험하다
            self.release_model()
        self._model = self.load_model(options)
        self._model_options = options
        return self._model

    def release_model(self) -> None:
        """받아쓰기 모델을 그래픽카드에서 내린다.

        기준 PC 의 VRAM 은 12GB 다. `large-v3` 가 4~5GB, 번역 모델이 5~9GB 라
        같이 올리면 넘친다. 로컬 번역을 돌리기 전에 반드시 내려야 한다.
        다음에 받아쓸 때 다시 올라간다 — 그 대가는 모델 올리는 시간뿐이다.

        `torch` 가 있으면 캐시까지 비운다. 없으면 참조만 끊는다.
        """
        if self._model is None:
            return
        self._model = None
        self._model_options = None
        import gc

        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass  # torch 가 없어도 참조는 끊었다
        log.write("모델", "받아쓰기 모델을 그래픽카드에서 내림")

    # ---- 받아쓰기 ----

    def look_up_work(self, job: Job) -> None:
        """파일 이름이나 폴더에서 품번을 찾아 작품 정보를 가져온다.

        없어도 그만이다. 인터넷이 안 되거나 품번이 없으면 그냥 넘어간다.
        이것 때문에 자막을 못 만들면 안 된다.
        """
        # 사용자가 정해 준 것이 있으면 그것이 이긴다
        work_id = job.work_id or dlsite.extract_work_id(job.audio)
        if not work_id:
            return
        try:
            job.work = self.fetch_work(
                work_id, cache_dir=settings_store.config_dir() / "works"
            )
        except Exception:
            return  # 남의 서버가 죽어도 자막은 만들어야 한다
        if job.work is not None and job.work.found:
            job.work_context = job.work.context()

    def transcribe(
        self,
        job: Job,
        *,
        should_stop: Callable[[], bool] | None = None,
        use_cache: bool = True,
    ) -> Job:
        """`use_cache=False` 면 담아 둔 것을 무시하고 처음부터 받아쓴다.

        강도를 견줄 때 쓴다. 담아 둔 것을 그대로 읽으면 두 강도가 같은 결과를
        내놓아서 "차이 없음" 만 나온다. 견주는 뜻이 없어진다.
        """
        self.look_up_work(job)

        log.write("받아쓰기", "시작", 파일=job.audio.name)

        강도 = self.preset()

        if use_cache and load_transcript(job):
            # 이미 받아쓴 파일이다. 2시간짜리를 두 번 돌리게 하면 안 된다.
            #
            # 다만 **어느 강도로 받아쓴 것인지**를 봐야 한다. 그러지 않으면
            # 「빠르게」로 한 번 돌린 파일은 나중에 「속삭임」을 골라도 영영
            # 「빠르게」 결과가 나온다. 강도를 고르는 뜻이 통째로 없어진다
            같은강도 = (not job.cached_preset) or job.cached_preset == 강도.id
            if 같은강도:
                log.write(
                    "받아쓰기", "담아 둔 것을 씀",
                    파일=job.audio.name, 줄=len(job.segments),
                    강도=job.cached_preset or "옛것(모름)",
                )
                job.stage = Stage.번역
                job.progress = 1.0
                return self._start_translation(job, note="받아쓴 것이 남아 있어 건너뜀")

            옛강도 = presets.get(job.cached_preset)
            if job.translations:
                # 붙여넣은 번역이 들어 있다. 다시 받아쓰면 줄 번호가 달라져서
                # **그동안 붙여넣은 것이 통째로 쓸모없어진다.** 말없이 지우지
                # 않는다. 정말 다시 받고 싶으면 '다시 받아쓰기' 를 누르면 된다
                log.write(
                    "받아쓰기", "강도가 다르지만 번역이 있어 그대로 씀",
                    파일=job.audio.name, 담아둔강도=옛강도.name, 고른강도=강도.name,
                )
                job.stage = Stage.번역
                job.progress = 1.0
                job.hint = 강도가_다르다는_말(옛강도, 강도)
                return self._start_translation(job, note=job.hint)

            # 붙여넣은 것이 없으니 잃을 것이 없다. 고른 강도로 다시 받아쓴다
            log.write(
                "받아쓰기", "강도가 달라 다시 받아씀",
                파일=job.audio.name, 담아둔강도=옛강도.name, 고른강도=강도.name,
            )
            job.segments = []
            job.cached_preset = ""
            job.hint = ""
            job.message = f"「{강도.name}」로 다시 받아씁니다"
            self._emit(job)

        # 여기서부터 잰다. 모델 올리는 시간도 기다리는 시간이므로 같이 센다
        job.started_at = time.monotonic()
        job.eta_sec = 0.0

        options = self.options()
        log.write("받아쓰기", "강도", 파일=job.audio.name, 강도=강도.name, 모델=강도.model,
                  beam=강도.beam_size, 소리고르기=강도.normalize, 두번=강도.two_pass)

        # **모델을 올리기 전에** 차례를 바꾸고 무엇이 되고 있는지 띄운다.
        #
        # 처음 켜면 여기서 3GB 를 내려받는다. 「극한」이면 두 개라 6GB 다.
        # 예전에는 이 줄이 `_ensure_model` **뒤에** 있어서, 받는 내내 화면이
        # 「받아쓰기 전」 인 채로 멈춰 있었다. 로그도 「강도」에서 끊긴다.
        # 사용자는 눌렀는데 아무 일도 안 일어나는 것으로 본다 — 실제로 그랬다.
        job.stage = Stage.받아쓰기
        job.message = "받아쓰기 모델을 준비하는 중…"
        self._emit(job)

        # 받아쓰기 모델을 올리기 전에 그래픽카드를 비운다.
        #
        # Ollama 는 한 번 쓴 모델을 한동안 붙잡고 있다. 7B 면 5GB 쯤이다.
        # 12GB 카드에서 그것이 앉아 있는 채로 `large-v3`(약 4.7GB)를 올리면
        # 자리가 모자란다. 그때 ctranslate2 는 **파이썬 오류를 내지 않고
        # 프로세스를 통째로 죽인다.** 자취도 안 남는다.
        #
        # 번역이 끝난 뒤에 내리는 길은 이미 있다. 그런데 사용자가 터미널에서
        # `ollama run` 으로 직접 올려 둔 것은 앱이 내린 적이 없다. 여기서
        # 한 번 더 내린다. 실패해도 넘어간다 — 못 내렸다고 멈추면 안 된다.
        if options.device == "cuda":
            self._그래픽카드_비우기()
            options = self._자리가_되는지(job, options)

        # **어디에 받는지를 남긴다.** 이것이 없어서 「10분째 모델을 올리는
        # 중」 이 나왔을 때, 받고 있는 것인지 엉뚱한 폴더를 재고 있는 것인지
        # 가릴 수가 없었다. 재는 자리와 그때 크기를 같이 적는다
        try:
            자리 = os.environ.get("HUGGINGFACE_HUB_CACHE") or os.environ.get("HF_HUB_CACHE")
            log.write("받아쓰기", "모델 준비 시작", 파일=job.audio.name,
                      모델=강도.model, 캐시자리=자리 or "(기본)",
                      이미받은바이트=_받은크기(강도.model))
        except Exception:
            pass      # 로그 때문에 받아쓰기가 멈추면 안 된다

        try:
            with self._모델받는동안(job, 강도.model):
                model = self._ensure_model(options)
        except asr.GpuUnavailable as error:
            # 그래픽카드를 못 쓰면 CPU로 내려가서 계속한다. 멈추는 것보다 낫다
            log.error("그래픽카드를 못 써서 CPU로 내려감", error, **log.gpu_state())
            options = options.for_cpu()
            self._model = None
            job.gpu문제 = True
            job.message = "그래픽카드를 쓰지 못해 CPU로 처리합니다. 느립니다"
            self._emit(job)
            with self._모델받는동안(job, 강도.model):
                model = self._ensure_model(options)

        job.message = ""
        self._emit(job)

        def 진행(update: asr.TranscribeProgress) -> None:
            job.progress = update.ratio
            job.duration_sec = update.duration_sec
            job.message = f"{update.segment_count}줄"
            job.eta_sec = _남은시간(job.elapsed_sec, update.ratio, 강도.뒷일_몫)
            self._emit(job)

        # 소리가 작으면 VAD 가 통째로 버린다. 키워서 넣는다
        넣을것 = self._leveled(job, 강도)

        # 여기가 로그의 **깜깜한 구간**이었다.
        #
        # 「소리고르기 함」 다음 줄이 곧바로 「1차 끝」이라, 그 사이에서 죽으면
        # 로그가 「소리고르기 함」에서 뚝 끊긴 채로 끝난다. 그것만 보고는
        # 모델을 못 올린 것인지, 훑다가 죽은 것인지 가릴 수 없었다.
        # 실제로 그 자취를 받아 놓고도 어디서 죽었는지 못 읽었다.
        #
        # ctranslate2 가 네이티브에서 죽으면 파이썬 오류가 아예 없다. 프로세스가
        # 통째로 사라진다. 그러면 **마지막으로 남은 줄이 유일한 단서**다.
        # 그 줄에 무엇을 넣고 들어갔는지까지 적어 둔다.
        log.write(
            "받아쓰기", "훑기 시작 — 여기서 끊기면 모델 안에서 죽은 것이다",
            파일=job.audio.name, 모델=options.model, 장치=options.device,
            계산=options.compute_type, 단어시각=options.word_timestamps,
            # **여기가 제일 중요하다.** 자리가 모자라면 ctranslate2 는 오류를
            # 내지 않고 죽는다. 죽은 뒤에 남는 것은 이 숫자 하나뿐이다
            남은VRAM=(lambda 남: None if 남 is None else f"{남:.1f}GB")(gpu.free_vram_gb()),
            되풀이막기=options.no_repeat_ngram_size or None,
            눌러둠=("풂" if options.keep_nonverbal else "기본"),
            넣는것=("고른 소리" if 넣을것 is not job.audio else "원본 파일"),
        )

        훑기시작 = time.monotonic()
        result = self.transcribe_fn(
            model, job.audio, options,
            on_progress=진행, should_stop=should_stop,
            audio_data=None if 넣을것 is job.audio else 넣을것,
        )
        훑는데걸림 = max(0.001, time.monotonic() - 훑기시작)
        job.duration_sec = result.duration_sec
        raw = result.segments

        if getattr(result, "stopped", False) or (should_stop and should_stop()):
            # 도중에 멈춘 것은 반쪽이다. 담아 두면 다음에 켤 때 그 반쪽을 "이미
            # 받아썼다" 며 그대로 쓴다. 2시간짜리가 영영 30분짜리로 남는다
            log.write("받아쓰기", "멈춤 — 담아 두지 않음", 파일=job.audio.name, 줄=len(raw))
            job.segments = []
            job.stage = Stage.대기
            job.progress = 0.0
            job.message = "멈췄습니다. 다시 시작하면 처음부터 받아씁니다"
            self._emit(job)
            return job
        # **배속을 남긴다.** 「느리다」 는 말만으로는 CPU 로 떨어진 것인지,
        # 묶음이 안 걸린 것인지, 원래 이만큼인지 가릴 수 없다. 숫자가 있어야
        # 다음에 무엇을 볼지 정해진다.
        #
        # 눈금 — CPU 는 0.5배속 아래, 그래픽카드에 하나씩 넣으면 2~5배속,
        # 묶어서 넣으면 10배속 위로 나온다
        배속 = result.duration_sec / 훑는데걸림
        job.배속 = round(배속, 1)
        log.write(
            "받아쓰기", "1차 끝",
            파일=job.audio.name, 길이=round(result.duration_sec, 1), 줄=len(raw),
            모델=options.model, 장치=options.device,
            걸림=f"{훑는데걸림:.0f}초", 배속=f"{배속:.1f}배",
            묶음=options.batch_size or "안 묶음",
        )

        # **강도만 본다.** 예전에는 설정 체크박스로도 끌 수 있었는데, 유저
        # 입장에서 품질 강화를 끌 이유가 없다는 말을 듣고 뺐다. 설정 읽기를
        # 남겨 두면 옛날에 꺼 둔 채 저장된 사람이 **되켤 단추도 없이 영영
        # 꺼진 채**가 되므로, 저장된 값은 여기서 안 본다
        if 강도.rescan_gaps:
            raw = self._rescan(job, model, options, raw, should_stop=should_stop, 소리=넣을것)

        if 강도.retry_broken:
            raw = self._retry_broken(
                job, model, options, raw, should_stop=should_stop, 소리=넣을것
            )

        # 두 번째 모델로 또 훑어서 합친다. 한쪽이 놓친 것을 다른 쪽이 잡는다
        if 강도.two_pass and not (should_stop and should_stop()):
            raw = self._second_pass(job, 강도, 넣을것, raw, should_stop=should_stop)

        # 합치기는 마지막에 한다. 다시 훑어 찾은 것까지 함께 합쳐야 한다
        job.segments = merge_segments(raw)
        # 이 결과가 어느 강도로 나온 것인지 새겨 둔다. 담아 둘 때 함께 적힌다
        job.cached_preset = 강도.id
        job.duration_after_vad = getattr(result, "duration_after_vad", 0.0)
        if 강도.check_coverage:
            self._check_coverage(job, 소리=넣을것)
        log.write(
            "받아쓰기", "합친 뒤",
            파일=job.audio.name, 전=len(raw), 후=len(job.segments),
        )
        job.stage = Stage.번역
        job.progress = 1.0
        job.message = f"{len(job.segments)}줄"
        if use_cache:
            # 견주려고 돌린 것은 담아 두지 않는다. 진짜 결과를 덮으면 안 된다
            save_transcript(job)
        return self._start_translation(job)

    def _rescan(
        self,
        job: Job,
        model,
        options: asr.TranscribeOptions,
        raw: list[dict[str, Any]],
        *,
        should_stop: Callable[[], bool] | None = None,
        소리=None,
    ) -> list[dict[str, Any]]:
        gaps = asr.find_gaps(raw, job.duration_sec)
        if not gaps:
            return raw

        job.stage = Stage.다시훑기
        job.progress = 0.0
        job.message = f"말이 안 잡힌 {len(gaps)}군데를 다시 봅니다"
        self._emit(job)

        def 진행(position: int, total: int) -> None:
            job.progress = position / total if total else 1.0
            self._emit(job)

        found = self.rescan_fn(
            model, 소리 if 소리 is not None else job.audio, gaps, options,
            on_progress=진행, should_stop=should_stop,
        )
        log.write(
            "다시훑기", "끝",
            파일=job.audio.name, 빈곳=len(gaps), 찾음=len(found),
        )
        if not found:
            return raw

        merged = asr.merge_rescanned(raw, found)
        job.message = f"다시 훑어 {len(merged) - len(raw)}줄을 더 찾았습니다"
        self._emit(job)
        return merged

    def _retry_broken(
        self,
        job: Job,
        model,
        options: asr.TranscribeOptions,
        raw: list[dict[str, Any]],
        *,
        should_stop: Callable[[], bool] | None = None,
        소리=None,
    ) -> list[dict[str, Any]]:
        """통째로 망가진 구간을 설정을 바꿔 다시 받아쓴다.

        whisper 는 못 알아들으면 비워 두지 않고 그럴듯한 글자를 지어낸다. 그것이
        번역으로 넘어가면 AI 가 헛소리를 그럴듯한 한국어로 바꿔 준다. 재료가
        썩으면 누가 요리해도 안 된다.

        같은 설정으로 다시 보면 같은 헛소리가 나오므로 지어내는 쪽을 조여서 본다.
        새로 받은 것이 더 나을 때만 바꾼다.
        """
        구간 = garbage.find_broken_spans(raw)
        if not 구간:
            return raw

        job.stage = Stage.다시훑기
        job.progress = 0.0
        job.message = f"받아쓰기가 망가진 {len(구간)}군데를 다시 봅니다"
        self._emit(job)

        def 진행(자리: int, 전체: int) -> None:
            job.progress = 자리 / 전체 if 전체 else 1.0
            self._emit(job)

        새로받음 = self.rescan_fn(
            model, 소리 if 소리 is not None else job.audio, 구간, options.for_retry(),
            on_progress=진행, should_stop=should_stop,
            # 여기서 또 손대면 조여 놓은 것이 지워진다. VAD를 끄면 더 지어낸다
            retune=False,
        )
        if not 새로받음:
            log.write("망가진구간", "다시 봐도 아무것도 못 얻음", 파일=job.audio.name)
            return raw

        고친것 = _replace_spans(raw, 구간, 새로받음)
        전 = len(garbage.find_broken(raw))
        후 = len(garbage.find_broken(고친것))
        log.write(
            "망가진구간", "다시 봄",
            파일=job.audio.name, 군데=len(구간), 망가진줄=f"{전}→{후}",
        )
        if 후 >= 전:
            return raw  # 나아지지 않았으면 건드리지 않는다
        job.message = f"망가진 줄 {전}개를 {후}개로 줄였습니다"
        self._emit(job)
        return 고친것

    def _leveled(self, job: Job, 강도: presets.Preset):
        """모델에 넣을 것. 강도가 시키면 소리를 키워서 넣는다.

        속삭임이 통째로 사라지는 가장 큰 원인은 VAD 가 "너무 작아서 말이 아님"
        으로 버리는 것이다. 기준을 낮추는 것에는 한계가 있고, 아예 키워서 넣으면
        넉넉히 잡힌다.

        못 하면 원본 경로를 그대로 준다. 이것 때문에 받아쓰기를 못 하면 안 된다.
        """
        if not 강도.normalize:
            return job.audio
        try:
            소리, 잰것 = preprocess.load_leveled(job.audio)
        except preprocess.PreprocessUnavailable as error:
            log.write("소리고르기", "건너뜀", 파일=job.audio.name, 까닭=str(error))
            return job.audio
        except Exception as error:
            log.error("소리 고르기 실패", error, 파일=job.audio.name)
            return job.audio

        log.write(
            "소리고르기", "함",
            파일=job.audio.name,
            원래크기=round(잰것["rms"], 4),
            배율=round(잰것["applied_gain"], 2),
        )
        if 잰것["applied_gain"] >= 4:
            job.message = "녹음이 작아서 소리를 키워 넣습니다"
            self._emit(job)
        return 소리

    def _second_pass(
        self,
        job: Job,
        강도: presets.Preset,
        넣을것,
        raw: list[dict[str, Any]],
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """다른 모델로 한 번 더 훑어서 합친다.

        모델마다 놓치는 것이 다르다. 신음에 특화된 모델은 또렷한 대사를 가끔
        흘리고, 기본 모델은 신음을 통째로 버린다. 둘을 합치면 서로를 메운다.

        **겹치는 것은 넣지 않는다.** 이미 줄이 있는 자리는 그대로 두고, 비어
        있는 자리에만 끼워 넣는다. 그러지 않으면 같은 대사가 두 번 나온다.
        """
        job.stage = Stage.다시훑기
        job.progress = 0.0
        job.message = f"{강도.second_model} 로 한 번 더 훑습니다"
        self._emit(job)

        두번째 = self.options(model=강도.second_model)
        # 두 번째 모델은 앞 문맥 예시를 쓰는 쪽이 낫다. 기본 모델이기 때문이다
        두번째.use_primer = True
        두번째.no_repeat_ngram_size = 0
        두번째.repetition_penalty = 1.0

        try:
            model = self._ensure_model(두번째)
            결과 = self.transcribe_fn(
                model, job.audio, 두번째, should_stop=should_stop,
                audio_data=None if 넣을것 is job.audio else 넣을것,
            )
        except Exception as error:
            log.error("두 번째 훑기 실패", error, 파일=job.audio.name, 모델=강도.second_model)
            return raw

        if getattr(결과, "stopped", False):
            return raw

        합침 = asr.merge_rescanned(raw, 결과.segments)
        log.write(
            "두번훑기", "합침",
            파일=job.audio.name, 모델=강도.second_model,
            첫판=len(raw), 두번째=len(결과.segments), 합계=len(합침),
        )
        job.message = f"두 번째 훑기로 {len(합침) - len(raw)}줄을 더 찾았습니다"
        self._emit(job)
        return 합침

    def _check_coverage(self, job: Job, *, 소리=None) -> None:
        """소리는 있는데 자막이 없는 자리를 찾는다. **찾기만 한다.**

        전사용 VAD 와 다른, 더 관대한 VAD 를 한 번 더 돌린다. 같은 VAD 로 다시
        보면 같은 것을 또 버려서 아무것도 못 찾는다.

        여기서 다시 받아쓰지 않는 까닭은 `coverage.py` 에 적어 두었다. 관대한
        VAD 는 BGM 과 옷 스치는 소리도 잡는데, 확인 없이 다시 받아쓰면 효과음을
        그럴듯한 일본어로 받아적어 자막에 넣게 된다.

        켜고 끄는 것은 **강도가 정한다** (`강도.check_coverage` — 부르는 쪽).
        설정 체크박스가 따로 있었는데 유저가 끌 이유가 없어서 뺐다. 저장된
        옛 값을 읽으면 꺼 둔 사람이 되켤 단추도 없이 영영 꺼진 채가 된다.
        """
        try:
            들린것 = self.detect_speech_fn(
                job.audio if 소리 is None or 소리 is job.audio else 소리
            )
        except coverage.DetectorUnavailable as error:
            log.write("커버리지", "검사용 VAD 를 못 씀", 파일=job.audio.name, 까닭=str(error))
            return
        except Exception as error:  # 진단 때문에 자막을 못 만들면 안 된다
            log.error("커버리지 검사 실패", error, 파일=job.audio.name)
            return

        job.uncovered = coverage.find_uncovered(들린것, job.segments)
        log.write(
            "커버리지", "봄",
            파일=job.audio.name,
            소리구간=len(들린것),
            덮인비율=f"{coverage.covered_ratio(job.segments, 들린것):.0%}",
            빠진곳=len(job.uncovered),
            VAD통과=round(job.duration_after_vad, 1) or None,
            전체=round(job.duration_sec, 1),
        )
        for 시작, 끝 in job.uncovered[:20]:
            log.write("커버리지", f"빠진 곳 {_clock(시작)} ~ {_clock(끝)}")

    # ---- 번역 ----

    def _start_translation(self, job: Job, *, note: str = "") -> Job:
        # 받아쓰기가 끝났다. 남은 시간을 계속 띄워 두면 끝났는데도 기다리는
        # 것처럼 보인다
        job.started_at = 0.0
        job.eta_sec = 0.0
        job.report = quality.inspect(
            job.segments, job.duration_sec, work=job.work, uncovered=job.uncovered
        )
        log.write(
            "검사", job.report.summary(),
            파일=job.audio.name, 잡힌비율=f"{job.report.coverage:.0%}",
        )
        for 짚은것 in job.report.findings:
            log.write("검사", f"{짚은것.at} {짚은것.kind}: {짚은것.message}")
        # 받아쓰기가 잘 됐는지는 이 시점에 알아야 한다. 번역까지 하고 나서
        # "말이 절반밖에 안 잡혔네" 를 알면 늦다
        job.message = f"{note} · {job.report.summary()}" if note else job.report.summary()

        # 담아 둔 번역이 다 차 있으면 바로 자막까지 만든다. 그러지 않으면 다시
        # 켰을 때 번역 차례로 남아 있는데 대기열에는 안 올라와서 영영 안 끝난다
        if job.segments and all(
            job.translations.get(s["index"], "").strip() for s in job.segments
        ):
            try:
                return self.finish(job)
            except Exception as error:
                log.error("담아 둔 번역으로 자막 만들기 실패", error, 파일=job.audio.name)

        self._emit(job)
        return job

    def save_japanese(self, job: Job) -> Path | None:
        """받아적은 일본어를 시각과 함께 파일로 낸다.

        받아쓰기가 정확한지는 사람이 들어 보는 수밖에 없다. 시각이 붙어 있으면
        음원을 그 지점으로 돌려 놓고 맞는지 볼 수 있다.
        """
        if not job.segments:
            return None

        줄 = [f"# {job.audio.name}", f"# {len(job.segments)}줄", ""]
        for segment in job.segments:
            줄.append(f"[{_clock(segment['start'])}] {segment['ja']}")

        target = job.audio.with_suffix(".ja.txt")
        글 = "\n".join(줄) + "\n"
        try:
            _한번에쓰기(target, 글)
        except OSError:
            target = Path(
                self.settings.get("output", {}).get("fallback_dir")
                or str(Path.home() / "Desktop")
            ) / _대체이름(job, target.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            _한번에쓰기(target, 글)
        return target

    def run_auto_translation(self, group, *, should_stop: Callable[[], bool] | None = None):
        """공급자로 자동 번역한다. 막힌 묶음은 복붙 대기줄에 남는다.

        묶음은 작품 단위라 트랙 여럿이 한 번에 처리된다.
        """
        from app.core import providers

        if group.session is None or group.done:
            return group

        chosen = route.정해진값(self.settings)["보내는길"]
        if chosen == "manual":
            return group

        if providers.is_local(chosen):
            # 내 컴퓨터에서 도는 번역 모델은 받아쓰기와 같은 그래픽카드를 쓴다.
            # 12GB 에 둘 다 올리면 터진다. 받아쓰기 쪽을 먼저 내린다
            self.release_model()

        # 주소와 모델 이름은 **내 컴퓨터에서 도는 것에만** 쓴다.
        #
        # 화면에서 그 둘은 로컬 공급자를 골랐을 때만 입력할 수 있는데, 설정은
        # 덮어쓰기가 아니라 겹쳐 쌓기라 한 번 넣은 값이 그대로 남는다. 그래서
        # Ollama 를 쓰다 Groq 으로 바꾸면 Groq 이 `localhost:11434` 로 불렸다.
        # 고른 곳은 불려 본 적도 없고, Groq 키가 내 컴퓨터로 날아갔다.
        # Gemini 로 바꾸면 모델 이름이 `qwen2.5:14b` 인 채로 불려 404 가 났다.
        번역설정 = self.settings.get("translation", {})
        내컴퓨터 = providers.is_local(chosen)
        provider = providers.create(
            chosen,
            api_key=settings_store.api_key(self.settings, chosen),
            model=str(번역설정.get("model") or "") if 내컴퓨터 else "",
            url=str(번역설정.get("url") or "") if 내컴퓨터 else "",
        )

        def 진행(update) -> None:
            for job in group.jobs:
                job.stage = Stage.번역
                job.message = update.message
                self._emit(job)

        try:
            group.session.run_auto(provider, on_progress=진행, should_stop=should_stop)
        finally:
            if 내컴퓨터:
                # 번역이 끝났으면(또는 터졌으면) 그래픽카드를 되돌려 준다.
                # 안 내리면 다음 받아쓰기가 남은 VRAM 으로 돌다가 터지거나
                # 조용히 CPU 로 떨어져서 몇 배 느려진다
                provider.unload()
                log.write("번역", "로컬 모델 내림", 모델=provider.model)
        return group

    # ---- 자막 ----

    def finish(self, job: Job) -> Job:
        """모은 번역으로 자막을 쓴다."""
        job.stage = Stage.자막
        self._emit(job)

        output = self.settings.get("output", {})
        # 「자막 밀릴 때 보정」 설정은 뺐다 — 쓰인 적이 없고, 저장된 옛 값을
        # 계속 읽으면 자막이 이유 없이 밀린 채 고칠 단추도 없다
        entries = lrc.build_entries(
            job.segments,
            job.translations,
            gap_clear_sec=float(output.get("gap_clear_sec", lrc.GAP_CLEAR_SEC)),
        )
        if not entries:
            job.stage = Stage.실패
            job.error = "번역된 줄이 하나도 없어 자막을 만들지 못했습니다."
            log.write("자막", "못 만듦 — 번역이 없음", 파일=job.audio.name)
            self._emit(job)
            return job

        # `[ti:]` 는 **일본어를 그대로** 쓴다. 자막 파일은 다른 앱이 읽는
        # 것이고, 원본 제목이 있어야 음원과 짝을 찾는다. 품번을 못 찾은
        # 작품은 파일 이름을 쓰는데, 제목 번역으로 이름을 바꾸고 나면 그것이
        # 한국어가 되어 버린다. 바꾸기 전 이름을 되찾아 쓴다
        if job.work and job.work.found and job.work.title:
            제목 = job.work.title
        else:
            from app.core import titles as titles_store
            # 작품 열쇠는 품번일 수도 폴더일 수도 있다(`api._display_key`).
            # 어느 쪽으로 담겼는지 여기서는 모르므로 둘 다 본다
            후보 = [
                (job.work_id or "").strip(),
                dlsite.extract_work_id(job.audio) or "",
                str(job.audio.parent),
            ]
            옛이름 = job.audio.name
            for 열쇠 in 후보:
                찾은것 = titles_store.원래이름(열쇠, job.audio.name)
                if 찾은것 != job.audio.name:
                    옛이름 = 찾은것
                    break
            제목 = Path(옛이름).stem
        job.output = lrc.write(self._output_path(job), entries, title=제목)
        log.write("자막", "만듦", 파일=str(job.output), 줄=len(entries))
        save_transcript(job)  # 다 된 것도 담아 둔다. 다시 넣으면 그대로 나온다
        job.report = quality.inspect(
            job.segments,
            job.duration_sec,
            translation=job.translations,
            work=job.work,
            uncovered=job.uncovered,
        )

        # 번역이 다 차지 않았는데 "완료" 라고 하면 안 된다. 자막은 만들어 두되
        # 아직 물어볼 것이 남은 트랙은 번역 차례로 남겨야, 작품 상자에
        # "번역할 것 3개" 가 제대로 뜬다
        빠진줄 = sum(
            1 for s in job.segments if not job.translations.get(s["index"], "").strip()
        )
        if 빠진줄:
            job.stage = Stage.번역
            job.progress = job.translated_ratio
            job.message = (
                f"{빠진줄}줄이 아직 안 됐습니다. 지금까지 것으로 자막을 만들어 뒀습니다"
            )
        else:
            job.stage = Stage.완료
            job.progress = 1.0
            job.message = job.report.summary()
        self._emit(job)
        return job

    def _output_path(self, job: Job) -> Path:
        """음원 옆에 같은 이름으로. 거기 못 쓰면 대체 폴더로."""
        beside = lrc.output_path_for(job.audio)
        output = self.settings.get("output", {})
        if output.get("next_to_audio", True) and lrc.can_write_next_to(job.audio):
            return beside

        fallback = output.get("fallback_dir") or str(Path.home() / "Desktop")
        job.message = f"음원 폴더에 쓸 수 없어 {fallback} 에 저장합니다"
        return Path(fallback) / _대체이름(job, beside.name)

    def _emit(self, job: Job) -> None:
        if self.on_event:
            self.on_event(job)
