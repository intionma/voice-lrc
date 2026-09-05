"""번역을 대신 해 줄 곳들.

두 갈래다.

**자동** — 사용자가 받아 둔 API 키로 프로그램이 직접 부른다. 카드 등록 없이
받을 수 있는 키만 목록에 둔다. 키는 사용자 PC에만 저장하고 저장소에는 넣지 않는다.

**복붙** — 키 없이 채팅창에 붙여넣는다. 느리지만 막히지 않는다.

자동으로 돌리다 거절당하면 그 묶음부터 복붙으로 넘긴다. 야하지 않은 구간은
자동으로 지나가고 걸리는 곳만 손이 간다.

여기서는 API를 부르고 답의 본문을 꺼내기만 한다. 답이 쓸만한지 세어 보는 것은
`exchange.parse_response` 가 한다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

TIMEOUT_SEC = 180

# 응답을 바로 못 쓰게 만드는 상황들. 부르는 쪽이 골라서 대응한다.


class ProviderError(Exception):
    """공급자 호출이 실패했다."""


class AuthFailed(ProviderError):
    """키가 틀렸거나 권한이 없다. 다시 시도해도 소용없다."""


class RateLimited(ProviderError):
    """쓸 수 있는 한도에 걸렸다. 잠시 뒤에 다시 하면 된다."""

    def __init__(self, message: str, retry_after_sec: float | None = None):
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class Refused(ProviderError):
    """내용 때문에 거절당했다. 다른 곳이나 복붙으로 넘겨야 한다."""


class NetworkError(ProviderError):
    """인터넷이 안 되거나 서버가 죽었다."""


@dataclass(frozen=True)
class ProviderInfo:
    """UI가 목록을 그릴 때 쓰는 설명."""

    id: str
    name: str
    needs_key: bool
    key_url: str
    note: str
    default_model: str = ""
    # 내 컴퓨터에서 도는가. 그러면 받아쓰기와 그래픽카드를 나눠 쓴다
    local: bool = False
    default_url: str = ""


Transport = Callable[[str, dict[str, str], dict[str, Any]], tuple[int, str]]


def _http_post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        raise NetworkError(f"연결하지 못했습니다: {error.reason}") from error
    except TimeoutError as error:
        raise NetworkError("응답이 너무 오래 걸립니다.") from error


def _http_post_stream(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    on_line: Callable[[str], None],
) -> tuple[int, str]:
    """답을 **줄 단위로 흘려 받는다.** 다 오기를 기다리지 않는다.

    Ollama 는 `stream: true` 면 NDJSON 을 한 줄씩 내려 준다. 그것을 받는 대로
    세면 「30줄 중 17줄 왔습니다」 를 말할 수 있다.

    이게 없으면 1~3분 동안 화면에 아무 표시도 못 한다. 그동안 사용자는 멈춘
    것인지 도는 것인지 알 수가 없어서 창을 껐다 켠다. 껐다 켜면 처음부터다.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            if response.status != 200:
                return response.status, response.read().decode("utf-8", errors="replace")
            for 날것 in response:
                줄 = 날것.decode("utf-8", errors="replace").strip()
                if 줄:
                    on_line(줄)
            return 200, ""
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        raise NetworkError(f"연결하지 못했습니다: {error.reason}") from error
    except TimeoutError as error:
        raise NetworkError("응답이 너무 오래 걸립니다.") from error


class Provider:
    """공급자 공통 뼈대."""

    info: ProviderInfo

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        transport: Transport | None = None,
        url: str = "",
    ):
        self.api_key = api_key.strip()
        self.model = model.strip() or self.info.default_model
        self.url = url.strip() or self.info.default_url
        self._post = transport or _http_post

    def translate(self, prompt: str) -> str:
        raise NotImplementedError

    def unload(self) -> None:
        """다 쓰고 그래픽카드를 비운다. 내 컴퓨터에서 도는 것만 할 일이 있다."""
        return None

    def _raise_for_status(self, status: int, body: str) -> None:
        if status == 200:
            return
        if status in (401, 403):
            raise AuthFailed("API 키가 올바르지 않습니다. 키를 다시 확인해 주세요.")
        if status == 429:
            raise RateLimited("사용 한도에 걸렸습니다. 잠시 뒤에 다시 시도합니다.")
        if status >= 500:
            raise NetworkError(f"서버가 응답하지 않습니다 (HTTP {status}).")
        raise ProviderError(f"HTTP {status}: {body[:300]}")


