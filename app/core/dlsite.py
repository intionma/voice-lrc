"""품번으로 작품 정보를 가져온다.

동인음성은 파일이나 폴더 이름에 `RJ01234567` 같은 품번이 붙어 있다. 그것만 있으면
제목, 태그, 성우를 알 수 있다.

세 군데에 쓴다.

- **태그** — 미성년 설정인지 보는 데 쓴다. 대사만 보는 것보다 훨씬 정확하다.
  나이를 말로 안 하는 작품이 태반이라 대사만으로는 못 잡는다
- **성우와 제목** — 번역할 때 문맥으로 넣는다. 화자가 둘이면 이름을 알려 주는
  것만으로 번역이 나아진다
- **제목** — 자막 파일 안에 적는다

없어도 그만이다. 인터넷이 안 되거나 품번이 없거나 조회가 안 되면 그냥 넘어간다.
이것 때문에 자막을 못 만들면 안 된다.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 구역 여섯 곳을 차례로 물어보므로 하나가 오래 걸리면 전체가 오래 걸린다.
# 못 찾으면 그냥 넘어가는 것이라 오래 기다릴 이유가 없다
TIMEOUT_SEC = 6

# 품번. RJ 는 동인, VJ 는 상업, BJ 는 책이다.
#
# 폴더 이름이 언제나 품번만 있는 것이 아니다. 실제로 이런 것들이 온다.
#
#     [サンプル屋] RJ01234567 耳かき
#     작품RJ01234567                 ← 한글·일본어에 딱 붙어 있다
#     RJ01234567_耳かき              ← 밑줄로 이어진다
#     RJ01234567_01.wav             ← 파일 이름도 마찬가지
#
# `\b` 를 쓰면 이 중 절반을 못 잡는다. 파이썬에서 한글과 한자는 `\w` 라
# "작품RJ..." 사이에 경계가 없고, 밑줄도 `\w` 라 "RJ01234567_" 뒤에 경계가 없다.
#
# 그래서 경계를 직접 정한다.
#   앞: 영문자나 숫자가 아니어야 한다 (ARJ01234567 을 잘못 잡지 않게)
#   뒤: 숫자가 아니어야 한다 (RJ123456789 처럼 자릿수가 안 맞는 것을 자르지 않게)
WORK_ID = re.compile(r"(?<![0-9A-Za-z])([RVB]J\d{6,8})(?![0-9])", re.IGNORECASE)

# 작품이 어느 구역에 있는지 몰라서 차례로 물어본다
SECTIONS = ("maniax", "pro", "girls", "home", "soft", "books")

API = "https://www.dlsite.com/{section}/api/=/product.json?workno={work_id}"

# 남의 서버를 부르는 것이므로 누가 부르는지는 밝힌다
USER_AGENT = "trans-text/0.1 (subtitle tool; personal use)"

# 등장인물이 미성년으로 설정된 작품에 붙는 태그.
#
# 대사를 보는 것보다 이쪽이 정확하다. 나이를 말로 안 하는 작품이 태반이고,
# 받아쓰기가 틀리면 대사 쪽은 아예 못 본다.
MINOR_GENRES = (
    "ロリ",
    "女子小学生",
    "女子中学生",
    "女子学生",
    "男の娘",  # 겹치는 말이라 확인용으로만
    "学園もの",
    "少女",
    "幼馴染",  # 겹치는 말
)

# 위 중에서 이것만으로도 확실한 것
MINOR_GENRES_STRONG = ("ロリ", "女子小学生", "女子中学生")


@dataclass
class Work:
    """작품 하나에 대해 아는 것."""

    id: str
    title: str = ""
    maker: str = ""
    age: str = ""
    kind: str = ""
    series: str = ""
    genres: list[str] = field(default_factory=list)
    voices: list[str] = field(default_factory=list)
    image: str = ""
    found: bool = False

    @property
    def minor_genres(self) -> list[str]:
        return [g for g in self.genres if g in MINOR_GENRES]

    @property
    def minor_suspected(self) -> bool:
        return any(g in MINOR_GENRES_STRONG for g in self.genres)

    def context(self) -> str:
        """번역할 때 앞에 붙일 문맥.

        **성우 이름만 넣는다.** 화자가 둘 이상일 때 이름을 알려 주는 것이
        번역에 도움이 되는 유일한 정보다.

        제목과 태그는 넣지 않는다. 동인음성은 제목과 태그가 노골적인 경우가
        많고, 그것이 프롬프트에 들어가면 안전 필터가 원문을 보기도 전에 그것부터
        본다. 실제로 태그를 넣은 뒤부터 되던 번역이 거절당하기 시작했다.
        """
        if not self.found or not self.voices:
            return ""
        return f"화자: {', '.join(self.voices)}"

    def to_view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "maker": self.maker,
            "voices": self.voices,
            "genres": self.genres,
            "image": self.image,
            "kind": self.kind,
            "age": self.age,
            "minor_genres": self.minor_genres,
            "found": self.found,
        }


def extract_work_id(path: Path) -> str:
    """파일 이름이나 위 폴더에서 품번을 찾는다.

    `RJ01234567/01.音声/01_タイトル.mp3` 처럼 폴더에만 있는 경우가 많다.
    가까운 쪽부터 본다.
    """
    for name in (path.name, *[p.name for p in path.parents]):
        match = WORK_ID.search(name)
        if match:
            return match.group(1).upper()
    return ""


Transport = Callable[[str], str]


def _http_get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        return response.read().decode("utf-8", errors="replace")


def cache_path(work_id: str, folder: Path) -> Path:
    return folder / f"{work_id}.json"


def fetch(
    work_id: str,
    *,
    cache_dir: Path | None = None,
    transport: Transport | None = None,
) -> Work:
    """품번으로 작품 정보를 가져온다. 실패하면 빈 것을 돌려준다.

    한 번 가져온 것은 담아 둔다. 같은 작품을 여러 번 넣어도 남의 서버를 다시
    부르지 않는다.
    """
    work_id = work_id.upper().strip()
    if not WORK_ID.fullmatch(work_id):
        return Work(id=work_id)

    if cache_dir is not None:
        담아둔것 = _load_cache(work_id, cache_dir)
        if 담아둔것 is not None:
            return 담아둔것

    get = transport or _http_get
    닿았다 = False
    for section in SECTIONS:
        try:
            body = get(API.format(section=section, work_id=urllib.parse.quote(work_id)))
            data = json.loads(body)
        except urllib.error.HTTPError as error:
            # 서버가 답을 하긴 했다. 그 구역에 그 작품이 없다는 뜻이다.
            # 이것을 인터넷이 끊긴 것과 같이 다루면 "없는 품번" 을 영영 담아
            # 두지 못해서, 켤 때마다 구역 여섯 곳을 다시 물어본다
            if 400 <= error.code < 500 and error.code != 429:
                닿았다 = True
            continue
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            continue  # 인터넷이 안 되거나 답이 이상하면 다음 구역을 본다
        닿았다 = True  # 서버가 답은 했다
        if isinstance(data, list) and data:
            work = _parse(work_id, data[0])
            if cache_dir is not None:
                _save_cache(work, cache_dir)
            return work

    빈것 = Work(id=work_id)
    # 서버가 "없다" 고 한 것만 담아 둔다. 인터넷이 안 되던 것까지 담아 두면,
    # 인터넷이 없을 때 한 번 켠 것 때문에 그 작품은 영영 "못 찾음" 으로 남는다.
    # 나중에 인터넷이 돼도 다시 묻지 않으니 완전 초기화 말고는 길이 없다
    if cache_dir is not None and 닿았다:
        _save_cache(빈것, cache_dir)
    return 빈것


def _parse(work_id: str, data: dict[str, Any]) -> Work:
    genres = [
        str(g.get("name", "")).strip()
        for g in (data.get("genres") or [])
        if isinstance(g, dict) and g.get("name")
    ]
    creaters = data.get("creaters") or {}
    voices = [
        str(v.get("name", "")).strip()
        for v in (creaters.get("voice_by") or [])
        if isinstance(v, dict) and v.get("name")
    ]
    return Work(
        id=work_id,
        image=_image(data),
        title=str(data.get("work_name") or "").strip(),
        maker=str(data.get("maker_name") or "").strip(),
        age=str(data.get("age_category_string") or "").strip(),
        kind=str(data.get("work_type_string") or "").strip(),
        series=str(data.get("series_name") or "").strip(),
        genres=genres,
        voices=voices,
        found=True,
    )


def _image(data: dict[str, Any]) -> str:
    """표지 그림 주소.

    앞이 `//` 로 시작하는 형태로 오므로 앞을 채워 준다.
    """
    주소 = str(data.get("image_thumb") or "").strip()
    if not 주소:
        return ""
    if 주소.startswith("//"):
        return "https:" + 주소
    return 주소


def _load_cache(work_id: str, folder: Path) -> Work | None:
    path = cache_path(work_id, folder)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return Work(
        id=data.get("id", work_id),
        title=data.get("title", ""),
        maker=data.get("maker", ""),
        age=data.get("age", ""),
        kind=data.get("kind", ""),
        series=data.get("series", ""),
        genres=list(data.get("genres") or []),
        voices=list(data.get("voices") or []),
        image=data.get("image", ""),
        found=bool(data.get("found")),
    )


def _save_cache(work: Work, folder: Path) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        cache_path(work.id, folder).write_text(
            json.dumps(work.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # 담아 두지 못해도 그냥 넘어간다
