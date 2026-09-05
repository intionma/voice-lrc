"""번역할 것을 트랙 단위로 묶는다.

트랙 하나가 묶음 하나다. 트랙이 끝나는 대로 바로 번역할 수 있고, 나머지가
받아쓰는 동안 사용자는 이미 끝난 트랙을 붙여넣고 있으면 된다.

트랙 하나가 길면 그 안에서 다시 나뉜다. 300줄이 넘으면 AI 가 줄을 빠뜨리기
시작하기 때문이다.

작품 정보(성우·분위기)는 프롬프트에 함께 넣는다. 화자가 둘이면 이름을 알려 주는
것만으로 번역이 나아진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core import dlsite, names
from app.core.exchange import BATCH_CHARS, BATCH_LINES, 칸크기
from app.core.translate import TranslationSession

def 번호표주기(jobs: list[Any]) -> None:
    """번호표가 없는 트랙에 번호표를 붙인다. **이미 있는 것은 안 건드린다.**

    번호표가 바뀌면 브라우저에 남아 있던 답이 통째로 어긋나므로, 한 번 준
    번호는 그 트랙이 사라질 때까지 그대로 둔다.

    **가장 작은 빈 자리를 준다.** 계속 커지게 두면 번호가 길어져서 줄마다
    토큰이 늘고, AI 가 큰 번호를 보고 1부터 다시 매기는 일도 잦아진다.
    """
    쓰는것 = {int(getattr(j, "track_id", 0) or 0) for j in jobs}
    다음 = 1
    for job in jobs:
        if int(getattr(job, "track_id", 0) or 0) > 0:
            continue
        while 다음 in 쓰는것:
            다음 += 1
        job.track_id = 다음
        쓰는것.add(다음)


def 칸시작(job: Any) -> int:
    """이 트랙의 번호가 시작하는 자리. 번호표가 없으면 0(옛 방식)이다."""
    번호표 = int(getattr(job, "track_id", 0) or 0)
    return 번호표 * 칸크기

@dataclass
class Group:
    """트랙 하나. 그 안에서 다시 묶음으로 나뉜다.

    **이름이 「Group」이라 오래 헷갈렸다.** 주석에도 「한 작품」이라고 적혀
    있었는데 실제로는 트랙 하나다. 그래서 번역 화면에 작품이라는 층이 아예
    없었고, 작품 셋을 넣으면 트랙 열다섯 개가 일렬로 늘어서서 어느 작품
    것인지 긴 제목 글자로만 가려야 했다.

    이제 어느 작품의 몇 번째 트랙인지를 **함께 들고 다닌다.** 화면이 그것으로
    작품 → 트랙 → 묶음 세 층을 그린다.
    """

    key: str
    title: str
    # 어느 작품인가. `api._display_key` 가 정하는 값(품번이나 폴더)과 같다
    work_key: str = ""
    # 그 작품 안에서 몇 번째 트랙인가. 1부터
    track_no: int = 0
    # 파일 이름만. 작품 제목이 앞에 붙지 않은 것
    track_name: str = ""
    jobs: list[Any] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    owner: dict[int, tuple[Any, int]] = field(default_factory=dict)
    session: TranslationSession | None = None
    # 칸(10,000줄)을 넘쳐서 묶지 못한 트랙. 있으면 화면이 까닭을 알려 준다
    칸넘침: Any = None

    @property
    def done(self) -> bool:
        return self.session is None or self.session.done

    @property
    def touched(self) -> bool:
        """사용자가 이 묶음에 손을 댔는지.

        손대지 않은 묶음은 나중에 끝난 트랙과 다시 합칠 수 있다. 손댄 뒤에 합치면
        번호가 바뀌어 이미 붙여넣은 번역이 어긋난다.
        """
        session = self.session
        if session is None:
            return False
        return session.touched

    @property
    def line_count(self) -> int:
        return len(self.segments)

    @property
    def file_count(self) -> int:
        return len(self.jobs)

    def build(
        self,
        context: str = "",
        *,
        batch_lines: int = BATCH_LINES,
        batch_chars: int = BATCH_CHARS,
        가리기: bool = True,
    ) -> "Group":
        """트랙의 줄을 묶음으로 만든다.

        이미 번역이 있는 줄은 뺀다. 번역을 초기화했을 때 남은 줄만 다시 물어본다.
        """
        self.segments = []
        self.owner = {}

        for job in self.jobs:
            for segment in job.segments:
                if str(job.translations.get(segment["index"], "")).strip():
                    continue  # 이미 번역이 있다
                # **번호는 받아쓴 줄에 붙은 것이다.** 여기서 새로 세면 안 된다.
                #
                # 예전에는 `len(self.segments) + 1` 로 「아직 번역 안 된 줄 중
                # 몇 번째」를 매겼다. 그러면 앞쪽을 번역해 넣은 뒤 다시 묶을 때
                # 뒤쪽 번호가 앞으로 당겨진다. 그런데 사용자의 브라우저에는
                # **다시 묶기 전 번호로 적힌 프롬프트**가 그대로 떠 있다.
                # 그 답을 붙여넣으면 오류도 빠짐도 없이 조용히 엉뚱한 줄에
                # 들어간다 — 750줄 트랙에서 250줄이 250칸 밀렸다.
                #
                # 다시 묶는 길은 재시작만이 아니다. 줄 저장, 줄 삭제, 번역
                # 초기화, 강도 변경이 전부 여기를 지난다.
                자리 = int(segment["index"])
                if 자리 >= 칸크기:
                    # **조용히 옆 칸을 침범하는 것이 최악이다.** 그 순간부터
                    # 옆 트랙의 답을 제 것으로 받아들이고 오류도 안 난다.
                    # 넘치면 이 트랙은 통째로 묶지 않고 사람에게 알린다
                    self.segments = []
                    self.owner = {}
                    self.칸넘침 = job
                    return self
                번호 = 칸시작(job) + 자리
                self.segments.append(
                    {
                        "index": 번호,
                        "start": float(segment["start"]),
                        "end": float(segment["end"]),
                        "ja": segment["ja"],
                    }
                )
                self.owner[번호] = (job, segment["index"])

        # **한 번 쓴 사전은 그 트랙 내내 그대로 쓴다.** 앱을 껐다 켜는 사이에
        # 낱말 하나를 끄면 번호가 밀려서, 아까 복사해 둔 프롬프트로 받은 답의
        # `KW01` 이 딴 낱말로 조용히 되돌아온다.
        #
        # 그래서 담아 둔 것이 있으면 그것을 쓴다. 지금 목록은 **아직 사전이
        # 없는 트랙**에만 붙는다 — 고친 목록은 다음 트랙부터 듣는다
        담아둔사전 = next(
            (것 for 것 in (getattr(job, "가림사전", None) for job in self.jobs) if 것), {}
        )

        self.session = TranslationSession(
            self.segments,
            context=context,
            batch_lines=batch_lines,
            batch_chars=batch_chars,
            # 번역기로 가는 길에서는 가리지 않는다. 번역기는 `KW01` 을 그대로
            # 두지 않아서 되돌릴 수가 없다 (`route.ROUTES` 의 `잠근것`)
            가리기=가리기,
            가림사전=dict(담아둔사전) if 가리기 else {},
            # **내준 프롬프트의 기억을 이어받는다.** 다시 받아쓴 뒤에 옛 답이
            # 들어오면 그 줄만 걸러 내려면 이것이 있어야 한다
            내준원문=dict(
                next((getattr(j, "내준원문", None) for j in self.jobs
                      if getattr(j, "내준원문", None)), {})
            ),
        )
        # 이 트랙이 무슨 사전을 썼는지 담아 둘 수 있게 돌려준다.
        # 시험에서 가짜 일감을 넣기도 해서 밭이 없으면 그냥 지나간다
        for job in self.jobs:
            if hasattr(job, "가림사전"):
                job.가림사전 = dict(self.session.가림사전)
        return self

    def absorb(self) -> list[Any]:
        """모인 번역을 파일별로 나눠 담고, 다 채워진 파일을 돌려준다."""
        if self.session is None:
            return []

        for 통번호, text in self.session.translations.items():
            자리 = self.owner.get(통번호)
            if 자리 is None:
                continue
            job, 원래번호 = 자리
            job.translations[원래번호] = text

        채워진것 = []
        for job in self.jobs:
            if not job.segments:
                continue
            if all(job.translations.get(s["index"], "").strip() for s in job.segments):
                채워진것.append(job)
        return 채워진것

    def to_view(self) -> dict[str, Any]:
        session = self.session
        return {
            "key": self.key,
            "title": self.title,
            "files": self.file_count,
            "lines": self.line_count,
            "done": session.done if session else True,
            # 자동으로 된 것과 손으로 한 것을 따로 더하면 안 된다. 같은 묶음을
            # 두 번 세어 "8/7 끝남" 같은 것이 나온다
            "batch_done": session.finished_count if session else 0,
            "batch_total": session.total_batches if session else 0,
            # **병렬로 돌리면 어느 트랙이 남았는지가 곧 할 일 목록이다.**
            # 묶음 수만으로는 「트랙 다섯 중 셋 남음」 을 그릴 수 없다
            "work_key": self.work_key,
            "track_no": self.track_no,
            "track_name": self.track_name,
            "lines_total": self.line_count,
            "lines_done": len(session.translations) if session else 0,
        }


def 트랙번호_매기기(groups: list[Group]) -> None:
    """작품마다 1부터 다시 센다. **줄 세운 뒤에 불러야 한다.**

    `build_groups` 안에서 세면 안 된다. 그쪽은 **새로 받아쓴 것만** 받기
    때문이다. 작품의 1·2번 트랙을 받아쓰고 나중에 3번을 받아쓰면, 3번이
    또 1번이 되어 화면에 트랙 번호가 두 개씩 뜬다.
    """
    번호: dict[str, int] = {}
    for group in groups:
        번호[group.work_key] = 번호.get(group.work_key, 0) + 1
        group.track_no = 번호[group.work_key]


def _작품열쇠(job: Any) -> str:
    """이 트랙이 어느 작품인가. 품번이 있으면 품번, 없으면 폴더."""
    작품 = getattr(job, "work_id", "") or dlsite.extract_work_id(job.audio)
    return 작품 or str(job.audio.parent)


def group_key(job: Any) -> tuple[str, str]:
    """이 트랙의 묶음 열쇠와 보여 줄 이름.

    트랙 하나가 묶음 하나다. 열쇠는 파일 경로를 쓴다. 이름은 어느 작품의
    무슨 트랙인지 알 수 있게 만든다.
    """
    work_id = getattr(job, "work_id", "") or dlsite.extract_work_id(job.audio)
    이름 = job.audio.stem
    작품 = getattr(job, "work", None)
    if 작품 is not None and getattr(작품, "found", False) and 작품.title:
        이름 = f"{작품.title} · {job.audio.stem}"
    elif work_id:
        이름 = f"{work_id} · {job.audio.stem}"
    try:
        열쇠 = str(job.audio.resolve())
    except OSError:
        # 경로가 260자를 넘거나 드라이브가 빠지면 윈도우에서 여기가 터진다.
        # 파일을 넣는 쪽은 이미 이렇게 막아 두었는데 여기만 빠져 있었다
        열쇠 = str(job.audio)
    return 열쇠, 이름


def build_groups(
    jobs: list[Any],
    *,
    batch_lines: int = BATCH_LINES,
    batch_chars: int = BATCH_CHARS,
    가리기: bool = True,
    work_key_of: Any = None,
) -> list[Group]:
    """받아쓰기가 끝난 트랙을 하나씩 묶음으로 만든다.

    넣은 차례를 지킨다. 사용자가 목록에서 본 차례대로 번역하게 된다.

    묶음 한도는 번역할 곳에 따라 다르다. 사람이 붙여넣는 복붙은 작게, 내
    컴퓨터에서 도는 모델은 크게 준다. 정하는 곳은 `api._rebuild_queue` 다.

    `work_key_of` 로 「이 트랙이 어느 작품인가」를 알려 준다. 안 주면 파일
    이름에서 품번을 뽑고, 그것도 없으면 폴더로 본다. **작업 화면과 같은
    규칙이어야 한다** — 다르면 같은 파일이 두 화면에서 다른 작품이 된다.
    """
    # **묶기 전에 번호표부터.** 없는 트랙에만 붙고, 있던 것은 안 바뀐다
    번호표주기(jobs)

    묶음들 = []
    for job in jobs:
        if not job.segments:
            continue  # 아직 받아쓰지 않았다
        열쇠, 이름 = group_key(job)
        작품 = (work_key_of(job) if work_key_of else "") or _작품열쇠(job)
        group = Group(
            key=열쇠, title=이름, jobs=[job],
            work_key=작품, track_name=job.audio.stem,
        )
        group.build(
            # 호칭 표는 **여기서** 얹는다. 작품 정보를 가져오는 자리에서
            # 얹으면, 호칭을 나중에 적었을 때 다시 가져오기 전까지 안 붙는다.
            # 묶음을 다시 만들 때마다 최신 것을 본다
            context=names.붙이기(getattr(job, "work_context", ""), names.가져오기(작품)),
            batch_lines=batch_lines,
            batch_chars=batch_chars,
            가리기=가리기,
        )
        if group.segments:  # 다 번역된 트랙은 대기열에 넣지 않는다
            묶음들.append(group)
    return 묶음들