class GeminiProvider(Provider):
    """Google AI Studio 키. 일본어→한국어가 가장 자연스럽다."""

    info = ProviderInfo(
        id="gemini",
        name="Google Gemini",
        needs_key=True,
        key_url="https://aistudio.google.com/apikey",
        note="카드 등록 없이 키를 받는다. 일본어→한국어가 가장 자연스럽다",
        default_model="gemini-2.0-flash",
    )

    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    # 번역 작업인데도 성인 표현이 있으면 막히는 일이 있어 차단 기준을 낮춘다.
    # 그래도 서버가 자체로 막는 경우가 있고, 그때는 복붙으로 넘어간다.
    SAFETY = [
        {"category": c, "threshold": "BLOCK_NONE"}
        for c in (
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        )
    ]

    def translate(self, prompt: str) -> str:
        # 키를 주소에 붙이지 않는다. 주소는 오류 문구나 기록에 통째로 남을 수
        # 있어서, 한 번 새면 사용자 키가 그대로 드러난다. 헤더로 보낸다
        url = self.ENDPOINT.format(model=self.model)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "safetySettings": self.SAFETY,
            "generationConfig": {"temperature": 0.3},
        }
        status, body = self._post(url, {"x-goog-api-key": self.api_key}, payload)
        self._raise_for_status(status, body)

        data = json.loads(body)
        candidates = data.get("candidates") or []
        if not candidates:
            # 후보가 아예 없으면 프롬프트 단계에서 막힌 것이다
            raise Refused("내용 때문에 거절당했습니다.")

        candidate = candidates[0]
        if candidate.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise Refused("내용 때문에 거절당했습니다.")

        parts = candidate.get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if not text.strip():
            raise Refused("빈 답이 왔습니다. 거절당했을 수 있습니다.")
        return text


class OpenAICompatProvider(Provider):
    """OpenAI와 같은 모양의 API를 쓰는 곳들. 주소와 모델만 다르다."""

    endpoint = ""

    def _where(self) -> str:
        return self.url or self.endpoint

    def translate(
        self, prompt: str, system: str = "",
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        # 흘려받기는 Ollama 쪽에서만 한다. 여기서는 인자만 받아 두고 넘어간다
        메시지 = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        payload = {
            "model": self.model,
            "messages": 메시지,
            "temperature": 0.3,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        status, body = self._post(self._where(), headers, payload)
        self._raise_for_status(status, body)

        data = json.loads(body)
        choices = data.get("choices") or []
        if not choices:
            raise Refused("빈 답이 왔습니다.")

        message = choices[0].get("message") or {}
        text = (message.get("content") or "").strip()
        if not text:
            raise Refused("빈 답이 왔습니다. 거절당했을 수 있습니다.")
        return text


class GroqProvider(OpenAICompatProvider):
    """매우 빠르다. 번역 품질은 Gemini보다 떨어진다."""

    info = ProviderInfo(
        id="groq",
        name="Groq",
        needs_key=True,
        key_url="https://console.groq.com/keys",
        note="아주 빠름. 품질은 조금 아쉬움",
        default_model="llama-3.3-70b-versatile",
    )
    endpoint = "https://api.groq.com/openai/v1/chat/completions"


class OpenRouterProvider(OpenAICompatProvider):
    """모델이 여럿 있어 골라 쓸 수 있다."""

    info = ProviderInfo(
        id="openrouter",
        name="OpenRouter",
        needs_key=True,
        key_url="https://openrouter.ai/keys",
        note="여러 모델을 골라 쓸 수 있음",
        default_model="meta-llama/llama-3.3-70b-instruct:free",
    )
    endpoint = "https://openrouter.ai/api/v1/chat/completions"


class LocalProvider(OpenAICompatProvider):
    """내 컴퓨터에서 도는 번역 모델.

    거절이라는 것이 없다. 한도도 키도 인터넷도 없다. 성인물 번역이 막히는 문제를
    뿌리에서 없애는 유일한 길이다.

    대신 **받아쓰기와 같은 그래픽카드를 쓴다.** 12GB 에 `large-v3` 와 번역 모델을
    같이 올리면 터진다. 그래서 로컬 번역을 고르면 받아쓰기 모델을 먼저 내린다.
    그 규칙은 `job.Pipeline` 에 있다.
    """

    def _raise_for_status(self, status: int, body: str) -> None:
        if status == 404:
            raise ProviderError(
                f"'{self.model}' 모델이 없습니다.\n"
                f"명령 프롬프트에서 `ollama pull {self.model}` 을 먼저 해 주세요."
            )
        super()._raise_for_status(status, body)

    def translate_split(
        self, rules: str, data: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """지시문과 알맹이를 **따로** 보낸다.

        `system` 에 규칙, `user` 에 번역할 줄만 넣는다. 작은 모델은 한 덩어리로
        주면 둘을 구분하지 못한다.

        실제로 겪었다. 7B 모델이 우리 지시문을 번역할 내용으로 알고 「1. 형식 —
        이게 제일 중요함 / 2. みもりあいの, 涼花みなせ / 3. 1번부터 30번까지…」
        처럼 **규칙 문장에 번호를 매겨서** 되돌려 줬다. 번호가 1부터라 읽는 쪽은
        멀쩡한 답으로 알고 담았고, 정작 일본어 대사는 손도 안 댄 채였다.

        이 메서드가 있는 공급자에게만 나눠 보낸다. 밖의 AI 는 한 덩어리로 줘도
        헷갈리지 않으므로 그대로 둔다.
        """
        return self.translate(data, system=rules, on_progress=on_progress)

    def translate(
        self, prompt: str, system: str = "",
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        try:
            return super().translate(prompt, system=system, on_progress=on_progress)
        except NetworkError as error:
            # 안 켜져 있는 것이 가장 흔하다. 그것부터 말해 준다
            raise NetworkError(
                f"{self.info.name} 에 연결하지 못했습니다. 켜져 있는지 확인해 주세요.\n"
                f"({self._where()})\n"
                f"자세히: {error}"
            ) from error


# 글자 하나가 대략 몇 토큰인가. 일본어·한국어는 영어보다 나쁘다.
# 넉넉히 잡는 편이 안전하다 — 모자라면 조용히 잘리고, 남으면 조금 느릴 뿐이다
_글자당토큰 = 1.2

# 답도 원문만큼 나온다. 한국어가 일본어보다 길어질 때가 있어 조금 더 본다
_답배수 = 1.3

# 창을 넓히는 데도 VRAM 이 든다. 32,768 은 Qwen2.5 의 네이티브 최대이기도 하다.
# 그보다 넓히면 VRAM 이 남더라도 모델이 제대로 못 쓴다
MAX_NUM_CTX = 32768

# Ollama 가 기본으로 쓰는 창. 우리 묶음에는 턱없이 모자라다
OLLAMA_DEFAULT_CTX = 4096


# 윈도우와 화면 출력이 미리 먹는 몫. 4K 120Hz 는 이만큼 쓴다
_화면몫GB = 1.5

# 연산용 그래프 버퍼. 모델 무게와 KV 캐시 말고 따로 드는 것
_버퍼GB = 0.8


def 그래픽카드에_들어가나(모델GB: float, 창: int, vram_gb: float) -> tuple[bool, float]:
    """이 모델을 이 창으로 올리면 그래픽카드에 들어가는가. `(들어감, 필요한GB)`.

    안 들어가면 Ollama 는 **말없이 일부 층을 CPU 로 내린다.** 오류가 아니라
    그냥 몇 배 느려지므로, 사용자는 원래 이런 건 줄 안다.

    KV 캐시는 창 크기에 정비례한다. 토큰당 얼마인지는 모델마다 다르지만,
    파일 크기로 어림잡을 수 있다 — 큰 모델이 층도 많고 헤드도 많다.
    Qwen2.5 로 재 보면 7B(4.7GB)가 토큰당 56KiB, 14B(9GB)가 192KiB 다.
    """
    if vram_gb <= 0 or 모델GB <= 0:
        return True, 0.0
    # 파일 크기에 따라 토큰당 KV 를 어림한다. 위 두 점을 잇는 정도로 충분하다
    토큰당KiB = 56.0 * (모델GB / 4.7) ** 1.8
    kvGB = 창 * 토큰당KiB * 1024 / (1024 ** 3)
    필요 = 모델GB + kvGB + _버퍼GB
    return 필요 <= (vram_gb - _화면몫GB), 필요


# 이름에 이런 조각이 들어 있으면 번역용으로 고르지 않는다.
#
# `-vl` 은 그림을 보는 모델이다. 같은 이름·같은 크기라도 글만 하는 판보다
# **지시를 훨씬 못 따른다.** 실제로 `qwen2.5-vl-abliterated:7b` 이 우리 지시문을
# 번역할 내용으로 알고 규칙 문장에 번호를 매겨서 되돌려 줬다.
#
# 임베딩 모델은 아예 글을 못 만든다. 크기만 보고 고르면 이런 것이 뽑힌다.
번역용이_아닌_모델 = ("-vl", "vision", "embed", "bge", "clip", "rerank", "moondream", "llava")


def 번역용인가(이름: str) -> bool:
    """이 모델을 번역에 써도 되는가."""
    낮춘것 = str(이름 or "").lower()
    return not any(조각 in 낮춘것 for 조각 in 번역용이_아닌_모델)


def pick_local_model(
    sizes: dict[str, float], vram_gb: float | None = None, prefer: str = ""
) -> str:
    """이미 받아 둔 것 중 쓸 만한 모델 하나를 고른다. 없으면 빈 값.

    사용자가 「직접 복붙」을 골라 두었으면 설정에 모델 이름이 없다. 그런데도
    "내 컴퓨터 AI로 번역" 단추는 눌려야 한다. 그래서 여기서 알아서 고른다.

    **그래픽카드에 들어가는 것 중 가장 큰 것**을 고른다. 큰 쪽이 번역이 낫고,
    들어가지 않으면 조용히 CPU 로 떨어져 몇 배 느려지기 때문이다.
    VRAM 을 못 재면 다 들어가는 것으로 보고 가장 큰 것을 고른다 —
    사용자가 자기 컴퓨터에 받아 둔 것이니 못 돌릴 것을 받아 두진 않았을 것이다.
    """
    if not sizes:
        return ""

    def 들어감(이름: str) -> bool:
        if vram_gb is None or vram_gb <= 0:
            return True
        return 그래픽카드에_들어가나(sizes[이름], MAX_NUM_CTX, vram_gb)[0]

    # 사용자가 골라 둔 것은 존중한다. 다만 번역용이 아닌 것(그림 모델·임베딩)은
    # 골라 둔 것이라도 쓰지 않는다 — 고를 때 그런 것인 줄 몰랐을 뿐이다
    if prefer and 번역용인가(prefer):
        같은것 = [이름 for 이름 in sizes if _같은모델(이름, prefer)]
        if 같은것 and 들어감(같은것[0]):
            return 같은것[0]

    쓸만한것 = {이름: 크기 for 이름, 크기 in sizes.items() if 번역용인가(이름)}
    후보 = [이름 for 이름 in 쓸만한것 if 들어감(이름)]
    if 후보:
        return max(후보, key=lambda 이름: 쓸만한것[이름])
    # 하나도 안 들어가면 그나마 가장 작은 것. 느려도 도는 편이 낫다
    if 쓸만한것:
        return min(쓸만한것, key=lambda 이름: 쓸만한것[이름])
    # 번역용이 하나도 없다. 그래도 뭔가는 줘야 화면이 「받기」를 띄운다
    return ""


def _같은모델(a: str, b: str) -> bool:
    """`qwen2.5` 와 `qwen2.5:latest` 는 같은 것이다."""
    풀기 = lambda x: x if ":" in x else f"{x}:latest"  # noqa: E731
    return 풀기(str(a).strip()) == 풀기(str(b).strip())


def 필요한창(prompt: str) -> int:
    """이 프롬프트를 자르지 않고 넣으려면 창이 얼마나 넓어야 하는가.

    Ollama 는 창이 모자라면 **말없이 프롬프트 앞부분을 잘라낸다.** 하필 앞이
    「번호를 다시 매기지 마라」 같은 형식 규칙이라, 잘리면 번호가 1부터 다시
    매겨져서 돌아온다. 그러면 자막이 통째로 어긋난다.

    묶음 하나가 5,000자 남짓이니 들어가는 것만 6,000토큰이고, 한국어 답까지
    같은 창에 들어가야 해서 실제로는 1만 토큰이 넘는다. 기본값 4,096 으로는
    될 리가 없다.

    2 의 거듭제곱으로 올린다. 어중간한 값은 모델이 좋아하지 않는다.
    """
    쓸것 = 필요토큰(prompt)
    창 = OLLAMA_DEFAULT_CTX
    while 창 < 쓸것 and 창 < MAX_NUM_CTX:
        창 *= 2
    return min(창, MAX_NUM_CTX)


def 필요토큰(prompt: str) -> int:
    """이 프롬프트에 들어가는 것과 나오는 것을 합쳐 몇 토큰인가.

    `필요한창` 은 이 값을 2의 거듭제곱으로 올려 준다. 올리기 **전** 값이라
    한도에 얼마나 여유가 있는지 볼 때는 이쪽을 봐야 한다.
    """
    return int(len(prompt) * _글자당토큰 * (1 + _답배수)) + 512


class OllamaProvider(LocalProvider):
    """가장 손이 덜 가는 로컬 실행기. 설치하고 켜 두면 끝이다.

    **네이티브 `/api/chat` 으로 부른다.** OpenAI 호환 창구(`/v1/chat/completions`)
    로는 `num_ctx` 를 넘길 수가 없어서, 창이 기본값 4,096 에 묶인다. 우리 묶음은
    그 두세 배라 넣는 족족 앞이 잘려 나갔다.
    """

    def _where(self) -> str:
        """설정에 무엇이 들어 있든 네이티브 창구로 보낸다.

        설정에는 예전부터 `/v1/chat/completions` 가 들어 있다. 사용자가 그것을
        고친 적이 없어도 우리는 `/api/chat` 으로 가야 한다.
        """
        바탕 = (self.url or self.endpoint).strip()
        for 꼬리 in ("/v1/chat/completions", "/v1", "/api/chat", "/api/generate"):
            if 바탕.endswith(꼬리):
                바탕 = 바탕[: -len(꼬리)]
                break
        return 바탕.rstrip("/") + "/api/chat"

    def translate(
        self, prompt: str, system: str = "",
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """`on_progress` 를 주면 답을 흘려 받으면서 그때까지 온 것을 넘겨준다.

        1~3분 동안 화면에 아무 표시도 없으면 사용자는 멈춘 줄 알고 창을 껐다
        켠다. 껐다 켜면 처음부터다.
        """
        창 = 필요한창(system + prompt)
        메시지 = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        payload = {
            "model": self.model,
            "messages": 메시지,
            "stream": bool(on_progress),
            "options": {
                "temperature": 0.3,
                "num_ctx": 창,
                # 답이 도중에 끊기면 뒷줄이 통째로 빈다. 창이 허락하는 만큼 쓴다
                "num_predict": -1,
            },
            # 묶음 여럿을 잇달아 보낸다. 사이마다 모델을 내렸다 올리면
            # 그것만으로 몇 분이 날아간다
            "keep_alive": "30m",
        }
        try:
            if on_progress:
                글, status, body = self._흘려받기(payload, on_progress)
            else:
                status, body = self._post(self._where(), {}, payload)
                글 = ""
        except NetworkError as error:
            raise NetworkError(
                f"{self.info.name} 에 연결하지 못했습니다. 켜져 있는지 확인해 주세요.\n"
                f"({self._where()})\n"
                f"자세히: {error}"
            ) from error
        self._raise_for_status(status, body)

        if not on_progress:
            data = json.loads(body)
            글 = ((data.get("message") or {}).get("content") or "").strip()
        글 = 글.strip()
        if not 글:
            raise Refused("빈 답이 왔습니다.")
        return 글

    def _흘려받기(
        self, payload: dict[str, Any], on_progress: Callable[[str], None]
    ) -> tuple[str, int, str]:
        """NDJSON 을 받아 붙이면서 그때까지 온 글을 알려 준다."""
        모인것: list[str] = []

        def 한줄(줄: str) -> None:
            try:
                조각 = json.loads(줄)
            except ValueError:
                return
            글조각 = ((조각.get("message") or {}).get("content") or "")
            if 글조각:
                모인것.append(글조각)
                on_progress("".join(모인것))

        status, body = _http_post_stream(self._where(), {}, payload, 한줄)
        return "".join(모인것), status, body

    def unload(self) -> None:
        """번역이 끝났으면 그래픽카드를 비운다.

        Ollama 는 마지막으로 쓴 뒤에도 한동안 모델을 붙잡고 있다. 그대로 두면
        다음 받아쓰기가 남은 VRAM 으로 돌다가 터지거나 CPU 로 떨어진다.
        `keep_alive: 0` 이 "지금 내려라" 라는 뜻이다.

        실패해도 넘어간다. 못 내렸다고 자막을 못 만들면 안 된다.
        """
        try:
            self._post(
                self._where(),
                {},
                {"model": self.model, "messages": [], "keep_alive": 0},
            )
        except Exception:
            pass

    info = ProviderInfo(
        id="ollama",
        name="Ollama (내 컴퓨터)",
        needs_key=True,  # 키 대신 주소를 받는다
        key_url="https://ollama.com/download",
        note="거절 없음. 키도 인터넷도 필요 없음. 받아쓰기는 잠시 멈춤",
        # 12GB 에서 받아쓰기 모델을 내리고 돌리면 들어간다.
        # 일본어→한국어는 Qwen 계열이 가장 낫다
        default_model="qwen2.5:14b",
        local=True,
        default_url="http://localhost:11434/v1/chat/completions",
    )
    endpoint = "http://localhost:11434/v1/chat/completions"


class LMStudioProvider(LocalProvider):
    """이미 LM Studio 를 쓰고 있으면 이쪽. 서버를 켜 두어야 한다."""

    info = ProviderInfo(
        id="lmstudio",
        name="LM Studio (내 컴퓨터)",
        needs_key=True,
        key_url="https://lmstudio.ai/",
        note="LM Studio 의 로컬 서버를 켜 두어야 함",
        default_model="",  # 올려 둔 모델을 그대로 쓴다
        local=True,
        default_url="http://localhost:1234/v1/chat/completions",
    )
    endpoint = "http://localhost:1234/v1/chat/completions"


class ManualProvider(Provider):
    """키 없이 채팅창에 직접 붙여넣는 방식.

    호출할 곳이 없으므로 번역하지 않는다. UI가 이 공급자를 보면 프롬프트를
    보여 주고 사용자가 붙여넣기를 기다린다.
    """

    info = ProviderInfo(
        id="manual",
        name="직접 복붙",
        needs_key=False,
        key_url="",
        note="키가 필요 없다. 느리지만 막히지 않는다",
    )

    def translate(self, prompt: str) -> str:
        raise ProviderError("복붙 방식은 사용자가 직접 붙여넣습니다.")


REGISTRY: dict[str, type[Provider]] = {
    cls.info.id: cls
    for cls in (
        GeminiProvider,
        GroqProvider,
        OpenRouterProvider,
        OllamaProvider,
        LMStudioProvider,
        ManualProvider,
    )
}

# 보여 줄 차례. 키 없이 바로 되는 것이 앞, 거절이 없는 것이 그다음이다
_ORDER = ("manual", "ollama", "lmstudio", "gemini", "groq", "openrouter")


def available() -> list[ProviderInfo]:
    """UI에 보여 줄 목록. 복붙을 맨 앞에 둔다 — 키 없이 바로 되는 유일한 길이다."""
    infos = [cls.info for cls in REGISTRY.values()]
    return sorted(infos, key=lambda i: (_ORDER.index(i.id) if i.id in _ORDER else 99))


def is_local(provider_id: str) -> bool:
    """이 공급자가 내 컴퓨터에서 도는가. 그러면 그래픽카드를 나눠 써야 한다."""
    cls = REGISTRY.get(provider_id)
    return bool(cls is not None and cls.info.local)


def create(
    provider_id: str,
    *,
    api_key: str = "",
    model: str = "",
    transport: Transport | None = None,
    url: str = "",
) -> Provider:
    try:
        cls = REGISTRY[provider_id]
    except KeyError:
        raise ProviderError(f"모르는 번역 공급자입니다: {provider_id}") from None
    return cls(api_key=api_key, model=model, transport=transport, url=url)
