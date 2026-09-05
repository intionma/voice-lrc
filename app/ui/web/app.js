// 화면. 파이썬이 하는 일은 전부 pywebview.api 를 거친다.
//
// 상태는 짧은 간격으로 물어보며 그린다. 파이썬이 화면을 직접 건드리는 것보다
// 단순하고, 받아쓰기가 오래 돌아도 화면이 얼지 않는다.
//
// 번역은 파일 하나씩이 아니라 대기열 전체를 훑는다. 이 화면은 `prompt()` 와
// `submit()` 만 되풀이하면 되고, 지금 몇 번 파일인지 알 필요가 없다.

const $ = (id) => document.getElementById(id);
const api = () => (window.pywebview && window.pywebview.api) || null;

// **홈 화면이 없다.** 「무엇을 할까요?」 에 고를 것이 하나뿐이었다. 켤 때마다
// 카드를 한 번 더 누르게 만들 이유가 없어서, 켜면 바로 일감 앞에 선다.
// **번역도 화면이 아니라 작업 화면 오른쪽 칸이다.** 왼쪽 나무가 앱 뼈대라서,
// 트랙을 누르면 그 트랙이 지금 어느 단계든 오른쪽에서 이어 간다
const 화면 = {
  // 첫 실행에만. 머리띠(제목·뒤로·설정)도 없이 통째로 차지한다
  onb: { el: "screen-onb", title: "", back: false, 머리없음: true },
  work: { el: "screen-work", title: "일본어 음성 → 한국어 자막", back: false },
  titles: { el: "screen-titles", title: "제목 번역", back: true },
  settings: { el: "screen-settings", title: "설정", back: true },
};
let 지금 = "work";
let 마지막상태 = { jobs: [], providers: [], settings: {}, queue: { total: 0 } };
let 고른공급자 = "manual";
let 지금프롬프트 = "";
// 지시문 없는 판. 기계 번역기에 넣을 때 쓴다
let 지금원문 = "";
// 지금 검수할 수 있는 묶음. 없으면 null
let 검수할것 = null;
let 지난그림 = "";

// 사용자가 치던 품번. 다시 그려도 날아가지 않게 들고 있는다
const 치던품번 = {};

// 펼쳐 둔 검사표. **다시 그려도 살아 있어야 한다.**
//
// 화면은 0.6초마다 다시 그린다. 그래픽카드 값이 늘 조금씩 움직여서 실제로
// 거의 매번 다시 그려진다. 펼친 것을 마디 안에만 들고 있으면 누른 지 한
// 순간 만에 도로 접힌다 — 누르는 쪽에서는 **아무 반응이 없는 것으로 보인다.**
// 치던 품번과 같은 까닭, 같은 길이다
const 펼친검사표 = {};

// 실패한 까닭을 화면에 띄운다.
//
// 창구는 왜 안 됐는지 늘 말해 주는데, 화면이 그것을 버리는 자리가 여럿 있었다.
// 그러면 눌러도 아무 일이 없는 것처럼 보인다. 이 저장소에서 제일 나쁘게 치는
// 모양이다
// 화면 어디에 있든 보이는 자리. `#notice` 는 작업 화면 **안**에 있어서,
// 설정이나 제목 번역 화면에서 터진 것은 숨은 칸에 적히고 끝났다 — 사용자에게는
// 또 「눌러도 반응이 없다」 였다. 그래서 화면 밖(body)에 하나 띄운다
let 뜬말시계 = null;

function 뜬말(글, 어떤것) {
  let box = document.getElementById("float-notice");
  if (!box) {
    box = document.createElement("div");
    box.id = "float-notice";
    box.className = "notice bad float-notice";
    document.body.appendChild(box);
  }
  box.textContent = 글;
  box.className = `notice ${어떤것 || "bad"} float-notice`;
  box.hidden = false;
  if (뜬말시계) clearTimeout(뜬말시계);
  // 스스로 사라진다. 안 사라지면 화면을 가린 채 남아서 다음 일을 방해한다
  뜬말시계 = setTimeout(() => { box.hidden = true; }, 8000);
}

function 안된까닭(결과, 기본말) {
  const 글 = (결과 && 결과.message) || 기본말 || "하지 못했습니다";
  // 작업 화면에 있으면 늘 쓰던 자리에도 적는다. 거기가 눈이 가는 자리다
  const box = $("notice");
  if (box) {
    box.hidden = false;
    box.className = "notice bad";
    box.textContent = 글;
  }
  뜬말(글);
}

// 단추가 터져도 화면이 조용히 죽지 않게 감싼다.
//
// 창구가 뜻밖에 터지면 그 자리에서 약속이 깨지는데, 아무 데도 안 나온다.
// 사용자는 눌러도 아무 일이 없는 것만 본다. `새로고침` 은 이미 이렇게 막고
// 있었는데 단추들은 맨몸이었다
function 안전하게(할일, 기본말) {
  const 부를것 = 할일;
  return async (...것들) => {
    try {
      return await 부를것(...것들);
    } catch (e) {
      안된까닭({ message: (기본말 || "하지 못했습니다") + ": " + (e && e.message ? e.message : e) });
    }
  };
}

function 보이기(이름) {
  // 없는 화면 이름이 들어오면 조용히 둔다. 「번역」 이 화면에서 오른쪽 칸으로
  // 옮겨 가면서, 남아 있던 자리에서 부르면 여기서 터졌다
  if (!화면[이름]) return;
  지금 = 이름;
  for (const [key, info] of Object.entries(화면)) {
    $(info.el).hidden = key !== 이름;
  }
  // 앱 이름(`#brand`)은 그대로 두고 **지금 어느 화면인지만** 바꾼다.
  // 이름이 화면마다 바뀌면 그건 이름이 아니라 이정표다
  $("title").textContent = 화면[이름].title;
  if ($("bar-sep")) $("bar-sep").hidden = !화면[이름].title;
  $("back").hidden = !화면[이름].back;
  $("to-settings").hidden = 이름 === "settings" || !!화면[이름].머리없음;
  // 첫 실행 화면은 머리띠 없이 통째로 쓴다.
  // **`querySelector` 를 안 쓴다** — 화면 시험의 가짜 DOM 은 id 로만 찾는다
  if ($("topbar")) $("topbar").hidden = !!화면[이름].머리없음;
  if (이름 === "settings") 설정열기();
  if (이름 === "titles") 제목칸그리기();
  if (이름 === "work") 다시그리기();   // 단계 표시가 옛 화면 기준으로 남지 않게
  if (이름 !== "work") 소리멈춤();
}

// ---- 상태 ----

// 그래픽카드가 지금 얼마나 차 있는지. **늘 보인다.**
//
// 자리가 모자라면 받아쓰기가 파이썬 오류도 없이 통째로 죽는다. 그런데 지금
// 얼마나 쓰고 있는지 알아볼 곳이 앱 어디에도 없었다. 번역 모델이 눌러앉아
// 있는지, 받아쓰기 모델이 아직 안 내려갔는지를 여기서 본다.
//
// 못 재는 컴퓨터(그래픽카드가 없거나 nvidia-smi 가 없는)에서는 통째로 숨긴다.
// 모르면서 0GB 라고 하는 것이 안 보여 주는 것보다 나쁘다.
function VRAM그리기(것) {
  const 칸 = $("vram");
  if (!칸) return;
  if (!것 || !것.total_gb) {
    칸.hidden = true;
    return;
  }
  칸.hidden = false;
  const 찬비율 = Math.max(0, Math.min(1, (것.used_gb || 0) / 것.total_gb));
  const 막대 = $("vram-fill");
  막대.style.width = Math.round(찬비율 * 100) + "%";
  // 노랑이면 슬슬 위험, 빨강이면 받아쓰기 모델(약 5GB)이 이미 안 들어간다
  막대.className = "vram-fill"
    + (것.free_gb < 5 ? " bad" : 찬비율 > 0.6 ? " warn" : "");

  const 일함 = 것.util_pct ? ` · ${것.util_pct}%` : "";
  $("vram-text").textContent = `${것.used_gb} / ${것.total_gb}GB${일함}`;
  칸.title = 것.free_gb < 5
    ? `남은 자리 ${것.free_gb}GB. 받아쓰기 모델(약 5GB)이 안 들어갑니다`
    : `남은 자리 ${것.free_gb}GB`;
}

async function 새로고침() {
  const 창구 = api();
  if (!창구) return;
  try {
    마지막상태 = await 창구.state();
    VRAM그리기(마지막상태.gpu);
    // **처음 켰으면 길부터 묻는다.** 설정을 나열하면 아무도 안 읽는다.
    //
    // 한 번만 들어간다. 상태는 0.6초마다 다시 오는데, 그때마다 여기로 오면
    // **고르는 중에 1단계로 되돌아가서** 영영 다음으로 못 간다
    if (마지막상태.처음켬 && !온보딩들어감) {
      온보딩들어감 = true;
      온보딩붙이기();
      온보딩단계(1);
      보이기("onb");
      return;
    }
    if (온보딩들어감 && 마지막상태.처음켬) return;
    작업그리기();
    주단추맞추기(false);
    // 감시가 넣은 것은 **창구 쪽에서** 일어난다. 화면은 상태를 받아 보고서야
    // 안다 — 그리지 않으면 답이 들어갔는데 아무 표시도 안 난다
    감시그리기();
  } catch (e) {
    // 그리다 터지면 화면이 조용히 빈 채로 남는다. 파일을 넣어도 아무것도 안
    // 보이는데 오류는 어디에도 안 나온다. 그러지 않게 띄운다
    const box = $("notice");
    box.hidden = false;
    box.className = "notice bad";
    box.textContent = "화면을 그리다 문제가 생겼습니다: " + (e && e.message ? e.message : e);
  }
}

function 작업그리기() {
  const { jobs, busy, notice, queue } = 마지막상태;

  // 사용자가 칸에 무언가 치고 있으면 다시 그리지 않는다.
  // 0.6초마다 그리면 치던 글자가 통째로 날아간다
  // **글상자도 봐야 한다.** 여태 `INPUT` 만 봐서, 긴 글을 치는 자리가
  // 0.6초마다 날아갈 수 있었다
  const 치는칸 = document.activeElement && document.activeElement.tagName;
  const 지금친다 = 치는칸 === "INPUT" || 치는칸 === "TEXTAREA";
  const 그림 = JSON.stringify(마지막상태);
  if (지금친다 || 그림 === 지난그림) return;
  지난그림 = 그림;

  // **여기서도 그린다.** 번역 칸이 안 떠 있을 때도 트랙 진행은 바뀐다.
  // 번역칸그리기 는 검수 창이 닫혀 있으면 일찍 돌아가므로 거기만 믿으면
  // 목록이 낡은 채로 남는다
  병렬그리기(마지막상태);

  $("notice").hidden = !notice;
  $("notice").textContent = notice || "";

  // 그래픽카드를 못 썼으면 **그 자리에서** 고칠 길을 내민다. 설정 깊은 곳에
  // 두고 오류 문구를 읽어 찾아오라고 할 수는 없다
  const 카드고칠까 = !!마지막상태.gpu_고칠까;
  if ($("gpu-fix")) $("gpu-fix").hidden = !카드고칠까;

  // 새 판이 **확실히** 있을 때만 말한다. 0 은 「최신」이 아니라
  // 「모르거나 최신」이라, 그것으로 경고를 띄우면 고칠 수도 없는 딱지가 된다
  const 새판 = Number(마지막상태.새판 || 0);
  if ($("new-ver")) {
    $("new-ver").hidden = 새판 < 1;
    if (새판 >= 1) {
      $("new-ver-what").textContent = `새 판이 ${새판}개 나왔습니다.`;
    }
  }
  $("stop").hidden = !busy;
  // 누른 자리에서 무슨 일이 벌어지는지 보인다 — 줄의 「멈추는 중…」 과 같다
  // 「멈추기」 가 홀로 좌상단에 있어서 무엇을 멈추는지 안 붙어 있었다
  $("stop").textContent = 마지막상태.stopping ? "멈추는 중…" : "전부 멈추기";
  $("stop").disabled = !!마지막상태.stopping;
  // **빈 목록에서는 숨긴다.** 「먼저 음원을 넣으세요」 라고 적힌 꺼진 단추가
  // 떠 있었다 — 같은 화면의 드롭존과 「+ 음원 넣기」 가 이미 그 말을 한다.
  // 같은 행동을 세 군데서 말하면 어느 것을 눌러야 하는지 헷갈린다
  $("start").hidden = busy || jobs.length === 0;
  // 비울 것이 없으면 「비우기」 도 없다
  if ($("clear")) $("clear").hidden = jobs.length === 0;

  // 눌리지 않는 이유를 단추가 직접 말한다. 회색으로만 두면 고장인 줄 안다.
  // 「전부」 는 지름길이다 — 기본은 표에서 고르고 명령하는 것
  const 할것 = jobs.filter((j) => j.stage === "대기").length;
  $("start").disabled = 할것 === 0;
  $("start").textContent =
    jobs.length === 0 ? "먼저 음원을 넣으세요"
    : 할것 === 0 ? "받아쓰기 끝"
    : `아래 전부 받아쓰기 (${할것}개)`;
  $("start").title = "작품 목록에 있는 것을 위에서부터 차례로 받아씁니다."
    + " 몇 개만 하려면 오른쪽 표에서 골라 명령하세요.";

  // **동작을 가르는 상태는 화면에 떠 있어야 한다** (규칙 5).
  //
  // 「고른 것만」 판으로 돌고 있으면, 도는 중에 끌어다 놓은 작품을 **안
  // 잡는다.** 그런데 그 표시가 어디에도 없어서, 사용자는 「받아쓰는 중에
  // 작품을 넣으면 멈췄다가 다시 눌러야 한다」 를 스스로 알아내야 했다
  const 알림 = $("only-note");
  if (알림) {
    const 고른것만 = busy && (마지막상태.only || 0) > 0;
    알림.hidden = !고른것만;
    알림.textContent = 고른것만
      ? `고른 ${마지막상태.only}개만 받아쓰는 중입니다.`
        + " 지금 새로 넣는 음원은 이 판에 안 잡힙니다 — 넣은 뒤 그 줄의"
        + " 「받아쓰기」 를 누르면 줄에 붙습니다."
      : "";
  }

  // 번역으로 가는 단추는 여기 없다. 작품 머리와 나무가 그 길이다 —
  // 전역 단추는 「어느 작품의 무엇을 번역하는지」 를 말하지 못한다

  // 파일이 있으면 드롭존을 얇게 줄여 목록에 자리를 준다.
  // 목록이 길어지면 끌어다 놓는 자리보다 트랙을 보는 것이 중요하다
  // 파일이 있으면 큰 드롭존을 통째로 치운다. 넣는 길은 사이드바 아래 띠와
  // 화면 아무 데나 끌어다 놓기가 맡는다 — 늘 크면 일하는 칸이 눌린다
  $("drop").hidden = jobs.length > 0;

  // **작품을 넘나들어 한 번에 맡기는 길.** 작품이 둘 이상일 때만 띄운다 —
  // 하나뿐이면 작품 머리의 단추와 같은 일이라 둘이 나란히 있을 까닭이 없다
  const 전부묶기 = $("copy-all-works");
  if (전부묶기) {
    const 작품들 = (마지막상태.works || []);
    const 번역할것 = 작품들.flatMap(
      (w) => (w.jobs || []).filter((j) => j.at >= 0));
    const 걸친작품 = 작품들.filter(
      (w) => (w.jobs || []).some((j) => j.at >= 0)).length;
    전부묶기.hidden = !(걸친작품 >= 2 && 번역할것.length >= 2);
    if (!전부묶기.hidden) {
      전부묶기.textContent =
        `전부 묶어서 복사 (${걸친작품}작품 · ${번역할것.length}트랙)`;
      전부묶기.title = "작품을 넘나들어 한 프롬프트로 묶습니다."
        + " 답은 번호를 보고 제 트랙으로 갈라집니다";
      전부묶기.onclick = 안전하게(
        () => 묶어서복사(번역할것.map((j) => j.index), 전부묶기),
        "묶지 못했습니다");
    }
  }

  $("tally").textContent = 세어보기(jobs);

  작품나눠그리기(마지막상태.works || []);
}

// ---- 작업 화면: 왼쪽 작품 / 오른쪽 트랙 표 ----
//
// 예전에는 작품 상자가 세로로 쌓이고 그 안에 트랙 카드가 격자로 깔렸다.
// 작품 셋에 트랙 열다섯이면 화면을 한참 굴려야 했고, 어느 작품을 보고
// 있는지도 스크롤 자리로만 알 수 있었다.

let 고른작품 = "";
// **접은 것은 나무만의 일이다.** `고른작품` 으로 접으려 하면, 「고른 것이
// 없으면 첫 작품으로」 규칙이 곧바로 도로 펴 버려서 누르는 쪽에서는 아무
// 일도 안 난 것으로 보인다. 접기는 제 값을 따로 든다
let 접은작품 = new Set();
// **번쩍임은 상태여야 한다.** 나무는 0.6 초마다 통째로 다시 그려지므로,
// 누른 그 요소에 class 를 얹어 봐야 다음 그림에서 버려진다 — 눌러도
// 아무 일이 없는 것으로 보인다. 어느 것이 번쩍이는지를 들고 있다가
// 그릴 때마다 붙인다
let 번쩍인작품 = null;
let 번쩍시계 = null;
// 오른쪽에 띄울 트랙. **왼쪽 나무가 뼈대**라서, 오른쪽은 고른 것 하나만
// 그린다. 둘 다 목록이면 같은 것이 화면에 두 번 뜨고, 「왼쪽 맥락 / 오른쪽
// 작업」 규칙이 깨진다
let 고른트랙 = -1;
// 오른쪽 칸이 무엇을 띄우고 있나. "list" 는 트랙 표, "edit" 는 검수 창
// (한 줄씩 · 통째로 · 번역이 전부 이 안의 탭이다). 번역이 딴 화면이던
// 때의 "translate" 모드는 없앴다 — 문이 두 개면 화면이 랜덤으로 보인다
let 오른쪽모드 = "list";

// ---- 할 일 필터 · 선택 · 표 ----
//
// **고르고, 명령한다.** 오른쪽은 트랙 표다 — 행마다 상태별 「다음 할 일」
// 단추가 붙고, 여러 개 고르면 액션 바가 뜬다. 전역 단추는 없다.

let 선택트랙 = new Set();     // 표에서 고른 트랙 index 들
let 마지막누른자리 = -1;       // Shift 범위 선택용 (표의 행 자리)
let 표에보인것 = [];           // 지금 표에 그려진 job 들 (행 순서)
let 할일필터 = "all";
// 빼기로 지워지는 중인 트랙. 8초 안에 되돌릴 수 있다
let 지운대기 = new Map();      // index → true
let 지운타이머 = null;
let 지운모음 = new Set();

const 할일목록 = [
  { id: "all", 이름: "전체", 고르기: () => true },
  { id: "asr", 이름: "받아쓰기 필요",
    고르기: (j) => (j.stage === "대기" && j.lines === 0) || j.stage === "실패" },
  { id: "paste", 이름: "붙여넣기 대기", 고르기: (j) => j.at >= 0 },
  { id: "warn", 이름: "봐야 할 것", 경고: true,
    고르기: (j) => !!(j.report && (j.report.findings || []).length) },
];

function 모든트랙(작품들) {
  const 난것 = [];
  작품들.forEach((w) => (w.jobs || []).forEach((j) => 난것.push({ job: j, 작품: w })));
  return 난것;
}

function 할일그리기(작품들) {
  const 칸 = $("todo-filters");
  if (!칸) return;
  칸.innerHTML = "";
  const 전부 = 모든트랙(작품들).filter((x) => !지운대기.has(x.job.index));
  할일목록.forEach((f) => {
    const 수 = f.id === "all" ? 전부.length
      : 전부.filter((x) => f.고르기(x.job)).length;
    if (f.id !== "all" && !수) return;   // 없는 것은 보여 주지 않는다
    const b = document.createElement("button");
    b.type = "button";
    b.className = "todo-b" + (할일필터 === f.id ? " on" : "");
    const 글 = document.createElement("span");
    글.textContent = f.이름;
    const 센것 = document.createElement("span");
    센것.className = "cnt" + (f.경고 ? " warn" : "");
    센것.textContent = f.경고 ? `⚠ ${수}` : String(수);
    b.append(글, 센것);
    b.onclick = () => {
      할일필터 = f.id;
      선택트랙.clear();
      보는칸 = -1;
      오른쪽모드 = "list";
      다시그리기();
    };
    칸.appendChild(b);
  });
}

function 작품나눠그리기(작품들) {
  // **빈 화면도 같은 틀이다.** 파일이 없다고 레이아웃이 통째로 달라지면
  // 첫 파일을 넣는 순간 화면이 점프한다. 왼쪽 나무는 비어 있고,
  // 오른쪽 자리에 끌어놓기 상자가 들어온다
  $("work-lay").hidden = false;
  if (!작품들.length) {
    $("work-list").innerHTML = "";
    $("work-head").innerHTML = "";
    $("work-ask").innerHTML = "";
    $("jobs").innerHTML = "";
    const 필터칸 = $("todo-filters");
    if (필터칸) 필터칸.innerHTML = "";
    return;
  }

  할일그리기(작품들);

  // 고른 것이 없어졌으면 첫 작품으로. 빈 오른쪽을 띄우면 잘못 들어온 줄 안다
  if (!작품들.some((w) => w.key === 고른작품)) 고른작품 = 작품들[0].key;
  const 지금작품 = 작품들.find((w) => w.key === 고른작품) || 작품들[0];

  // 표에 무엇을 늘어놓을지. 「전체」 면 고른 작품의 트랙, 필터면 **모든
  // 작품**에서 걸리는 트랙 — 작품 열 개가 뒤섞여도 무엇부터 할지 보인다
  const 필터 = 할일목록.find((f) => f.id === 할일필터) || 할일목록[0];
  const 걸러진 = 필터.id === "all"
    ? (지금작품.jobs || []).map((j) => ({ job: j, 작품: 지금작품 }))
    : 모든트랙(작품들).filter((x) => 필터.고르기(x.job));
  표에보인것 = 걸러진.filter((x) => !지운대기.has(x.job.index));

  // **왼쪽을 그리기 전에** 고른 트랙을 정한다. 나중에 정하면 왼쪽 나무가
  // 옛 값으로 그려져서, 오른쪽에 뜬 트랙이 왼쪽에서는 안 골라져 보인다
  const 트랙들 = (지금작품.jobs || []).filter((j) => !지운대기.has(j.index));
  const 보이는줄들 = 표에보인것.map((x) => x.job);
  // -2 는 「사용자가 일부러 푼」 상태다 — 작품을 눌렀을 때. 여기서 첫
  // 트랙을 대신 골라 주면 작품을 눌러도 선택이 안 풀리는 것처럼 보인다.
  // -1(처음)은 예전처럼 첫 트랙을 골라 준다 — 켜자마자 카드가 보여야 한다
  if (고른트랙 !== -2 &&
      !보이는줄들.some((j) => j.index === 고른트랙) &&
      !트랙들.some((j) => j.index === 고른트랙)) {
    고른트랙 = 보이는줄들.length ? 보이는줄들[0].index
      : (트랙들.length ? 트랙들[0].index : -1);
  }
  const 지금트랙 = 보이는줄들.find((j) => j.index === 고른트랙)
    || 트랙들.find((j) => j.index === 고른트랙);

  $("work-side-tally").textContent = `작품 ${작품들.length}개`;

  const 목록 = $("work-list");
  목록.innerHTML = "";
  작품들.forEach((작품) => 목록.appendChild(작품고르는칸(작품)));

  $("work-head").innerHTML = "";
  if (필터.id === "all") {
    $("work-head").appendChild(작품머리(지금작품));
  } else {
    $("work-head").appendChild(필터머리(필터, 표에보인것.length));
  }

  $("work-ask").innerHTML = "";
  if (필터.id === "all" && 지금작품.needs_id) {
    $("work-ask").appendChild(품번묻기(지금작품));
  }

  const 칸 = $("jobs");
  칸.innerHTML = "";
  // 검수 창을 띄우고 있으면 표는 접고 홀쭉한 카드만 — 드릴다운.
  // 목록 모드면 **표 + 고른 트랙의 상세 카드** (목록 + 미리보기)
  const 드릴다운 = 보는칸 >= 0;
  if (!드릴다운) {
    칸.appendChild(트랙표(표에보인것, 필터.id !== "all"));
    // 상세(뷰어)가 열려 있으면 카드를 안 얹는다. 뷰어가 Tr02 를 보이는데
    // 위에 Tr01 카드가 떠 있으면 지금 뭘 보는지 시선이 갈린다. 진행은
    // 왼쪽 나무 막대가 이미 말한다
    if (지금트랙) 칸.appendChild(작업하나(지금트랙, 지금트랙.index));
  }

  선택바그리기();
  고치는칸그리기(지금작품);
}

function 필터머리(필터, 수) {
  const head = document.createElement("header");
  head.className = "work-head";
  const info = document.createElement("div");
  info.className = "work-info";
  const title = document.createElement("div");
  title.className = "work-title";
  title.textContent = `${필터.이름} — ${수}개`;
  const sub = document.createElement("div");
  sub.className = "work-sub";
  sub.textContent = "모든 작품에서 모았습니다. 다 처리하면 이 목록이 비워집니다.";
  info.append(title, sub);
  head.appendChild(info);
  return head;
}

// ---- 트랙 표 ----

// **한 단계에 이름은 하나다.** 같은 화면에서 위쪽 셈은 「받아쓰기 전 4」,
// 왼쪽 나무는 「대기」, 표는 「번역 차례」 인데 셈은 「번역할 차례」 였다.
// 셋이 같은 것을 말하는 줄 알 방법이 없다. 이름은 여기 한 곳에서만 짓는다
const 단계이름 = {
  대기: "받아쓰기 전",
  받아쓰기: "받아쓰는 중",
  다시훑기: "다시 보는 중",
  번역: "번역할 차례",
  자막: "자막 만드는 중",
  완료: "끝남",
  건너뜀: "말이 없어 건너뜀",
  실패: "실패",
};

function 단계말(단계) { return 단계이름[단계] || 단계 || ""; }

function 상태말(job) {
  // 단추가 「멈추는 중…」 인데 옆의 상태가 「받아쓰는 중 41%」 이면
  // 둘 중 어느 쪽이 맞는 말인지 알 수 없다
  if (job.stopping) return "멈추는 중…";
  const 받는중 = job.stage === "받아쓰기" || job.stage === "다시훑기";
  if (받는중 && job.progress > 0) {
    return `${단계말(job.stage)} ${Math.round(job.progress * 100)}%`
      + (job.eta_sec > 0 ? ` · ${시간말(job.eta_sec)} 남음` : "");
  }
  if (받는중) return 단계말(job.stage);
  if (job.stage === "실패") return 단계말("실패");
  if (job.stage === "건너뜀") return 단계말("건너뜀");
  if (job.stage === "완료") return `${단계말("완료")} · ${job.lines}줄`;
  if (job.at >= 0 && (job.dots || []).length > 1) {
    return `${단계말("번역")} ${job.dots.filter(Boolean).length}/${job.dots.length} 묶음`;
  }
  if (job.at >= 0) return 단계말("번역");
  if (job.lines > 0) return `${job.lines}줄`;
  return 단계말("대기");
}

/** 상태가 곧 다음 행동. 행의 주 단추 하나가 「지금 이 트랙에 할 일」 이다 */
function 다음행동(job) {
  const 받는중 = job.stage === "받아쓰기" || job.stage === "다시훑기";
  // **시작한 것은 그 자리에서 무를 수 있어야 한다.** 예전에는 「받아쓰는 중…」
  // 이 눌리지 않는 회색 글씨였고, 멈추는 단추는 화면 맨 위에만 있었다.
  // 누른 자리에서 멀고, 무엇을 멈추는 것인지도 말하지 못했다
  // **누른 뒤에 단추가 바뀌어야 한다.** 멈추라고 해도 실제로 서기까지
  // 몇 분이 걸리는데, 그동안 단추가 「■ 멈추기」 그대로여서 눌린 줄 모르고
  // 계속 다시 눌렀다. 지금 무엇을 하고 있는지 그 자리에서 말한다
  if (job.stopping) {
    return { 글: "멈추는 중…", 모양: "q", 잠김: true };
  }
  if (받는중) {
    return { 글: "■ 멈추기", 모양: "q",
             할일: async () => { await 되돌릴수있게(() => api().stop_track(job.index)); } };
  }
  // **큐에 걸어 둔 것이 보여야 한다.** 도는 중에 다른 트랙의 「받아쓰기」 를
  // 누르면 큐에는 제대로 붙는데 줄이 그대로여서, 눌러도 아무 반응이 없는
  // 것처럼 보였다. 같은 자리를 다시 누르면 줄에서 빠진다
  if (job.asr_queued) {
    return { 글: "대기 중 · 빼기", 모양: "q",
             할일: async () => { await 되돌릴수있게(() => api().stop_track(job.index)); } };
  }
  if (job.stage === "실패") {
    return { 글: "받아쓰기 다시", 모양: "p",
             할일: async () => {
               await 되돌릴수있게(() => api().redo_transcribe(job.index));
               await 새로고침();
             } };
  }
  if (job.at >= 0) {
    const 끝 = (job.dots || []).filter(Boolean).length;
    const 다음번호 = (job.dots || []).length > 1 ? `${끝 + 1}번 묶음 ` : "";
    return { 글: `${다음번호}번역`, 모양: "p", 할일: () => 트랙열기(job) };
  }
  if (job.lines > 0) {
    return { 글: "▶ 듣고 검수", 모양: "g", 할일: () => 받아쓴것보기(job.index) };
  }
  if (job.stage === "건너뜀") return { 글: "말 없음", 모양: "q", 잠김: true };
  return { 글: "받아쓰기", 모양: "p",
           할일: async () => { 마지막상태 = await api().start_tracks([job.index]); 작업그리기(); } };
}

function 트랙표(줄들, 작품이름도) {
  const 표 = document.createElement("div");
  표.className = "ttable";

  const head = document.createElement("div");
  head.className = "trow thead";
  ["", "#", "트랙", "단계", "상태", "다음 할 일"].forEach((h) => {
    const c = document.createElement("span");
    c.textContent = h;
    // 머리에 올려도 범례가 뜬다. 점에 올리는 것을 모르는 사람도 여기는 본다
    if (h === "단계") c.title = "넣기 · 받아쓰기 · 번역 · 자막 순서. 초록 = 끝남 · 파랑 = 지금 · 빨강 = 실패 · 회색 = 아직";
    head.appendChild(c);
  });
  표.appendChild(head);

  줄들.forEach(({ job, 작품 }, 자리) => {
    표.appendChild(표트랙줄(job, 작품, 자리, 작품이름도));
  });

  if (!줄들.length) {
    const 빈것 = document.createElement("div");
    빈것.className = "ttable-empty";
    빈것.textContent = "여기 걸리는 트랙이 없습니다. 다 처리한 것입니다.";
    표.appendChild(빈것);
  }

  const 안내 = document.createElement("div");
  안내.className = "tbl-hint";
  안내.textContent =
    "더블클릭 = 열기 · Shift/Ctrl 클릭 = 여러 개 · Enter = 열기 · Del = 빼기 · 우클릭 = 메뉴";
  표.appendChild(안내);
  return 표;
}

function 표트랙줄(job, 작품, 자리, 작품이름도) {
  const row = document.createElement("div");
  row.className = "trow"
    + (선택트랙.has(job.index) ? " sel" : "")
    + (고른트랙 === job.index ? " cur" : "");
  row.id = `trow-${job.index}`;

  const cb = document.createElement("span");
  cb.className = "cb" + (선택트랙.has(job.index) ? " on" : "");
  cb.onclick = (ev) => { ev.stopPropagation(); 행선택(job, 자리, { toggle: true }); };

  const no = document.createElement("span");
  no.className = "no";
  no.textContent = String(자리 + 1);

  // **트랙 이름이 먼저다.** 예전에는 작품 이름을 앞에 붙였는데, 작품
  // 제목이 길어서 칸을 다 먹고 트랙 이름은 「…」 뒤로 잘려 나갔다.
  // 거른 목록(「받아쓰기 필요」)에서 두 줄이 똑같이 「ジト目ふたなり…」 로
  // 보여서 어느 트랙인지 가릴 수가 없었다.
  // 작품 이름은 뒤에 흐리게 붙이고, 자리가 모자라면 그쪽부터 줄어든다
  const nm = document.createElement("span");
  nm.className = "nm";
  const 트랙이름 = document.createElement("b");
  트랙이름.className = "nm-t";
  트랙이름.textContent = job.ko || job.name;
  nm.appendChild(트랙이름);
  if (작품이름도) {
    const 작품이름 = document.createElement("span");
    작품이름.className = "nm-w";
    작품이름.textContent = 작품.ko || 작품.name;
    nm.appendChild(작품이름);
  }
  nm.title = (작품이름도 ? `${작품.ko || 작품.name}\n` : "") + (job.path || job.name);

  const 상태 = document.createElement("span");
  상태.className = "st"
    + (job.stage === "실패" ? " bad" : "")
    + (job.stage === "완료" ? " ok" : "")
    + (job.stage === "받아쓰기" || job.stage === "다시훑기" ? " run" : "");
  상태.textContent = 상태말(job);
  // **왜 실패했는지를 줄에서 보여 준다.** 카드를 열어야 「뭔가 터짐」 이 보였다.
  // 열다섯 개가 실패했으면 열다섯 번 열어야 안다
  if (job.stage === "실패" && job.error) {
    const 까닭 = String(job.error).trim();
    상태.textContent = `실패 — ${까닭.length > 28 ? 까닭.slice(0, 28) + "…" : 까닭}`;
    상태.title = 까닭;
  }
  if (job.report && (job.report.findings || []).length) {
    const 표시 = document.createElement("span");
    표시.className = "tr-warn";
    표시.textContent = ` ⚠${job.report.findings.length}`;
    상태.appendChild(표시);
  }

  const 행동 = 다음행동(job);
  const 단추 = document.createElement("button");
  단추.type = "button";
  단추.className = "act " + 행동.모양;
  단추.textContent = 행동.글;
  단추.disabled = !!행동.잠김;
  단추.onclick = 안전하게(async (ev) => {
    ev.stopPropagation();
    if (행동.할일) await 행동.할일();
  }, "하지 못했습니다");

  row.append(cb, no, nm, 파이프바(job), 상태, 단추);

  row.onclick = (ev) => 행선택(job, 자리, {
    범위: ev.shiftKey, 더하기: ev.ctrlKey || ev.metaKey,
  });
  row.ondblclick = 안전하게(() => 트랙열기(job), "트랙을 열지 못했습니다");
  row.oncontextmenu = (ev) => {
    ev.preventDefault();
    // 고른 것 밖을 우클릭하면 그것 하나를 고른 것으로 본다 (탐색기와 같다)
    if (!선택트랙.has(job.index)) 행선택(job, 자리, {});
    우클릭메뉴(job, ev);
  };
  return row;
}

function 행선택(job, 자리, 방식) {
  if (방식.범위 && 마지막누른자리 >= 0) {
    const [a, b] = [Math.min(마지막누른자리, 자리), Math.max(마지막누른자리, 자리)];
    if (!방식.더하기) 선택트랙.clear();
    for (let i = a; i <= b; i += 1) {
      const x = 표에보인것[i];
      if (x) 선택트랙.add(x.job.index);
    }
  } else if (방식.더하기 || 방식.toggle) {
    if (선택트랙.has(job.index)) 선택트랙.delete(job.index);
    else 선택트랙.add(job.index);
    마지막누른자리 = 자리;
  } else {
    선택트랙 = new Set([job.index]);
    마지막누른자리 = 자리;
  }
  고른트랙 = job.index;   // 아래 상세 카드가 이 트랙으로 갱신된다
  다시그리기();
}

// ---- 여러 개 골랐을 때의 액션 바 ----

function 선택바그리기() {
  const 바 = $("selbar");
  if (!바) return;
  const 고른것 = 표에보인것.filter((x) => 선택트랙.has(x.job.index));
  if (고른것.length < 2 || 오른쪽모드 !== "list" || 보는칸 >= 0) {
    바.hidden = true;
    return;
  }
  바.hidden = false;
  바.innerHTML = "";

  const 수 = document.createElement("b");
  수.textContent = `${고른것.length}개 선택`;
  바.appendChild(수);

  const 받아쓸것 = 고른것.filter(
    (x) => x.job.stage === "대기" && x.job.lines === 0);
  const 받기 = document.createElement("button");
  받기.type = "button";
  받기.className = "sb p";
  받기.textContent = 받아쓸것.length ? `받아쓰기 (${받아쓸것.length})` : "받아쓰기";
  받기.disabled = !받아쓸것.length;
  받기.title = 받아쓸것.length ? "" : "고른 것 중에 받아쓸 것이 없습니다";
  받기.onclick = 안전하게(async () => {
    마지막상태 = await api().start_tracks(받아쓸것.map((x) => x.job.index));
    선택트랙.clear();
    작업그리기();
  }, "받아쓰기를 시작하지 못했습니다");
  바.appendChild(받기);

  // **트랙마다 오가지 않게 한다.** 트랙이 넷이면 복사·붙여넣기를 여덟 번
  // 한다. 고른 것을 한 프롬프트로 묶어 한 번에 맡기고, 답도 한 덩이로 받는다.
  // 답은 번호만 보고 제 트랙으로 갈라진다
  const 번역할것 = 고른것.filter((x) => x.job.at >= 0);
  if (번역할것.length >= 2) {
    const 묶기 = document.createElement("button");
    묶기.type = "button";
    묶기.className = "sb p";
    묶기.textContent = `번역 묶어서 복사 (${번역할것.length}트랙)`;
    묶기.onclick = 안전하게(
      () => 묶어서복사(번역할것.map((x) => x.job.index), 묶기),
      "묶지 못했습니다");
    바.appendChild(묶기);
  }

  const 빼기 = document.createElement("button");
  빼기.type = "button";
  빼기.className = "sb";
  빼기.textContent = "빼기";
  빼기.onclick = () => 빼기실행(new Set(고른것.map((x) => x.job.index)));
  바.appendChild(빼기);

  const 닫기 = document.createElement("button");
  닫기.type = "button";
  닫기.className = "sb x";
  닫기.textContent = "✕ Esc";
  닫기.onclick = () => { 선택트랙.clear(); 다시그리기(); };
  바.appendChild(닫기);
}

/** 고른 트랙들을 한 프롬프트로 묶어 복사한다.
 *
 *  **호칭이 부딪히면 먼저 말해 준다.** 작품 A 가 「お兄さん = 오빠」,
 *  작품 B 가 「お兄さん = 형」 이면 구획을 나눠 줘도 AI 가 섞을 수 있다.
 *  오류도 안 나고 줄도 안 빠져서 들어 보기 전에는 모른다. 막지는 않는다 */
async function 묶어서복사(자리들, 단추) {
  const 것 = await api().prompt_many(자리들);
  if (!것 || !것.ok) {
    알림((것 && 것.message) || "묶지 못했습니다.", "bad");
    return;
  }

  const 부딪힘 = 것.name_clashes || [];
  // **막지 않는다.** 복사는 무해하다 — 지우는 것이 없으므로 되돌릴 것도
  // 없다. 팝업으로 흐름을 끊는 대신, 복사한 뒤에 눈에 띄게 말해 주고
  // 무엇을 하면 되는지까지 적는다. 마음에 안 들면 작품별로 다시 복사하면
  // 그만이다 — 그것이 이 경고의 「되돌리기」 다
  await 글복사(복사방식 === "plain" && 것.plain ? 것.plain : 것.text);

  const 원래 = 단추.textContent;
  단추.textContent = "복사했습니다 ✓";
  setTimeout(() => { 단추.textContent = 원래; }, 1600);

  // 답을 기다리는 참이다. 여기서부터 클립보드를 본다
  await 감시자동으로켜기();

  const 미룬것 = 것.left_out || [];
  const 작품말 = 것.works > 1 ? ` · 작품 ${것.works}개` : "";
  // 호칭이 부딪히면 **복사한 뒤에** 말한다. 오류도 안 나고 줄도 안 빠져서
  // 들어 보기 전에는 모르는 종류라, 그냥 넘어가면 안 된다
  if (부딪힘.length) {
    const 보기 = 부딪힘.slice(0, 3).map((c) => `「${c.말}」`).join(" · ");
    알림(
      `복사했습니다. 다만 작품마다 다르게 옮기는 호칭이 있습니다 — ${보기}. `
      + "한 프롬프트에 같이 넣으면 AI 가 섞을 수 있습니다. "
      + "섞이면 작품별로 따로 복사해서 다시 맡기세요.", "bad");
    return;
  }

  알림(
    `트랙 ${것.tracks.length}개${작품말} · ${것.lines}줄을 복사했습니다. `
    + "AI 답을 통째로 붙여넣으면 트랙마다 갈라 들어갑니다."
    + (미룬것.length
       ? ` (줄 수 한도를 넘어 ${미룬것.length}개는 다음에: ${미룬것.join(" · ")})`
       : ""),
    "ok");
  await 번역칸그리기();
}

// ---- 우클릭 메뉴 ----
//
// 첫 항목이 그 트랙의 기본 행동(=Enter)이다. 메뉴는 지름길일 뿐 —
// 같은 명령이 행 단추와 상세 카드에도 있다.

function 메뉴닫기() {
  const 메뉴 = $("ctxmenu");
  if (메뉴) 메뉴.hidden = true;
}

function 우클릭메뉴(job, ev) {
  const 메뉴 = $("ctxmenu");
  if (!메뉴) return;
  메뉴.innerHTML = "";

  const 항목 = (글, 할일, 모양, 키) => {
    const d = document.createElement("div");
    d.className = "mi" + (모양 ? ` ${모양}` : "");
    d.textContent = 글;
    if (키) {
      const k = document.createElement("span");
      k.className = "mk";
      k.textContent = 키;
      d.appendChild(k);
    }
    d.onclick = 안전하게(async () => {
      메뉴닫기();
      await 할일();
    }, "하지 못했습니다");
    메뉴.appendChild(d);
  };
  const 금 = () => {
    const h = document.createElement("hr");
    메뉴.appendChild(h);
  };

  const 행동 = 다음행동(job);
  if (행동.할일) 항목(행동.글, 행동.할일, "first", "Enter");
  if (job.lines > 0 && job.at >= 0) 항목("▶ 듣고 검수", () => 받아쓴것보기(job.index));
  if (job.stage === "대기" && job.lines === 0) {
    항목("앞 2분만 미리 받아쓰기", async () => {
      const 결과 = await api().preview_transcribe(job.index);
      if (!결과.ok) { 안된까닭(결과, "미리 받아쓰지 못했습니다."); return; }
      미리보기지켜보기();
    });
  }
  금();
  if (job.lines > 0 || job.stage === "실패" || job.stage === "건너뜀") {
    항목("받아쓰기 다시", async () => {
      await 되돌릴수있게(() => api().redo_transcribe(job.index));
      await 새로고침();
      다시그리기();
    });
  }
  if (job.lines > 0) 항목("다른 강도와 견주기", () => 견주기열기(job.index));
  if (job.output) 항목("폴더 열기", () => api().open_folder(job.output));
  금();
  항목("목록에서 빼기", () => 빼기실행(new Set([job.index])), "danger", "Del");

  메뉴.hidden = false;
  메뉴.style.left = Math.min(ev.clientX, (window.innerWidth || 1200) - 260) + "px";
  메뉴.style.top = Math.min(ev.clientY, (window.innerHeight || 800) - 320) + "px";
}

// ---- 빼기 → Undo 토스트 ----
//
// 확인창은 읽히지 않는다. 실행부터 하고 8초 안에 되돌린다.
// 8초가 지나야 창구의 remove 를 실제로 부른다 — 그전에는 화면에서만 숨는다.

function 빼기실행(집합) {
  if (!집합.size) return;
  // 이미 기다리는 것이 있으면 합친다. 토스트는 하나만 띄운다
  if (지운타이머) clearTimeout(지운타이머);
  집합.forEach((i) => { 지운대기.set(i, true); 지운모음.add(i); });
  선택트랙.clear();
  다시그리기();

  토스트(`트랙 ${지운모음.size}개를 뺐습니다`, "되돌리기", () => {
    clearTimeout(지운타이머);
    지운타이머 = null;
    지운모음.forEach((i) => 지운대기.delete(i));
    지운모음 = new Set();
    다시그리기();
  });

  지운타이머 = setTimeout(안전하게(async () => {
    지운타이머 = null;
    // **큰 자리부터 뺀다.** 트랙 번호는 목록의 자리라, 앞에서부터 빼면
    // 뒤 번호가 한 칸씩 밀려 엉뚱한 것을 빼게 된다
    const 뺄것 = [...지운모음].sort((a, b) => b - a);
    지운모음 = new Set();
    for (const i of 뺄것) {
      지운대기.delete(i);
      await api().remove(i);
    }
    토스트숨김();
    await 새로고침();
    다시그리기();
  }, "빼지 못했습니다"), 8000);
}

let 토스트타이머 = null;

function 토스트(글, 단추글, 할일) {
  const 칸 = $("undo-toast");
  if (!칸) return;
  칸.innerHTML = "";
  const 말 = document.createElement("span");
  말.textContent = 글;
  칸.appendChild(말);
  if (단추글) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "undo";
    b.textContent = 단추글;
    b.onclick = () => { 토스트숨김(); if (할일) 할일(); };
    칸.appendChild(b);
  }
  칸.hidden = false;
  if (토스트타이머) clearTimeout(토스트타이머);
  토스트타이머 = setTimeout(토스트숨김, 8000);
}

/** 창구가 지운 것을 되돌릴 수 있다고 하면(`undo`) 토스트를 띄운다.
 *
 * **위험한 일을 막는 방법을 이것 하나로 모은다.** 여태는 넷이었다 —
 * 되돌리기 토스트 한 군데, 두 번 누르기 넷, `confirm()` 팝업 셋, 그리고
 * **아무것도 안 하는 것 다섯.** 제일 좋은 것을 제일 안 썼다.
 *
 * 묻지 않으므로 흐름이 안 끊기고, 잘못 눌러도 8초 안에 물릴 수 있다.
 * 확인창보다 되돌리기가 낫다는 것은 오래된 이야기다. */
async function 되돌릴수있게(부르기, 기본말) {
  const 답 = await 부르기();
  if (답 && (답.works || 답.jobs)) 마지막상태 = 답;
  다시그리기();
  const 무엇 = 답 && 답.undo;
  if (!무엇) return 답;
  토스트(답.message || 기본말 || `${무엇} 했습니다`, "되돌리기", 안전하게(async () => {
    const 물린것 = await api().undo_last();
    if (물린것 && (물린것.works || 물린것.jobs)) 마지막상태 = 물린것;
    await 새로고침();
    다시그리기();
  }, "되돌리지 못했습니다"));
  return 답;
}

function 토스트숨김() {
  const 칸 = $("undo-toast");
  if (칸) 칸.hidden = true;
}

// 오른쪽은 둘 중 하나다 — **트랙 목록(표)** / **트랙 하나(검수 창)**.
// 번역은 이제 딴 화면이 아니라 검수 창의 「통째로」 탭이다. 트랙 하나에
// 들어가는 문이 두 개(듣고 고치기 · 번역)라서, 어디로 들어갔냐에 따라
// 화면이 달라지던 것이 "랜덤" 느낌의 뿌리였다
function 오른쪽칸모드() {
  const 고치는중 = 보는칸 >= 0;
  // 창이 닫혀 있으면 하위 판(번역·통째로)도 닫는다. 안 그러면 다음에
  // 열릴 때 지난번 판이 헛보인다
  if (!고치는중) {
    $("viewer-tr").hidden = true;
    $("viewer-all").hidden = true;
  }
  // 번역 붙여넣기 칸이 떠 있는 동안인지는 DOM 이 안다. 따로 변수로 들면
  // 둘이 어긋난 채로 남는 날이 온다
  const 번역탭 = 고치는중 && !$("viewer-tr").hidden;
  $("work-edit").hidden = !고치는중;
  // 검수 중에는 작품 머리가 홀쭉해진다. 표지와 태그는 트랙을 골라
  // 보는 화면의 것이다. **번역 탭이 떠 있으면 아예 숨긴다** — 위에
  // 쌓이면 복사·붙여넣기 칸이 아래로 밀린다는 것이 실제 사용 소감이었다
  const 머리 = $("work-head");
  if (머리) {
    머리.hidden = 번역탭;
    if (고치는중) 머리.classList.add("compact");
    else 머리.classList.remove("compact");
  }
  // 트랙 줄은 검수 중에는 남긴다 — 받아쓰기 다시 · 폴더 열기 · 번역
  // 초기화 · 견주기 · 빼기 · 검사표가 전부 여기 있고, 숨기면 받아쓴
  // 트랙에서는 그 단추들을 아예 못 누른다. 번역 탭에서는 치운다
  $("jobs").hidden = 번역탭;
  $("work-ask").hidden = 고치는중;
  return 고치는중 ? "edit" : "list";
}

function 고치는칸그리기(지금작품) {
  if (오른쪽칸모드() !== "edit") return;

  // 앞뒤로 넘길 트랙. **같은 작품 안에서만** 넘긴다
  const 트랙들 = (지금작품 && 지금작품.jobs) || [];
  const 자리 = 트랙들.findIndex((j) => j.index === 보는칸);
  $("viewer-prev").disabled = 자리 <= 0;
  $("viewer-next").disabled = 자리 < 0 || 자리 >= 트랙들.length - 1;

  // 탭 이름이 지금 할 일을 말한다. 번역할 것이 남았으면 통째로 탭이 곧
  // 번역 칸이다 — 이름에 안 적으면 그 문이 있는 줄 모른다
  const 잡 = 트랙들[자리];
  $("viewer-tab-all").textContent =
    잡 && 잡.at >= 0 ? "통째로 · 번역" : "통째로";
}

// 같은 작품 안에서 앞뒤 트랙으로. 없으면 아무것도 안 한다
async function 옆트랙보기(걸음) {
  // **넘어가기 전에 하던 것을 마저 담는다.** ◀▶ 로 넘기는 것이 이 창의
  // 쓰는 법이라, 여기서 흘리면 고친 것이 조용히 날아간다
  await 고친것바로저장();
  const 작품들 = 마지막상태.works || [];
  const 지금작품 = 작품들.find((w) => w.key === 고른작품);
  const 트랙들 = (지금작품 && 지금작품.jobs) || [];
  const 자리 = 트랙들.findIndex((j) => j.index === 보는칸);
  const 갈곳 = 트랙들[자리 + 걸음];
  if (자리 < 0 || !갈곳) return;
  // 보던 탭 그대로 넘긴다. 번역을 넘기며 하다가 갑자기 한 줄씩로 튀면
  // 어디로 왔는지 모른다
  await 받아쓴것보기(갈곳.index, null, 탭지금);
}

// 왼쪽은 **작품 → 트랙 두 층**이다. 작품만 늘어놓으면 어느 트랙을 보고
// 있는지가 오른쪽 스크롤 자리로만 남는다. 번역 화면 나무와 같은 부품(.wk / .trs
// / .tr)을 쓴다 — 두 화면이 같아 보여야 한다
function 작품고르는칸(작품) {
  const 상자 = document.createElement("div");
  const 지금인가 = 작품.key === 고른작품;
  상자.className = "wk" + (지금인가 ? " on" : "");
  const 번쩍이나 = 작품.key === 번쩍인작품;

  const 칸 = document.createElement("button");
  칸.type = "button";
  칸.className = "wk-head" + (번쩍이나 ? " flash" : "");
  칸.onclick = () => {
    // 작품 클릭 = 그 작품을 펼치기. 한 번 더 누르면 접힌다.
    //
    // 제보 2 번: 「작품 이름을 클릭하면 뭐 변하는게 없음. 그리고 그 작품이
    // 접히지도 않음」. 골라 둔 작품을 또 눌러도 `고른작품` 이 그대로라
    // 아무 일도 안 났고, 번쩍임은 **오른쪽** 머리에서 났다 — 왼쪽을 보고
    // 있던 사람에게는 없는 것과 같다 (규칙 2).
    고른작품 = 작품.key;
    접은작품.delete(작품.key);   // 고르면 펴진다 — 고른 것이 접혀 있으면 앞뒤가 안 맞는다
    선택트랙.clear();
    고른트랙 = -2;      // 트랙 선택도 푼다 — 작품을 골랐지 트랙을 고른 게 아니다
    작품번쩍(작품.key);  // **누른 자리**가 번쩍인다. 이미 골라 둔 것을 또 눌러도
    목록으로();
  };

  const 표지 = document.createElement("span");
  표지.className = "wk-face";
  const 정보 = 작품.info;
  if (정보 && 정보.image) {
    const img = document.createElement("img");
    img.src = 정보.image;
    img.alt = "";
    // 그림을 못 받아도 자리는 남는다. 인터넷이 없을 수도 있다
    img.onerror = () => { 표지.textContent = "🎧"; };
    표지.appendChild(img);
  } else {
    표지.textContent = 작품.needs_id ? "?" : "🎧";
  }

  const 글칸 = document.createElement("div");
  글칸.className = "wk-t";
  const 이름 = document.createElement("span");
  이름.className = "wk-name";
  // 번역해 뒀으면 한국어를 앞에 세운다. **긴 일본어를 눈으로 가릴 수는
  // 없다** — 마우스를 올렸을 때만 보이면 있는 줄도 모른다
  이름.textContent = 작품.ko || 작품.name;
  이름.title = 작품.ko ? `${작품.ko}\n${작품.name}` : 작품.name;
  const 곁말 = document.createElement("span");
  곁말.className = "wk-sub";
  // 상태가 없을 때 「트랙 3개 · 」 처럼 꼬리가 남으면 안 된다.
  // **`status` 는 객체다** (`api._작품상태`). 통째로 넣으면 왼쪽 나무에
  // 「트랙 4개 · [object Object]」 가 찍힌다 — 사람이 읽을 것은 `label` 이다
  곁말.textContent = [`트랙 ${작품.jobs.length}개`, 작품.status && 작품.status.label]
    .filter(Boolean).join(" · ");
  글칸.append(이름, 곁말);

  // **접기는 제 자리를 가진다** (규칙 1 — 두 가지 일이 필요하면 자리를
  // 나눈다). 머리를 누르면 고르기, 꺾쇠를 누르면 접기. 하나에 둘을 물리면
  // 「눌렀는데 접혔다 폈다 한다」 가 된다
  const 꺾쇠 = document.createElement("span");
  꺾쇠.className = "wk-fold";
  const 접혔나 = () => 지금인가 && 접은작품.has(작품.key);
  꺾쇠.textContent = 접혔나() ? "▸" : "▾";
  꺾쇠.title = 접혔나() ? "트랙 펴기" : "트랙 접기";
  꺾쇠.hidden = !지금인가;
  꺾쇠.onclick = (ev) => {
    ev.stopPropagation();     // 머리의 「고르기」 까지 같이 일어나면 안 된다
    if (접은작품.has(작품.key)) 접은작품.delete(작품.key);
    else 접은작품.add(작품.key);
    번쩍(꺾쇠);
    다시그리기();
  };

  칸.append(표지, 글칸, 꺾쇠);
  상자.appendChild(칸);

  // 고른 작품만 트랙을 펼친다. 다 펼치면 열다섯 줄이 늘어선다
  if (지금인가 && !접은작품.has(작품.key)) 상자.appendChild(왼쪽트랙칸(작품));
  return 상자;
}

// 왼쪽 나무의 트랙 줄. 누르면 **오른쪽이 그 트랙으로 바뀐다**
function 왼쪽트랙칸(작품) {
  const 목록 = document.createElement("div");
  목록.className = "trs";
  (작품.jobs || []).filter((j) => !지운대기.has(j.index)).forEach((job, 자리) => {
    const 받아쓴것있음 = job.lines > 0;
    const 줄 = document.createElement("button");
    줄.type = "button";
    줄.className = "tr" + (job.index === 고른트랙 ? " on" : "")
      + (job.stage === "완료" ? " fin" : "");

    const 위 = document.createElement("div");
    위.className = "tr-top";
    const 번호 = document.createElement("span");
    번호.className = "tr-n";
    번호.textContent = 자리 + 1;
    const 이름 = document.createElement("span");
    이름.className = "tr-name";
    이름.textContent = job.ko || job.name;
    이름.title = job.ko ? `${job.ko}\n${job.name}` : (job.path || job.name);
    // 지금 받아쓰고 있는가. 「연속인 것은 점이 아니라 막대」 규칙이 걸리는
    // 자리다 — 묶음은 셀 수 있어서 점, 받아쓰기는 못 세어서 막대다
    const 받아쓰는중 = job.stage === "받아쓰기" || job.stage === "다시훑기";
    const 잰것 = 받아쓰는중 && job.progress > 0;

    const 딱지 = document.createElement("span");
    // 오른쪽이 고른 트랙 하나만 그리므로, 실패·건너뜀은 **여기서** 눈에
    // 띄어야 한다. 안 그러면 열다섯 개를 하나씩 눌러 봐야 안다
    const 나쁨 = job.stage === "실패" || job.stage === "건너뜀";
    딱지.className = "tr-k" + (나쁨 ? " bad" : "");
    // **다시 받아쓰는 중이면 옛 줄 수를 띄우면 안 된다.** 「124줄」 이 떠
    // 있으면 노는 것처럼 보여서, 지금 돌고 있는 트랙을 놓친다.
    // 남은 시간은 잰 것이 있을 때만 곁들인다 — 지어낸 숫자는 안 띄운다
    딱지.textContent = 잰것
      ? `${Math.round(job.progress * 100)}%`
        + (job.eta_sec > 0 ? ` · ${시간말(job.eta_sec)} 남음` : "")
      // **실패한 트랙은 「실패」 라고 쓴다.** 받아쓴 줄이 남아 있으면 「121줄」
      // 을 빨갛게 찍었다 — 줄 수는 실패가 아닌데 빨강이었다
      : (나쁨 || 받아쓰는중 || !받아쓴것있음 ? 단계말(job.stage)
         : (job.dots || []).length > 1 && job.translated < 1 && job.at >= 0
           ? `${job.dots.filter(Boolean).length}/${job.dots.length} 묶음`
           : `${job.lines}줄`);
    // 파이프 바는 표의 것이다. 나무는 길찾기라 이름과 딱지 하나면 된다 —
    // 같은 목록이 양쪽에 같은 그림으로 두 번 뜨면 어수선하다
    위.append(번호, 이름, 딱지);

    // **걸어 둔 것이 보여야 한다.** 안 보이면 같은 트랙을 또 걸거나,
    // 걸어 놓고도 기다리는 줄 모르고 다른 것을 손으로 돌리게 된다
    if (job.running || job.queued) {
      const 걸림 = document.createElement("span");
      걸림.className = "tr-q" + (job.running ? " run" : "");
      걸림.textContent = job.running ? "번역 중" : `대기 ${job.queued}`;
      위.appendChild(걸림);
    }

    // **봐야 할 것이 있는 트랙.** 오른쪽이 고른 트랙 하나만 그리게 됐으므로,
    // 어느 트랙에 검사표가 붙었는지는 여기서밖에 못 본다
    if (job.report && (job.report.findings || []).length) {
      const 표 = document.createElement("span");
      표.className = "tr-warn";
      표.textContent = "⚠";
      표.title = "봐야 할 것이 있습니다";
      위.appendChild(표);
    }

    // **받아쓰는 중이면 흐르는 막대.** 나무에만 없어서, 열다섯 개를 걸어
    // 놓고도 지금 어느 것이 돌고 있는지 오른쪽을 눌러 봐야 알았다.
    // 몇 %인지는 모를 때가 있다(모델 올리는 동안) — 그때는 안 띄운다.
    // 묶음 점은 **도는 중일 때만** 한 줄 더 편다. 다 끝난 것은 파이프 바가
    // 이미 말하고 있어서, 같은 것을 두 번 그리면 자리만 먹는다
    줄.appendChild(위);
    // 도는 중일 때만 흐르는 막대. 묶음 점막대는 표가 말한다
    if (잰것) 줄.appendChild(흐르는막대(job));
    // 클릭은 선택이다 (규칙 1). 상세를 열려면 오른쪽 표의 단추나
    // 더블클릭을 쓴다 — 클릭마다 딴 화면이 뜨면 랜덤으로 느껴진다
    줄.onclick = 안전하게(() => 트랙선택(job), "트랙을 고르지 못했습니다");
    목록.appendChild(줄);
  });
  return 목록;
}

function 세어보기(jobs) {
  if (!jobs.length) return "";
  const 센것 = {};
  jobs.forEach((j) => { 센것[j.stage] = (센것[j.stage] || 0) + 1; });
  return `트랙 ${jobs.length}개 · ` +
    Object.keys(센것).map((k) => `${단계말(k)} ${센것[k]}`).join(" · ");
}

// 전역 단계 표시(음원→받아쓰기→번역→자막)가 여기 있었는데 뺐다.
// 작품이 여럿이면 각자 다른 단계라 「전체가 몇 단계」 는 거짓말이 된다.
// 단계는 나무의 파이프 바가 트랙마다 따로 말한다.

// ---- 작품 상자 ----

function 다시그리기() {
  지난그림 = "";   // 상태가 그대로여도 다시 그리게 한다
  작업그리기();
}

// ---- 오른쪽 화면의 규칙 ----
//
// 눌렀을 때 무엇이 뜰지 **외울 수 있어야 한다.** 규칙은 셋뿐이다.
//
//   1. 트랙·작품을 **클릭**하면 — 언제나 목록으로 돌아와 그것을 고른다.
//      상세가 열려 있었어도 닫힌다. 클릭이 화면을 바꾸는 일은 없다.
//      **예외 하나**: 상세(듣고 고치기·번역)가 열려 있는 동안 받아쓴 것이
//      있는 트랙을 클릭하면, 칸을 지킨 채 그 트랙으로 갈아끼운다 — ◀▶ 와
//      같은 일이다. 트랙마다 복사해 돌리는 흐름에서 클릭마다 칸이 닫히면
//      「다시 열기」 세금이 붙는다
//   2. 상세(번역·검수)는 **명시적으로만** 연다 — 행의 단추, 더블클릭,
//      Enter, 우클릭 메뉴. 단추에 적힌 그것이 열린다
//   3. 닫으면(←·Esc·목록으로) **언제나 목록**이다. "아까 어디였나" 로
//      돌아가는 영리함은 뒀다 — 그 영리함이 랜덤으로 느껴진다

/** 목록으로. 어느 상세가 열려 있었든 여기로 온다 */
function 목록으로(트랙번호) {
  보는칸 = -1;
  오른쪽모드 = "list";
  if (트랙번호 != null) {
    고른트랙 = 트랙번호;
    선택트랙 = new Set([트랙번호]);
  }
  소리멈춤();
  다시그리기();
}

/** 작품 머리 번쩍임. 다시 그려도 살아남게 **상태로** 켜 둔다 */
function 작품번쩍(열쇠) {
  번쩍인작품 = 열쇠;
  if (번쩍시계) clearTimeout(번쩍시계);
  번쩍시계 = setTimeout(() => { 번쩍인작품 = null; 번쩍시계 = null; 다시그리기(); }, 700);
}

/** 눌렀다는 것이 보여야 한다. 같은 작품 안에서는 클릭해도 하이라이트만
 * 옮겨 가서, 반응이 없는 죽은 클릭처럼 느껴진다 — 실제로 그런 말을 들었다 */
function 번쩍(el) {
  if (!el || !el.classList) return;
  el.classList.remove("flash");
  setTimeout(() => el.classList.add("flash"), 0);
  setTimeout(() => el.classList.remove("flash"), 700);
}

/** 나무에서 트랙 클릭 = **그 트랙으로 간다.** 늘 같은 일이다 (규칙 1).
 *
 * 제보 1 번: 「작품을 선택했을 때 트랙을 선택하면 그 트랙으로 이동되는게
 * 아님」. 예전에는 오른쪽 상세가 **열려 있으면** 그 트랙으로 옮겨 가고,
 * **닫혀 있으면** 목록에서 번쩍이기만 했다. 가른 것은 `오른쪽모드` 라는
 * 화면에 없는 값이다 — 같은 자리를 같은 방법으로 눌렀는데 결과가 둘이고,
 * 어느 쪽인지 알 방법이 없다 (규칙 5).
 *
 * 나무는 길찾기다. 길찾기에서 누른 것은 간다. 받아쓴 것이 없어 열어 볼
 * 것이 없는 트랙만 목록에 남는데, 그건 `118줄` / `받아쓰기 전` 이라고
 * **줄에 적혀 있는** 값으로 갈리므로 규칙 5 를 어기지 않는다.
 */
async function 트랙선택(job) {
  고른트랙 = job.index;
  선택트랙 = new Set([job.index]);
  if (job.lines > 0) {
    await 받아쓴것보기(job.index, null, 오른쪽모드 === "edit" ? 탭지금 : null);
    return;
  }
  목록으로(job.index);
  const 행 = $(`trow-${job.index}`);
  if (행) {
    if (typeof 행.scrollIntoView === "function") 행.scrollIntoView({ block: "nearest" });
    번쩍(행);
  }
}

// 번역으로 들어가는 지름길. 작품 머리의 「번역 이어서」 가 여기로 온다.
// 번역은 이제 딴 화면이 아니라 **그 트랙의 검수 창 「통째로」 탭**이다 —
// 대기열이 가리키는 트랙을 찾아 그 탭을 연다. 시험 하네스도 이 문으로 온다
async function 번역칸열기() {
  const 것 = await api().prompt();
  if (!것 || !것.ready) { 목록으로(); return; }
  const 전부 = (마지막상태.works || []).flatMap((w) => w.jobs || []);
  const 잡 = 전부.find((j) => j.at === 것.at && j.at >= 0)
    || 전부.find((j) => j.at >= 0);
  if (!잡) { 목록으로(); return; }
  await 받아쓴것보기(잡.index, null, "all");
}

/** 트랙 상세를 연다 (규칙 2) — 더블클릭 · Enter · 행 단추 · 메뉴에서만.
 * 늘 같은 검수 창이 열린다. 번역할 것이 있으면 「통째로」(번역) 탭으로,
 * 받아쓴 것만 있으면 「한 줄씩」 탭으로. */
async function 트랙열기(job) {
  고른트랙 = job.index;
  // 연 트랙이 곧 고른 트랙이다. 안 맞추면 Enter·Del 이 딴 트랙에 간다
  선택트랙 = new Set([job.index]);
  if (job.at >= 0) {
    await 받아쓴것보기(job.index, null, "all");
  } else if (job.lines > 0) {
    await 받아쓴것보기(job.index);
  } else {
    목록으로(job.index);
  }
}

function 작품머리(작품) {
  // 접기 화살이 있었다. 좌/우로 나눈 뒤에는 **고른 작품 하나만** 오른쪽에
  // 뜨므로 접을 이유가 없다 — 왼쪽에서 다른 작품을 누르면 된다
  const head = document.createElement("header");
  head.className = "work-head";

  const 정보 = 작품.info;
  const cover = document.createElement("div");
  cover.className = "cover";
  if (정보 && 정보.image) {
    const img = document.createElement("img");
    img.src = 정보.image;
    img.alt = "";
    // 그림을 못 받아도 자리는 남는다. 인터넷이 없을 수도 있다
    img.onerror = () => { cover.textContent = "🎧"; };
    cover.appendChild(img);
  } else {
    cover.textContent = 작품.needs_id ? "?" : "🎧";
  }
  head.appendChild(cover);

  const info = document.createElement("div");
  info.className = "work-info";

  const id = document.createElement("div");
  id.className = "work-id";
  id.textContent = (정보 && 정보.found ? 정보.id : 작품.folder) + `  ·  트랙 ${작품.jobs.length}개`;
  info.appendChild(id);

  const title = document.createElement("div");
  title.className = "work-title";
  title.textContent = 작품.name;
  title.title = 작품.name;
  info.appendChild(title);

  if (정보 && 정보.found) {
    const sub = document.createElement("div");
    sub.className = "work-sub";
    const 성우 = 정보.voices.length ? ` · ${정보.voices.join(", ")}` : "";
    sub.textContent = `${정보.maker}${성우}`;
    info.appendChild(sub);

    if (정보.genres.length) {
      const tags = document.createElement("div");
      tags.className = "tags";
      정보.genres.slice(0, 10).forEach((g) => {
        const t = document.createElement("span");
        t.className = "tag2" + (정보.minor_genres.includes(g) ? " warn" : "");
        t.textContent = g;
        tags.appendChild(t);
      });
      info.appendChild(tags);
    }
  }
  head.appendChild(info);

  // 어느 차례에 몇 개가 있는지. 트랙이 아홉이면 카드를 하나씩 볼 수 없다
  const 상태 = 작품.status;
  if (상태) {
    const 줄 = document.createElement("div");
    줄.className = "work-status";

    const 막대 = document.createElement("div");
    막대.className = "bar-track";
    const 찬것 = document.createElement("div");
    찬것.className = "bar-fill" + (상태.done === 상태.total ? " done" : "");
    찬것.style.width = Math.round((상태.done / (상태.total || 1)) * 100) + "%";
    막대.appendChild(찬것);

    const 말 = document.createElement("span");
    말.className = 상태.failed ? "bad-text" : "muted";
    말.textContent = 상태.label;

    const 낱개 = document.createElement("span");
    낱개.className = "muted";
    낱개.textContent = (상태.counts || [])
      .map((c) => `${c.name} ${c.count}`)
      .join(" · ");

    줄.append(막대, 말, 낱개);
    info.appendChild(줄);
  }

  const 오른쪽 = document.createElement("div");
  오른쪽.className = "work-actions";
  // 번역으로 가는 단추는 **작품 옆**에 있다. 전역 단추는 「어느 작품의
  // 무엇을 번역하는지」 를 말하지 못해서 뺐다. 남은 묶음이 있을 때만 뜬다
  const 큐 = (마지막상태 && 마지막상태.queue) || {};
  const 남은묶음 = (큐.total || 0) - (큐.done || 0);
  if (큐.ready && 남은묶음 > 0) {
    const 이어서 = document.createElement("button");
    이어서.id = "go-translate";
    이어서.className = "primary small";
    이어서.textContent = `번역 이어서 (${남은묶음}번 남음)`;
    이어서.onclick = 안전하게(번역칸열기, "번역 칸을 열지 못했습니다");
    오른쪽.appendChild(이어서);
  }

  // **묶어서 맡기는 길을 늘 띄운다.**
  //
  // 예전에는 트랙을 둘 이상 고르면 뜨는 띠 안에만 있었다. 그런데 **여러 개
  // 고르는 법을 모르면 그런 것이 있는 줄도 모른다** — 만든 사람도 몰랐다.
  // 트랙마다 들어가서 복사·붙여넣기를 되풀이하는 것이 제일 힘들다는 말을
  // 듣고 만든 기능인데, 정작 그 사람이 못 찾았다.
  //
  // 고르기는 「굳이 몇 개만」 할 때 쓰는 곁길로 남기고, 흔한 것 — 이 작품에
  // 남은 것 전부 — 을 작품 머리에 둔다
  const 이작품번역할것 = (작품.jobs || []).filter((j) => j.at >= 0);
  if (이작품번역할것.length >= 2) {
    const 묶기 = document.createElement("button");
    묶기.className = "ghost small";
    묶기.textContent = `묶어서 복사 (${이작품번역할것.length}트랙)`;
    묶기.title = "이 작품에서 번역할 트랙을 한 프롬프트로 묶어 복사합니다";
    묶기.onclick = 안전하게(
      () => 묶어서복사(이작품번역할것.map((j) => j.index), 묶기),
      "묶지 못했습니다");
    오른쪽.appendChild(묶기);
  }
  // **제목은 작품의 일이다** (규칙 6). 예전에는 나무에서 작품들과 같은 층에
  // 선 입구로 들어가 넣어 둔 작품 전부가 한꺼번에 떴다. 제목은 작품에
  // 딸린 것인데 작품 밖에 서 있으니 어색하다는 말을 들었다
  const 제목단추 = document.createElement("button");
  제목단추.id = "go-titles";
  제목단추.className = "ghost small";
  제목단추.textContent = 작품.ko ? "제목 다시 번역" : "제목 번역";
  제목단추.title = "이 작품의 제목과 트랙 이름을 한 번에 옮깁니다";
  제목단추.onclick = 안전하게(() => 제목칸열기(작품.key), "열지 못했습니다");
  오른쪽.appendChild(제목단추);

  // 접었을 때 요약만 띄우던 갈래가 있었다. 좌/우로 나눈 뒤에는 접는 것이
  // 없으므로 단추를 늘 띄운다
  // **되돌려야 하는 것들은 한 칸 떨어져, 한 단 조용하게.** 「번역 다시」 와
  // 「작품 빼기」 가 주 단추 바로 옆에 같은 무게로 서 있었다. 되돌리기는 있지만
  // 자리가 위험했다. 제일 좋은 답은 ⋯ 서랍인데, 그건 머리 짜임을 바꾸는 일이다
  const 금 = document.createElement("span");
  금.className = "act-sep";
  오른쪽.appendChild(금);
  오른쪽.appendChild(
    작은단추("번역 다시", async () => {
      await 되돌릴수있게(() => api().redo_translate(작품.key));
      await 새로고침();
      다시그리기();
    }, "quiet")
  );
  오른쪽.appendChild(
    작은단추("작품 빼기", async () => {
      await api().remove_work(작품.key);
      await 새로고침();
      다시그리기();
    }, "quiet")
  );
  head.appendChild(오른쪽);
  return head;
}

function 품번묻기(작품) {
  const wrap = document.createElement("div");
  wrap.className = "ask";

  const 말 = document.createElement("span");
  말.className = "muted";
  말.textContent = 작품.guess
    ? `${작품.guess} 를 DLsite 에서 찾지 못했습니다. 품번을 확인해 주세요.`
    : "품번을 몰라 작품 정보를 못 가져왔습니다.";

  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "RJ01234567";
  // 치던 것이 있으면 그것을 살린다. 다시 그려도 날아가지 않는다
  input.value = 치던품번[작품.key] != null ? 치던품번[작품.key] : (작품.guess || "");
  input.oninput = () => { 치던품번[작품.key] = input.value; };

  const 단추 = document.createElement("button");
  단추.className = "primary";
  단추.textContent = "찾기";
  const 까닭 = document.createElement("span");
  까닭.className = "why bad-text";
  까닭.hidden = true;

  const 보내기 = async () => {
    단추.disabled = true;
    까닭.hidden = true;
    const 결과 = await api().set_work_id(작품.key, input.value);
    if (결과 && 결과.ok === false) {
      // **까닭을 버리면 안 된다.** 예전에는 빈 칸으로 누르면 아무 일도 안
      // 일어난 것처럼 보이고, 치던 것까지 지워졌다. 눌러도 반응이 없는 것이
      // 제일 나쁘다
      까닭.textContent = 결과.message || "품번을 넣지 못했습니다.";
      까닭.hidden = false;
      단추.disabled = false;
      return;                       // 치던 것을 지우지 않는다
    }
    delete 치던품번[작품.key];
    지난그림 = "";  // 답이 오면 반드시 다시 그린다
    await 새로고침();
  };
  단추.onclick = 보내기;
  input.onkeydown = (ev) => { if (ev.key === "Enter") 보내기(); };

  wrap.append(말, input, 단추, 까닭);
  return wrap;
}

// 색은 뜻으로 정한다. `docs/17_UI_UNIFICATION.md` 의 표와 같아야 한다.
//
//   초록 끝남 · 파랑 지금 하는 중 · 노랑 봐야 할 것 · 빨강 망가짐 · 회색 아직
//
// **두 군데로 흩어져 있었다.** 번역 화면 나무는 제 나름대로 칠하고 작업
// 화면은 여기서 칠해서, 같은 상태가 화면마다 다른 색이었다
const 상태색 = {
  대기: "", 받아쓰기: "run", 다시훑기: "run", 번역: "run",
  자막: "run", 완료: "done", 건너뜀: "skip", 실패: "fail",
};

/** 점 하나가 단위 하나. 초록 끝남 / 파랑 지금 / 회색 남음.
 *
 * **두 화면이 같은 부품을 쓴다.** 번역 화면 나무에만 있어서, 작업 화면에서는
 * 「번역 차례」 라고만 떴다 — 열네 묶음 중 넷을 넣은 것인지 하나도 안 넣은
 * 것인지 알 수가 없었다.
 */
/** 못 세는 것이 얼마나 왔는지. 받아쓰기처럼 **연속인 것**에 쓴다.
 *
 * 묶음은 몇 개인지 세어지므로 점 막대를 쓴다. 받아쓰기는 못 세므로 막대다.
 */
function 흐르는막대(job) {
  const 칸 = document.createElement("div");
  칸.className = "track tr-run";
  const 찬것 = document.createElement("div");
  찬것.className = "fill";
  찬것.style.width = Math.round(job.progress * 100) + "%";
  칸.appendChild(찬것);
  칸.title = 걸린시간말(job) || job.stage;
  return 칸;
}

function 점막대(끝난것들, 지금여기) {
  const 칸 = document.createElement("div");
  칸.className = "dots";
  (끝난것들 || []).forEach((끝, i) => {
    const 점 = document.createElement("i");
    점.className = 끝 ? "done" : (지금여기 === i ? "now" : "");
    칸.appendChild(점);
  });
  return 칸;
}

/** 트랙 하나가 네 단계 중 어디까지 왔는지 — 넣기 · 받아쓰기 · 번역 · 자막.
 *
 * 초록 = 끝남, 파랑 = 지금, 빨강 = 망가짐, 회색 = 아직.
 * 나무 한 줄에 이것이 있어야 **열어 보지 않아도** 어디까지 왔는지 보인다.
 */
function 단계셈(job) {
  const 받는중 = job.stage === "받아쓰기" || job.stage === "다시훑기";
  const 받아씀 = job.lines > 0 && !받는중;
  const 자막다 = job.stage === "완료";
  return [
    "d",
    job.stage === "실패" ? "x" : (자막다 || 받아씀 ? "d" : (받는중 ? "h" : "")),
    자막다 || job.translated >= 1 ? "d"
      : ((job.dots || []).some(Boolean) || job.stage === "번역" ? "h" : ""),
    자막다 ? "d" : (job.output ? "h" : ""),
  ];
}

function 파이프바(job) {
  const wrap = document.createElement("span");
  wrap.className = "pipe";
  // **점마다 무엇인지 말한다.** 점 넷이 색만 있고 범례가 없어서 초록·파랑·빨강이
  // 뭘 뜻하는지 처음 보는 사람은 몰랐다. 점에 올리면 「받아쓰기 — 지금」 처럼 뜬다
  const 이름들 = ["넣기", "받아쓰기", "번역", "자막"];
  const 상태말들 = { d: "끝남", h: "지금", x: "실패", "": "아직" };
  wrap.title = "넣기 · 받아쓰기 · 번역 · 자막 순서. 초록 = 끝남 · 파랑 = 지금 · 빨강 = 실패 · 회색 = 아직";
  단계셈(job).forEach((c, k) => {
    const i = document.createElement("i");
    i.title = `${이름들[k]} — ${상태말들[c] || "아직"}`;
    i.className = c;
    wrap.appendChild(i);
  });
  return wrap;
}

/** 오른쪽 트랙 카드의 네 단계 상황판. 나무의 파이프 바와 같은 셈이되,
 * 여기는 자리가 있으니 단계마다 이름과 잰 값을 함께 적는다. */
function 단계띠(job) {
  const 칸들 = 단계셈(job);
  const 받는중 = job.stage === "받아쓰기" || job.stage === "다시훑기";
  const 글들 = [
    ["넣기", job.duration_sec > 0 ? 시간말(job.duration_sec) : ""],
    ["받아쓰기",
     job.stage === "실패" ? "실패"
       : job.stage === "건너뜀" ? "말이 없음"
       : 받는중 ? `${Math.round(job.progress * 100)}%`
       : job.lines > 0 ? `${job.lines}줄` : "대기"],
    ["번역",
     job.translated >= 1 ? "끝"
       : (job.dots || []).length > 1
         ? `${job.dots.filter(Boolean).length}/${job.dots.length} 묶음`
         : job.at >= 0 ? "차례" : "—"],
    ["자막", job.stage === "완료" ? "만듦" : job.output ? "쓰는 중" : "—"],
  ];

  const 띠 = document.createElement("div");
  띠.className = "stages";
  칸들.forEach((상태, i) => {
    const 한칸 = document.createElement("div");
    한칸.className = "stage " + (상태 || "todo");
    const 점 = document.createElement("span");
    점.className = "s-dot";
    점.textContent = 상태 === "d" ? "✓" : 상태 === "x" ? "!" : String(i + 1);
    const 글 = document.createElement("div");
    글.className = "s-t";
    const 이름 = document.createElement("b");
    이름.textContent = 글들[i][0];
    const 값 = document.createElement("span");
    값.textContent = 글들[i][1];
    글.append(이름, 값);
    한칸.append(점, 글);
    띠.appendChild(한칸);
  });
  return 띠;
}

function 작업하나(job, index) {
  // **카드가 아니라 줄이다.** 격자로 깔면 열다섯 개를 훑을 수 없다.
  //
  // 오른쪽이 번역·검수를 띄우고 있으면 **홀쭉해진다** — 단계 상황판과
  // 검사표를 접고 단추 줄만 남긴다. 그 둘은 트랙을 골라 보는 화면의 것인데,
  // 검수 중에도 그대로 펴져 있으면 정작 고치는 칸이 아래에 눌린다
  const 홀쭉 = 보는칸 >= 0;
  const box = document.createElement("div");
  box.className = "job" + (홀쭉 ? " slim" : "");

  const head = document.createElement("div");
  head.className = "job-head";
  const name = document.createElement("span");
  name.className = "job-name";
  name.textContent = job.name;
  name.title = job.path;
  const tag = document.createElement("span");
  tag.className = "tag " + (상태색[job.stage] || "");
  tag.textContent = job.stage;

  // 잘못 넣은 것을 뺄 길이 있어야 한다. 묻지 않고 빼고, 8초 안에 되돌린다
  const 빼기 = document.createElement("button");
  빼기.className = "x";
  빼기.textContent = "✕";
  빼기.title = "목록에서 빼기 (Del)";
  빼기.onclick = () => 빼기실행(new Set([index]));

  head.append(name, tag, 빼기);
  box.appendChild(head);

  // 네 단계 상황판(단계띠)이 있었는데 뺐다 — 표의 파이프 바와 같은 정보를
  // 다른 그림으로 반복했다

  if (job.message || job.error) {
    const msg = document.createElement("div");
    msg.className = "job-msg";
    msg.textContent = job.error || job.message;
    box.appendChild(msg);
  }

  if (job.stage !== "완료" && job.stage !== "대기" && job.progress > 0) {
    const track = document.createElement("div");
    track.className = "track";
    const fill = document.createElement("div");
    fill.className = "fill";
    fill.style.width = Math.round(job.progress * 100) + "%";
    track.appendChild(fill);
    box.appendChild(track);

    // 얼마나 지났고 얼마나 남았는지. 20분을 기다리는 사람에게 이것이 없으면
    // 멈춘 것인지 도는 것인지 알 수가 없어서 자꾸 창을 껐다 켜게 된다
    const 시계 = document.createElement("div");
    시계.className = "job-clock";
    시계.textContent = 걸린시간말(job);
    // 「3.2배속」 이 빠른 것인지 사람은 모른다. 눈금을 붙여 준다
    시계.title = 배속말(job["배속"]) || 걸린시간말(job);
    box.appendChild(시계);
  }

  // 묶음 진행. 「번역 차례」 라고만 떠 있으면 열네 묶음 중 넷을 넣은
  // 것인지 하나도 안 넣은 것인지 알 수가 없다. 번역 화면과 같은 부품이다.
  // 다 끝났으면 안 편다 — 단계 상황판이 이미 「끝」 이라고 말하고 있다
  if ((job.dots || []).length > 1 && job.dots.some((d) => !d)) {
    const 묶음줄 = document.createElement("div");
    묶음줄.className = "job-batches";
    const 끝난수 = job.dots.filter(Boolean).length;
    묶음줄.appendChild(점막대(job.dots, 끝난수 < job.dots.length ? 끝난수 : -1));
    const 센것 = document.createElement("span");
    센것.className = "muted";
    센것.textContent = `묶음 ${끝난수}/${job.dots.length}`;
    묶음줄.appendChild(센것);
    box.appendChild(묶음줄);
  }

  const foot = document.createElement("div");
  foot.className = "job-foot";

  // 이 카드에서 할 일은 사실 하나다. 그것만 크게 두고 나머지는 작게 둔다.
  // 단추 넷이 나란히 있으면 무엇을 눌러야 할지 알 수 없다
  // 이미 그 트랙을 고치고 있으면 이 단추는 할 일이 없다. 바로 밑에 열려 있다
  if (job.lines > 0 && index !== 보는칸) {
    const 주단추 = document.createElement("button");
    // 표 줄의 「▶ 듣고 검수」(초록) 와 같은 일인데 여기는 「듣고 확인하기」(파랑)
    // 였다. 같은 일은 같은 이름, 같은 색이다
    주단추.className = "primary go small wide";
    주단추.textContent = "▶ 듣고 검수";
    주단추.onclick = () => 받아쓴것보기(index);
    foot.appendChild(주단추);
  }
  // 다 안 됐어도 자막은 이미 만들어져 있다. 그때도 열 수 있어야 한다
  if (job.output) {
    foot.appendChild(작은단추("폴더 열기", () => api().open_folder(job.output)));
  }
  if (job.translated > 0) {
    foot.appendChild(
      작은단추("번역 초기화", async () => {
        await 되돌릴수있게(() => api().reset_translation(index));
        await 새로고침();
        다시그리기();
      })
    );
  }
  if (job.hint) {
    const 알림 = document.createElement("div");
    알림.className = "job-hint";
    알림.textContent = job.hint;
    box.appendChild(알림);
  }

  if (job.lines > 0 || job.stage === "실패" || job.stage === "건너뜀") {
    foot.appendChild(
      작은단추("받아쓰기 다시", async () => {
        await 되돌릴수있게(() => api().redo_transcribe(index));
        await 새로고침();
        다시그리기();
      })
    );
  }
  // 아직 안 받아쓴 트랙은 앞 2분만 먼저 돌려 볼 수 있다. 3시간을 20분
  // 돌리고 나서야 강도가 안 맞는 것을 알면 그 20분이 통째로 아깝다
  if (job.stage === "대기" && job.lines === 0) {
    foot.appendChild(작은단추("앞 2분만 미리 받아쓰기", async () => {
      const 결과 = await api().preview_transcribe(index);
      if (!결과.ok) { 안된까닭(결과, "미리 받아쓰지 못했습니다."); return; }
      미리보기지켜보기();
    }));
  }
  // 강도를 견줘 보는 길.
  //
  // 이 기능은 창구에는 있는데 **화면에 단추가 없어서 아무도 쓸 수 없었다.**
  // 강도가 다섯인데 어느 것이 나은지 재 볼 방법이 없으면, 사용자는 계속
  // 느낌으로만 고르게 된다
  if (job.lines > 0) {
    foot.appendChild(작은단추("다른 강도와 견주기", () => 견주기열기(index)));
  }
  if (foot.children.length) box.appendChild(foot);
  if (견주는칸 === index) box.appendChild(견주기칸(index));

  // 검사표는 홀쭉해도 남는다. 검수하러 들어온 사람이 보라고 있는 것이고,
  // 종류별로 접혀 있어 자리도 얼마 안 먹는다
  if (job.report && job.report.findings.length) box.appendChild(검사표(job.report, index));

  // 미리 받아쓴 것. 도는 중이거나 결과가 있으면 이 트랙 카드 안에 편다
  if (미리상태 && 미리상태.index === index
      && (!미리상태.done || (미리상태.lines || []).length || !미리상태.ok)) {
    box.appendChild(미리받아쓴것칸());
  }
  return box;
}

// ---- 앞 2분만 미리 받아쓰기 ----

let 미리상태 = null;
let 미리시계 = null;

function 미리보기지켜보기() {
  if (미리시계) return;
  미리시계 = setInterval(안전하게(async () => {
    미리상태 = await api().preview_progress();
    다시그리기();
    if (미리상태 && 미리상태.done) {
      clearInterval(미리시계);
      미리시계 = null;
    }
  }, "미리 받아쓰기 상태를 읽지 못했습니다"), 800);
}

function 미리받아쓴것칸() {
  const box = document.createElement("div");
  box.className = "findings preview-out";
  const 말 = document.createElement("div");
  말.className = "why" + (미리상태.ok ? "" : " bad-text");
  말.textContent = (미리상태.done ? "" : "⏳ ") + (미리상태.message || "");
  box.appendChild(말);
  (미리상태.lines || []).forEach((줄) => {
    const row = document.createElement("div");
    row.className = "finding";
    const at = document.createElement("span");
    at.className = "at";
    at.textContent = 줄.at;
    const msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = 줄.ja;
    row.append(at, msg);
    box.appendChild(row);
  });
  return box;
}

// ---- 강도 견주기 ----

let 견주는칸 = -1;
let 견줄강도 = "";

function 견주기열기(index) {
  견주는칸 = 견주는칸 === index ? -1 : index;
  견줄강도 = "";
  다시그리기();
}

function 견주기칸(index) {
  const box = document.createElement("div");
  box.className = "compare";

  const 지금강도 = ((마지막상태.settings || {}).asr || {}).preset || "whisper";
  const 목록 = (마지막상태.presets || []).filter((p) => p.id !== 지금강도);
  if (!견줄강도 && 목록.length) 견줄강도 = 목록[0].id;

  const 말 = document.createElement("p");
  말.className = "why";
  말.textContent =
    "같은 음원을 다른 강도로 한 번 더 받아써서 견줍니다. " +
    "어느 쪽이 낫다고 판정하지 않고 숫자만 냅니다 — 줄이 늘어도 헛소리가 는 것일 수 있습니다.";
  box.appendChild(말);

  const 줄 = document.createElement("div");
  줄.className = "compare-pick";
  const 고르개 = document.createElement("select");
  목록.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.name} (2시간에 ${Math.round(p.minutes_per_hour * 2)}분)`;
    o.selected = p.id === 견줄강도;
    고르개.appendChild(o);
  });
  고르개.onchange = () => { 견줄강도 = 고르개.value; };

  const 시작 = document.createElement("button");
  시작.className = "ghost small";
  시작.textContent = "견주기 시작";
  시작.onclick = async () => {
    시작.disabled = true;
    const 결과 = await api().compare_preset(index, 견줄강도);
    if (!결과.ok) {
      $(`cmp-msg-${index}`).textContent = 결과.message || "견주지 못했습니다";
      시작.disabled = false;
      return;
    }
    견주는거보기(index);
  };
  줄.append(고르개, 시작);
  box.appendChild(줄);

  const 알림 = document.createElement("div");
  알림.className = "why";
  알림.id = `cmp-msg-${index}`;
  box.appendChild(알림);

  const 결과칸 = document.createElement("div");
  결과칸.id = `cmp-out-${index}`;
  box.appendChild(결과칸);
  return box;
}

// **되풀이해서 물어보는 것은 반드시 감싼다.**
//
// `setInterval` 안에서 터지면 시계는 안 멈춘다. 0.8초마다 같은 곳에서 계속
// 터지고, `clearInterval` 이 있는 줄까지 영영 못 간다. 화면에는 「견주는 중…」
// 이 그대로 남아서 **멈춘 것처럼 보이는데 실제로는 쉬지 않고 도는 중**이다.
//
// 눌러서 시작하는 것과 다르다. 그쪽은 `안전하게` 가 한 번 알리고 끝나지만,
// 여기는 끝이 없다.
function 견주는거보기(index) {
  const 보기 = setInterval(async () => {
    let 것;
    try {
      것 = await api().compare_progress();
    } catch (e) {
      clearInterval(보기);
      const 알림 = $(`cmp-msg-${index}`);
      if (알림) 알림.textContent = "견주지 못했습니다: " + (e && e.message ? e.message : e);
      return;
    }
    const 알림 = $(`cmp-msg-${index}`);
    if (알림) 알림.textContent = (것 && 것.message) || "견주는 중…";
    if (!것 || !것.done) return;
    clearInterval(보기);
    const 칸 = $(`cmp-out-${index}`);
    // **결과 모양까지 본다.** `것.left` 가 없으면 그리다 터지는데, 그때는
    // 이미 시계를 껐으니 화면이 「견주는 중…」 인 채로 굳는다
    if (칸 && 것.result && 것.result.left && 것.result.right) {
      try {
        견준것그리기(칸, 것.result);
      } catch (e) {
        if (알림) 알림.textContent = "결과를 그리지 못했습니다: " + (e && e.message ? e.message : e);
      }
    } else if (알림) {
      알림.textContent = 것.message || "견준 결과가 비어 있습니다.";
    }
  }, 800);
}

function 견준것그리기(칸, 것) {
  칸.innerHTML = "";
  const 표 = document.createElement("div");
  표.className = "compare-table";
  [["", 것.left.label, 것.right.label],
   ["줄 수", 것.left.lines, 것.right.lines],
   ["말이 잡힌 시간", `${Math.round(것.left.coverage * 100)}%`,
                      `${Math.round(것.right.coverage * 100)}%`],
   ["망가진 줄", 것.left.broken, 것.right.broken],
  ].forEach((칸들) => {
    칸들.forEach((값) => {
      const c = document.createElement("span");
      c.textContent = String(값);
      표.appendChild(c);
    });
  });
  칸.appendChild(표);

  const 요약 = document.createElement("p");
  요약.className = "why";
  요약.textContent =
    `새로 잡은 곳 ${것.only_right_count}군데, 놓친 곳 ${것.only_left_count}군데. ` +
    "들어 보고 정하세요.";
  칸.appendChild(요약);
}

function 작은단추(글, 할일, 모양) {
  const b = document.createElement("button");
  b.className = "ghost small" + (모양 ? ` ${모양}` : "");
  b.textContent = 글;
  b.onclick = 할일;
  return b;
}

// 「4분 12초 지남 · 약 7분 남음」.
//
// 남은 시간은 **재어 본 속도**로만 말한다. 아직 못 쟀으면 그 말을 아예 안 한다.
// 근거 없는 「1분 남음」이 20분이 되면 그다음부터는 아무것도 안 믿게 된다.
function 걸린시간말(job) {
  const 조각 = [];
  if (job.elapsed_sec > 0) 조각.push(`${시간말(job.elapsed_sec)} 지남`);
  if (job.eta_sec > 0) 조각.push(`약 ${시간말(job.eta_sec)} 남음`);
  else if (job.elapsed_sec > 0) 조각.push("남은 시간 재는 중…");
  // 다 끝난 뒤에는 **얼마나 빨랐는지**를 남긴다. 「느리다」 는 느낌만으로는
  // CPU 로 떨어진 것인지 원래 이만큼인지 가릴 수 없다. 눈금은 아래 도움말에
  const 배 = Number(job["배속"] || 0);
  if (배 > 0) 조각.push(`${배}배속`);
  return 조각.join(" · ");
}

// 「3.2배속」 이 빠른 것인지 느린 것인지 사람은 모른다. 눈금을 붙여 준다
function 배속말(배) {
  const x = Number(배 || 0);
  if (!(x > 0)) return "";
  if (x < 1) return "그래픽카드를 못 쓰고 있을 수 있습니다 (CPU 는 1배속 아래)";
  if (x < 6) return "그래픽카드에 하나씩 넣는 속도입니다";
  return "묶어서 넣고 있습니다";
}

// 「7분」 「1시간 20분」 「40초」. 초 단위까지 흔들리는 숫자를 보여 주면
// 그것만 쳐다보게 된다
function 시간말(초) {
  const s = Math.max(0, Math.round(초));
  if (s < 60) return `${s}초`;
  const 분 = Math.round(s / 60);
  if (분 < 60) return `${분}분`;
  return `${Math.floor(분 / 60)}시간 ${분 % 60}분`;
}

function 검사표(report, index) {
  const box = document.createElement("div");
  box.className = "findings";

  // 짚어 준 자리를 누르면 그 줄로 가서 바로 들려준다.
  // 시각만 보여 주고 끝내면 사용자가 플레이어를 따로 열어 그 자리를 찾아야 한다
  const 가기 = (f) => {
    if (index == null || f.at_sec == null) return null;
    const b = document.createElement("button");
    b.className = "goto";
    b.textContent = "▶ 들어보기";
    b.onclick = (ev) => { ev.stopPropagation(); 받아쓴것보기(index, f.at_sec); };
    return b;
  };

  // 같은 종류가 스무 줄씩 나오면 볼 수가 없다. 종류별로 접어서 세어 준다
  const 종류 = new Map();
  report.findings.forEach((f) => {
    if (!종류.has(f.kind)) 종류.set(f.kind, []);
    종류.get(f.kind).push(f);
  });

  종류.forEach((목록, kind) => {
    const 첫째 = 목록[0];
    const row = document.createElement("div");
    row.className = "finding " + (kind === "미성년의심" ? "막음" : 첫째.severity);

    const at = document.createElement("span");
    at.className = "at";
    at.textContent = 첫째.at;

    const msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = 목록.length === 1 ? 첫째.message : `${요약(kind)} ${목록.length}군데`;
    row.append(at, msg);
    const 단추 = 가기(첫째);
    if (단추) row.appendChild(단추);

    if (목록.length > 1) {
      // 트랙마다 · 종류마다 따로 기억한다. 하나를 펼쳤다고 다른 트랙의
      // 같은 종류까지 펼쳐지면 안 된다
      const 열쇠 = `${index}:${kind}`;
      const 펼쳤나 = !!펼친검사표[열쇠];
      const 더 = document.createElement("button");
      더.className = "more";
      더.textContent = 펼쳤나 ? "접기" : "자세히";
      const 자세히 = document.createElement("div");
      자세히.className = "detail";
      자세히.hidden = !펼쳤나;
      목록.forEach((f) => {
        const d = document.createElement("div");
        d.className = "finding " + f.severity;
        const a = document.createElement("span");
        a.className = "at";
        a.textContent = f.at;
        const m = document.createElement("span");
        m.className = "msg";
        m.textContent = f.message;
        d.append(a, m);
        const 낱개단추 = 가기(f);
        if (낱개단추) d.appendChild(낱개단추);
        자세히.appendChild(d);
      });
      더.onclick = () => {
        펼친검사표[열쇠] = !펼친검사표[열쇠];
        // 마디만 고치면 다음 새로고침에 도로 접힌다. 들고 있는 것을 바꾸고
        // 통째로 다시 그린다
        자세히.hidden = !펼친검사표[열쇠];
        더.textContent = 자세히.hidden ? "자세히" : "접기";
      };
      row.appendChild(더);
      box.append(row, 자세히);
      return;
    }
    box.appendChild(row);
  });
  return box;
}

const 요약말 = {
  긴줄: "자막이 오래 떠 있는 곳",
  빈구간: "말이 오래 없는 곳",
  반복: "같은 말이 되풀이되는 곳",
  자신없음: "자신 없게 받아적은 곳",
  번역없음: "번역이 빠진 곳",
  미성년의심: "미성년 설정으로 보이는 곳",
  적게잡힘: "말이 적게 잡힘",
  안잡힘: "말은 있는데 자막이 없는 곳",
  망가짐: "받아쓰기가 실패한 줄",
  망가진구간: "통째로 망가진 구간",
  빈결과: "말을 하나도 못 잡음",
};
const 요약 = (kind) => 요약말[kind] || kind;

// ---- 자막 검수: 듣고 고치는 곳 ----
//
// 자막이 맞는지는 **들어 봐야** 안다. 글자만 봐서는 받아쓰기가 틀린 것인지,
// 번역이 어색한 것인지, 시각이 밀린 것인지 가릴 수 없다.
//
// 142줄 중 이상한 3줄을 눈으로 찾을 수는 없어서 거르개를 둔다.

let 보는칸 = -1;
let 고친것 = {};
// 일본어를 고친 것. 받아쓰기가 틀린 줄은 한국어만 고쳐서는 반쪽이다
let 고친것ja = {};
let 본줄들 = [];
let 거르개 = "all";
let 소리 = null;          // 지금 나는 소리
let 나는줄 = 0;
// 마지막으로 들어 본 것의 길이들. 멈춘 뒤에도 남는다 — 끝나고 나서 봐야
// 「몇 초를 보냈는데 몇 초에서 끊겼는지」 를 읽을 수 있다
let 잰것 = null;

const 거르개목록 = [
  { id: "all", 이름: "전체", 고르기: () => true },
  { id: "gap", 이름: "말은 있는데 자막 없음", 고르기: (줄) => 줄.uncovered },
  { id: "none", 이름: "번역 없음", 고르기: (줄) => !(줄.ko || "").trim() },
  { id: "bad", 이름: "이상한 줄", 고르기: (줄) => 줄.broken },
];

async function 받아쓴것보기(index, 갈곳, 탭) {
  let 것;
  try {
    것 = await api().transcript(index);
  } catch (e) {
    // 창구가 뜻밖에 터져도 조용히 죽지 않는다
    안된까닭({ message: "받아쓴 것을 열지 못했습니다: " + (e && e.message ? e.message : e) });
    return;
  }
  if (!것.ok) {
    안된까닭(것, "받아쓴 것을 열지 못했습니다.");
    return;
  }

  보는칸 = index;
  고친것 = {};
  고친것ja = {};
  본줄들 = 것.lines || [];
  본간극 = 것.gaps || [];
  거르개 = "all";
  소리멈춤();
  // 파형은 곁들이라 기다리지 않는다. 다 재지면 알아서 띠가 나타난다
  파형싣기();

  // 고치는 곳은 이제 **작업 화면 오른쪽**이다. 번역 화면 나무에서 들어왔을
  // 수도 있으므로 화면을 옮기고, 그 트랙이 든 작품을 왼쪽에서 골라 준다.
  // 안 골라 주면 다른 작품을 보던 왼쪽과 오른쪽이 서로 다른 것을 가리킨다
  const 임자 = (마지막상태.works || []).find(
    (w) => (w.jobs || []).some((j) => j.index === index));
  if (임자) 고른작품 = 임자.key;
  고른트랙 = index;
  선택트랙 = new Set([index]);
  오른쪽모드 = "edit";
  if (지금 !== "work") 보이기("work");

  // 한국어가 있으면 그것을 앞에 세운다. 파일 이름만 적혀 있으면 지금
  // 무엇을 고치고 있는지 못 읽는다
  $("viewer-name").textContent = 것.ko || 것.name;
  $("viewer-name").title = 것.ko ? `${것.ko}\n${것.name}` : 것.name;
  $("viewer-count").textContent = `${본줄들.length}줄`;
  $("viewer-msg").textContent = "";
  다시그리기();
  // 딴 데서 시키지 않는 한 「한 줄씩」 부터다. 지난번에 통째로를 보다
  // 닫았다고 다음에도 거기서 열리면, 듣기 단추를 찾다가 없어서 헤맨다.
  // 「번역」 으로 들어온 경우와 트랙 넘기기만 탭을 지정해서 온다
  await 보기탭(탭 || "one");
  검수그리기(true);

  // 검사표에서 시각을 눌러 들어온 경우. 그 자리로 데려가고 바로 들려준다
  if (갈곳 != null) 그자리로(갈곳);
}

// 맨위로 = true 는 새로 열거나 거르개를 바꿨을 때만이다.
//
// 예전에는 다시 그릴 때마다 무조건 맨 위로 올렸다. 그런데 듣기를 누르면
// 그리기가 다시 도므로, **200번째 줄을 들으려고 누르는 순간 목록이 맨 위로
// 튀어 올랐다.** 듣고 있는 줄이 화면에서 사라진다. 소리가 끝날 때 또 한 번
// 튀었다. 들으면서 글을 보는 것이 이 창의 목적인데 그것이 안 됐다.
function 검수그리기(맨위로) {
  거르개그리기();
  const 몸 = $("viewer-body");
  const 있던자리 = 몸.scrollTop || 0;
  몸.innerHTML = "";
  보일줄().forEach((줄) => 몸.appendChild(고치는줄(줄)));
  몸.scrollTop = 맨위로 ? 0 : 있던자리;
  파형그리기();   // 듣는 줄 표시가 파형에도 따라온다
}

function 보일줄() {
  const 고르기 = (거르개목록.find((f) => f.id === 거르개) || 거르개목록[0]).고르기;
  return 본줄들.filter(고르기);
}

function 거르개그리기() {
  const 칸 = $("viewer-filters");
  칸.innerHTML = "";
  거르개목록.forEach((f) => {
    const 수 = 본줄들.filter(f.고르기).length;
    if (f.id !== "all" && !수) return;   // 없는 것은 보여 주지 않는다
    const b = document.createElement("button");
    b.className = "chip" + (거르개 === f.id ? " on" : "");
    b.textContent = `${f.이름} ${수}`;
    b.onclick = () => { 거르개 = f.id; 검수그리기(true); };
    칸.appendChild(b);
  });
}

function 그자리로(초) {
  const 줄 = 본줄들.reduce(
    (제일, 후보) =>
      Math.abs((후보.at_sec || 0) - 초) < Math.abs((제일.at_sec || 0) - 초) ? 후보 : 제일,
    본줄들[0]
  );
  if (!줄) return;
  // **먼저 그 줄을 보이게 한다.** 검사표에서 1:23:45 를 눌러 들어왔는데
  // 300줄짜리 목록의 맨 위가 보이면, 소리는 나는데 어느 줄인지 알 수 없다
  그줄보이기(줄.n);
  들어보기(줄);
}

function 그줄보이기(n) {
  const row = $(`row-${n}`);
  if (!row) return;
  if (typeof row.scrollIntoView === "function") {
    row.scrollIntoView({ block: "center" });
  }
}

function 고치는줄(줄) {
  const row = document.createElement("div");
  row.className = "edit-row" +
    (줄.uncovered ? " gap" : "") + (줄.broken ? " broken" : "") +
    (나는줄 === 줄.n ? " playing" : "");
  row.id = `row-${줄.n}`;

  // 듣기. 이것 하나가 이 창의 목적이다
  const 듣기 = document.createElement("button");
  듣기.className = "play";
  듣기.textContent = 나는줄 === 줄.n ? "■" : "▶";
  듣기.title = "이 줄만 들어 보기";
  듣기.onclick = () => (나는줄 === 줄.n ? 소리멈춤() : 들어보기(줄));

  const at = document.createElement("span");
  at.className = "at";
  at.textContent = 줄.at;

  const 왼쪽 = document.createElement("span");
  왼쪽.className = "n";
  왼쪽.append(듣기, at);

  const ja칸 = document.createElement("span");
  ja칸.className = "ja";
  // **일본어도 그 자리에서 고친다.** 검사표가 「이상한 줄」 이라고 짚어만
  // 주고 고칠 손이 없었다. 한국어만 고치면 반쪽이다 — 다시 번역하면 틀린
  // 일본어로 또 번역한다
  const ja = document.createElement("span");
  ja.className = "ja-edit";
  ja.contentEditable = "true";
  ja.textContent = 고친것ja[줄.n] != null ? 고친것ja[줄.n] : 줄.ja;
  ja.title = "눌러서 바로 고칠 수 있습니다 (받아쓰기가 틀렸을 때)";
  ja.oninput = () => {
    const 글 = (ja.textContent || "").trim();
    const 바뀜 = 글 !== (줄.ja || "");
    if (바뀜) 고친것ja[줄.n] = 글;
    else delete 고친것ja[줄.n];
    저장단추셈();
  };
  ja칸.appendChild(ja);
  // 왜 걸러졌는지 줄 안에 적는다. 왼쪽 색 띠만으로는 무슨 문제인지 모른다
  if (줄.broken) {
    const 표 = document.createElement("span");
    표.className = "row-badge bad";
    표.textContent = "이상한 줄";
    표.title = "발음이 비슷한 다른 말을 잘못 받아적은 것일 수 있습니다. 들어 보고 일본어를 바로 고치세요";
    ja칸.appendChild(표);
  } else if (줄.uncovered) {
    const 표 = document.createElement("span");
    표.className = "row-badge warn";
    표.textContent = "빈 구간";
    표.title = "말은 있는데 자막이 없는 구간에 걸칩니다";
    ja칸.appendChild(표);
  }

  const ko = document.createElement("input");
  ko.type = "text";
  // 치다가 화면이 다시 그려져도 친 것이 살아 있어야 한다
  ko.value = 고친것[줄.n] != null ? 고친것[줄.n] : (줄.ko || "");
  ko.placeholder = "아직 번역 없음";
  ko.className = 고친것[줄.n] != null ? "changed" : "";
  ko.oninput = () => {
    const 바뀜 = ko.value !== (줄.ko || "");
    if (바뀜) 고친것[줄.n] = ko.value;
    else delete 고친것[줄.n];
    ko.className = 바뀜 ? "changed" : "";
    저장단추셈();
  };

  // 줄 지우기 — 같은 말이 두 번 잡혔을 때. 한 번 더 눌러야 지운다
  const 지우기 = document.createElement("button");
  지우기.className = "row-del";
  지우기.textContent = "✕";
  지우기.title = "이 줄 지우기 (같은 말이 두 번 잡혔을 때)";
  지우기.onclick = 안전하게(async (ev) => {
    ev.stopPropagation();
    if (지우기.dataset.arm !== "1") {
      지우기.dataset.arm = "1";
      지우기.textContent = "지움?";
      setTimeout(() => {
        지우기.dataset.arm = "";
        지우기.textContent = "✕";
      }, 2500);
      return;
    }
    const 결과 = await 되돌릴수있게(() => api().delete_line(보는칸, 줄.n));
    if (!결과.ok) { $("viewer-msg").textContent = 결과.message || "지우지 못했습니다"; return; }
    본줄들 = 본줄들.filter((x) => x.n !== 줄.n);
    delete 고친것[줄.n];
    delete 고친것ja[줄.n];
    $("viewer-count").textContent = `${본줄들.length}줄`;
    $("viewer-msg").textContent = `${줄.n}번 줄을 지웠습니다`;
    검수그리기();
    await 새로고침();
  }, "지우지 못했습니다");

  row.append(왼쪽, ja칸, ko, 지우기);
  return row;
}

/** 고친 줄을 치는 대로 담는다. **저장 단추를 만들지 않는다**(화면 규칙).
 *
 * 「고친 것 저장」 단추가 있으면 고쳐 놓고 안 누른 채 다른 트랙으로 넘어가는
 * 일이 생긴다. ◀▶ 로 트랙을 넘기는 것이 이 창의 쓰는 법이라 더 그렇다. */
let 고친것시계 = null;

function 저장단추셈() {
  const 개수 = Object.keys(고친것).length + Object.keys(고친것ja).length;
  $("viewer-msg").textContent = 개수 ? `${개수}줄 고침 · 담는 중…` : "";
  if (!개수) return;
  if (고친것시계) clearTimeout(고친것시계);
  // 한 글자마다 담으면 창구를 너무 자주 부른다. 손이 멎으면 담는다
  고친것시계 = setTimeout(안전하게(고친것저장, "담지 못했습니다"), 600);
}

async function 고친것바로저장() {
  if (고친것시계) { clearTimeout(고친것시계); 고친것시계 = null; }
  if (!Object.keys(고친것).length && !Object.keys(고친것ja).length) return;
  await 고친것저장();
}

// ---- 파형 띠 ----
//
// 자막 시각이 맞는지는 **소리가 어디 있는지** 를 봐야 가릴 수 있다.
// 「1분 넘게 자막이 없다」 는 자리가 진짜 조용한 것인지 말을 놓친 것인지,
// 파형 위에 자막 구간을 얹으면 눈으로 갈린다. 누르면 그 자리 줄로 간다.

let 파형자료 = null;
let 파형말 = "";
let 본간극 = [];

async function 파형싣기() {
  파형자료 = null;
  // 재는 동안이라고 말해 둔다. 말없이 안 보이면 이 기능이 있는 줄도 모르고,
  // 실패해도 왜 안 되는지 알려 줄 길이 없다
  파형말 = "파형 재는 중… (처음 한 번은 트랙 전체를 읽어 몇 초 걸립니다)";
  파형그리기();
  const 여기 = 보는칸;
  let 것 = null;
  try {
    것 = await api().waveform(여기);
  } catch (e) {
    것 = { ok: false, message: String(e && e.message ? e.message : e) };
  }
  // 재는 동안(3시간짜리는 몇 초 걸린다) 다른 트랙으로 옮겼으면 버린다
  if (보는칸 !== 여기) return;
  if (것 && 것.ok) {
    파형자료 = 것;
    파형말 = "";
  } else {
    파형말 = "파형을 못 쟀습니다 — " + ((것 && 것.message) || "까닭 없음");
  }
  파형그리기();
}

function 파형그리기() {
  const 칸 = $("wave-strip");
  if (!칸) return;
  if (보는칸 < 0 || (!파형자료 && !파형말)) {
    칸.hidden = true;
    return;
  }
  칸.hidden = false;
  const 글 = $("wave-note");
  if (글) {
    글.hidden = !파형말;
    글.textContent = 파형말;
  }
  const canvas = $("wave-canvas");
  if (canvas) canvas.hidden = !파형자료;
  if (!파형자료) return;
  // 시험 DOM 에는 그릴 판이 없다. 띠를 보였다는 것까지만 확인한다
  if (!canvas || typeof canvas.getContext !== "function") return;

  const 폭 = canvas.clientWidth || 800;
  const 높이 = 56;
  canvas.width = 폭 * 2;     // 흐릿하지 않게 두 배로 그린다
  canvas.height = 높이 * 2;
  const ctx = canvas.getContext("2d");
  ctx.scale(2, 2);
  ctx.clearRect(0, 0, 폭, 높이);

  const 길이 = 파형자료.duration || 1;
  const 값들 = 파형자료.peaks || [];

  // 자막 구간 상자를 먼저 깔고 그 위에 곡선을 얹는다
  ctx.fillStyle = "rgba(108, 140, 255, .16)";
  본줄들.forEach((줄) => {
    const a = ((줄.at_sec || 0) / 길이) * 폭;
    const b = ((줄.end_sec || 0) / 길이) * 폭;
    ctx.fillRect(a, 4, Math.max(1, b - a), 높이 - 8);
  });
  // 말이 없는 구간은 노랑. 진짜 조용한 곳인지 여기를 눌러 들어 본다
  ctx.fillStyle = "rgba(210, 153, 34, .18)";
  본간극.forEach((곳) => {
    const a = ((곳.at_sec || 0) / 길이) * 폭;
    const b = ((곳.end_sec || 0) / 길이) * 폭;
    ctx.fillRect(a, 4, Math.max(1, b - a), 높이 - 8);
  });

  // 캔버스는 CSS 변수를 못 읽는다. style.css 의 --line-strong 과 같은 값
  ctx.strokeStyle = "#3a4150";
  ctx.lineWidth = 1;
  ctx.beginPath();
  const 가운데 = 높이 / 2;
  for (let x = 0; x < 폭; x += 1) {
    const i = Math.floor((x / 폭) * 값들.length);
    const 크기 = ((값들[i] || 0) / 100) * (가운데 - 4);
    ctx.moveTo(x + 0.5, 가운데 - Math.max(0.6, 크기));
    ctx.lineTo(x + 0.5, 가운데 + Math.max(0.6, 크기));
  }
  ctx.stroke();

  // 지금 듣는 줄
  const 듣는줄 = 본줄들.find((줄) => 줄.n === 나는줄);
  if (듣는줄) {
    const x = ((듣는줄.at_sec || 0) / 길이) * 폭;
    ctx.strokeStyle = "#f85149";   // --bad 와 같은 값
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, 높이);
    ctx.stroke();
  }
}

function 파형눌림(ev) {
  if (!파형자료 || !본줄들.length) return;
  const canvas = $("wave-canvas");
  const 폭 = (canvas && canvas.clientWidth) || 800;
  const 초 = (ev.offsetX / 폭) * (파형자료.duration || 1);
  // 그 자리에서 제일 가까운 줄로 가서 바로 들려준다
  그자리로(초);
}

function 잰것쓰기() {
  const 줄칸 = $("play-measure-row");
  const 칸 = $("play-measure");
  if (!줄칸 || !칸) return;
  if (!잰것) {
    줄칸.hidden = true;
    칸.textContent = "";
    return;
  }
  const 초 = (v) => (v == null || !isFinite(v) ? "?" : `${v.toFixed(1)}초`);
  const 메가 = (잰것.담긴것 / 1048576).toFixed(2);
  칸.textContent =
    `잰 것 — 자막 ${초(잰것.자막)} · 앱이 보낸 소리 ${초(잰것.보낸것)} (${메가}MB) · ` +
    `브라우저가 받은 길이 ${초(잰것.받은것)} · 실제로 들은 것 ${초(잰것.들은것)}`;
  줄칸.hidden = false;
}

async function 들어보기(줄) {
  소리멈춤();
  $("play-state").textContent = `${줄.at} 꺼내는 중…`;
  // **나뉜 줄은 시각이 짐작이다.** 짐작한 1~2초만 들려주면 숨소리만 나온다.
  // 원래 한 덩이였던 구간을 통째로 들려준다 — 그 말이 그 안에 있다
  const 들을시작 = 줄.play_sec != null ? 줄.play_sec : (줄.at_sec || 0);
  const 들을끝 = 줄.play_end != null ? 줄.play_end : (줄.end_sec || 0);
  const 것 = await api().play_clip(보는칸, 들을시작, 들을끝);
  if (!것.ok) {
    $("play-state").textContent = 것.message || "소리를 꺼내지 못했습니다";
    return;
  }

  나는줄 = 줄.n;
  소리 = new Audio(것.audio);

  // **어디가 짧은지 재서 적는다.** 「소리가 0.5초 들리고 끝난다」 만으로는
  // 앱이 짧게 보낸 것인지, 보낸 것은 긴데 브라우저가 중간에 끊은 것인지
  // 가릴 수 없다. 둘은 고칠 곳이 아주 다르다. 세 값을 나란히 적어 둔다
  잰것 = {
    자막: (줄.end_sec || 0) - (줄.at_sec || 0),
    보낸것: 것.seconds || 0,
    담긴것: 것.bytes || 0,
    받은것: null,
    들은것: null,
  };
  잰것쓰기();
  소리.addEventListener("loadedmetadata", () => {
    잰것.받은것 = 소리.duration;
    잰것쓰기();
  }, { once: true });
  // 앞에 여유를 붙여 잘랐으므로, 실제로 그 대사가 시작되는 자리부터 튼다.
  //
  // **길이를 알기 전에는 자리를 못 잡는다.** 만들자마자 `currentTime` 을
  // 넣으면 브라우저가 그냥 버리고 0 부터 튼다 — 그러면 늘 대사보다 조금
  // 앞에서 시작한다
  const 자리잡기 = () => {
    try { 소리.currentTime = 것.offset || 0; } catch (e) { /* 못 잡으면 처음부터 */ }
  };
  if (소리.readyState >= 1) 자리잡기();
  else 소리.addEventListener("loadedmetadata", 자리잡기, { once: true });
  // 어디까지 듣고 멈췄는지는 `소리멈춤` 이 적는다. 저 혼자 끝났든 손으로
  // 멈췄든 같은 값이 필요하다
  소리.onended = () => 소리멈춤();
  소리.play().catch((e) => {
    $("play-state").textContent = "소리를 내지 못했습니다: " + e;
  });

  // 어긋났을 때 무엇을 들려준 것인지 말할 수 있어야 한다
  $("play-state").textContent =
    `${줄.at} 듣는 중 (${(것.start || 0).toFixed(1)}초부터 · ${(것.offset || 0).toFixed(1)}초 뒤)`;
  $("play-stop").hidden = false;
  검수그리기();
}

// ---- 자막 고치기: 통째로 ----
//
// 한 줄씩 고치는 길은 한두 개 손볼 때 좋다. 번역이 통째로 어색하면 그것이
// 벌이다. 번역 화면과 **같은 두 칸 형식**을 함께 둔다.

let 통째로자료 = null;
// 지금 보는 탭. 트랙을 넘겨도 탭은 그대로 간다
let 탭지금 = "one";

/** 지금 검수 창에 열린 트랙의 작업. 없으면 null */
function 지금보는작업() {
  return (마지막상태.works || [])
    .flatMap((w) => w.jobs || [])
    .find((j) => j.index === 보는칸) || null;
}

async function 보기탭(어느것) {
  탭지금 = 어느것;
  const 통째로 = 어느것 === "all";
  $("viewer-tab-one").className = "tab" + (통째로 ? "" : " on");
  $("viewer-tab-all").className = "tab" + (통째로 ? " on" : "");
  $("viewer-one").hidden = 통째로;
  // 듣기 안내와 「고친 것 저장」은 한 줄씩 쪽 것이다. 통째로에서는 붙여넣고
  // 「넣기」를 누르므로, 그대로 두면 어느 단추를 눌러야 할지 헷갈린다
  $("viewer-play-foot").hidden = 통째로;
  if (!통째로) {
    $("viewer-all").hidden = true;
    $("viewer-tr").hidden = true;
    $("submit-msg").hidden = true;
    오른쪽칸모드();
    return;
  }
  소리멈춤();
  await 통째로칸그리기();
}

// 통째로 탭은 두 얼굴이다 — 번역할 것이 **남은** 트랙이면 복붙 번역 칸,
// 아니면 채워진 번역을 통째로 고치는 칸. 갈림은 여기 한 곳에서만 한다.
// (예전의 번역 화면이 이 탭으로 들어왔다. 트랙 하나에 들어가는 문이
// 두 개라 어디로 들어갔냐에 따라 화면이 달라지던 것을 없앴다)
async function 통째로칸그리기() {
  const 잡 = 지금보는작업();
  const 번역중 = !!잡 && 잡.at >= 0;
  $("viewer-tr").hidden = !번역중;
  $("viewer-all").hidden = 번역중;
  오른쪽칸모드();
  if (번역중) {
    // **큐가 화면을 끌고 가지 않는다.** 열린 트랙으로 큐를 못박는다
    await api().go_to(잡.at);
    await 번역칸그리기();
  } else {
    await 통째로그리기();
  }
}

async function 통째로그리기() {
  const 것 = await api().transcript_text(보는칸);
  if (!것.ok) { $("viewer-all-msg").textContent = 것.message || ""; return; }
  통째로자료 = 것;
  // 창구가 lines 를 안 주면 「undefined줄」 이 찍혔다. 화면은 창구를 믿지 않는다
  $("viewer-all-count").textContent = `${것.lines ?? 0}줄`;

  const 왼쪽 = $("viewer-all-ja");
  왼쪽.innerHTML = "";
  (것.ja || "").split("\n").filter(Boolean).forEach((줄) => {
    const [번호, ...나머지] = 줄.split("\t");
    const row = document.createElement("div");
    row.className = "line";
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = 번호;
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = 나머지.join("\t");
    row.append(n, t);
    왼쪽.appendChild(row);
  });

  // **이미 채워 놓는다.** 번역해 둔 것을 고치러 온 것이지 처음부터 다시
  // 하러 온 것이 아니다
  $("viewer-all-ko").value = 것.ko || "";
  주단추맞추기(true);
}

async function 통째로복사() {
  await 글복사((통째로자료 && 통째로자료.ja) || "");
  const 단추 = $("viewer-all-copy");
  const 원래 = 단추.textContent;
  단추.textContent = "복사했습니다 ✓";
  setTimeout(() => { 단추.textContent = 원래; }, 1400);
}

async function 통째로넣기() {
  const 결과 = await api().submit_transcript(보는칸, $("viewer-all-ko").value || "");
  $("viewer-all-msg").textContent = 결과.message || "";
  if (!결과.ok) return;
  // 넣은 뒤에는 담긴 것을 다시 읽어 온다. 화면에 든 것과 담긴 것이
  // 어긋난 채로 남으면 무엇이 들어갔는지 알 수 없다
  await 통째로그리기();
  await 검수그리기();
  await 새로고침();
}

function 통째로되돌리기() {
  $("viewer-all-ko").value = (통째로자료 && 통째로자료.ko) || "";
  $("viewer-all-msg").textContent = "담긴 것으로 되돌렸습니다.";
}

function 소리멈춤() {
  if (소리) {
    // 손으로 멈춘 것도 어디까지 들었는지 적어 둔다
    if (잰것 && 잰것.들은것 == null) {
      try { 잰것.들은것 = 소리.currentTime; 잰것쓰기(); } catch (e) { /* 못 읽으면 둔다 */ }
    }
    try { 소리.pause(); } catch (e) { /* 이미 멈춘 것 */ }
  }
  소리 = null;
  const 있었다 = 나는줄;
  나는줄 = 0;
  $("play-stop").hidden = true;
  $("play-state").textContent = "";
  if (있었다 && 보는칸 >= 0) 검수그리기();
}

async function 고친것저장() {
  if (!Object.keys(고친것).length && !Object.keys(고친것ja).length) return;
  let 된줄 = 0;
  const 말들 = [];

  if (Object.keys(고친것).length) {
    const 결과 = await api().save_lines(보는칸, 고친것);
    if (!결과.ok) {
      $("viewer-msg").textContent = 결과.message || "저장하지 못했습니다";
      return;
    }
    된줄 += 결과.changed || 0;
    Object.keys(고친것).forEach((n) => {
      const 줄 = 본줄들.find((x) => String(x.n) === String(n));
      if (줄) 줄.ko = 고친것[n];
    });
    고친것 = {};
  }

  // 일본어 고친 것도 담는다. 받아쓰기가 틀린 줄은 여기서 바로잡는다
  if (Object.keys(고친것ja).length) {
    const 결과 = await api().save_ja_lines(보는칸, 고친것ja);
    if (!결과.ok) {
      $("viewer-msg").textContent = 결과.message || "일본어를 저장하지 못했습니다";
      return;
    }
    된줄 += 결과.changed || 0;
    Object.keys(고친것ja).forEach((n) => {
      const 줄 = 본줄들.find((x) => String(x.n) === String(n));
      if (줄) 줄.ja = 고친것ja[n];
    });
    말들.push("일본어를 고친 줄은 번역이 옛 일본어 기준입니다 — 필요하면 그 줄만 다시 번역하세요");
    고친것ja = {};
  }

  $("viewer-msg").textContent =
    `${된줄}줄 담았습니다` + (말들.length ? ` · ${말들[0]}` : "");
  검수그리기();
  await 새로고침();
  다시그리기();
}

function 보기닫기() {
  // 하던 것을 마저 담고 닫는다. 안 그러면 0.6초 기다리던 것이 날아간다
  고친것바로저장();
  // 닫으면 **언제나 목록**이다 (규칙 3). 「아까 번역하던 자리로」 같은
  // 영리함이 화면을 예측 불가로 만들었다.
  //
  // 예전에는 여기서 `보는칸 < 0` 이면 그냥 돌아갔다. Esc 를 거르려던 것인데,
  // Esc 는 부르는 쪽에서 이미 거르고 있었고, 대신 **번역칸에서는 「목록으로」
  // 단추가 보이는 채로 아무 일도 안 했다.** 눌러도 반응이 없는 단추가 된다.
  목록으로();
}

// ---- 번역 (한 화면에서 끝까지) ----

// 작품 → 트랙 → 묶음. **세 층을 그대로 그린다.**
//
// 예전에는 트랙만 일렬로 늘어서 있었다. 작품 셋을 넣으면 트랙 열다섯 개가
// 쭉 늘어서서 어느 작품 것인지 긴 제목 글자로만 가려야 했고, 작품별로
// 넘어갈 방법도 없었다. 표지도 안 넘겨서 눈으로 구별할 수도 없었다.
// 왼쪽 나무는 **작업 화면 것 하나뿐이다.** 번역 화면이 제 나무를 따로
// 그리던 때는 같은 것을 두 벌 만들어 두고 서로 어긋났다.

function 다끝난칸(끝났나) {
  // 오른쪽 두 칸은 붙여넣을 것이 없으니 접고, 대신 무엇을 할 수 있는지
  // 적어 둔다. 빈 화면만 남으면 잘못 들어온 줄 안다
  const 두칸 = $("translate-panes");
  if (두칸) 두칸.hidden = !!끝났나;
  const 칸 = $("all-done");
  if (칸) 칸.hidden = !끝났나;
}

/** 번역 칸이 열려 있으면 클립보드를 본다.
 *
 *  예전에는 「복사하기」 를 누른 뒤에만 켰다. 그런데 답을 받아 오는 사람은
 *  이미 앞 묶음을 복사해 놓고 AI 창을 오가는 중이라, 앱으로 돌아와 다시
 *  복사를 누를 일이 없다. **번역할 것이 떠 있으면 그것이 곧 「답을 기다리는
 *  중」 이다.** 한 번만 켠다 */
let 감시켰나 = false;
async function 감시자동으로켜기() {
  if (감시켰나) return;
  감시켰나 = true;
  try {
    마지막상태 = { ...마지막상태, 감시: await api().감시켜기() };
  } catch (e) { /* 못 켜도 붙여넣기는 그대로 된다 */ }
  감시그리기();
}

async function 번역칸그리기() {
  // 번역 칸은 검수 창 「통째로」 탭 안에만 있다. 안 떠 있으면 그릴 곳이
  // 없다 — 딴 타이머(대기열 지켜보기)가 부를 수 있어서 여기서 거른다
  if (보는칸 < 0 || $("viewer-tr").hidden) return;
  const 것 = await api().prompt();
  // 번역할 것이 떠 있다 = 답을 기다리는 중이다. 여기서 클립보드를 본다
  if (것 && 것.ready) 감시자동으로켜기();
  // 대기열은 단추와 나무가 **같은 것을 보고** 그려야 한다
  if (!지금대기) await 대기열보기();
  // 여러 창에 나눠 맡기는 목록도 여기서 함께 그린다
  병렬그리기(마지막상태);

  if (!것.ready) {
    // 대기열 전체가 끝났다. 이 트랙도 끝났으면 채워진 번역을 통째로
    // 보여 주는 얼굴로 넘어간다. **쫓아내지는 않는다** — 왼쪽 목록과
    // 검수 창은 그대로다
    const 잡 = 지금보는작업();
    if (잡 && 잡.at < 0) { await 통째로칸그리기(); return; }
    다끝난칸(true);
    await 호칭그리기();
    await 검수그리기줄();
    return;
  }
  다끝난칸(false);

  const 지금작품 = (것.tree || []).find((w) => w.now) || {};
  const 지금트랙 = (지금작품.tracks || []).find((t) => t.now);

  // **큐가 열린 트랙에서 다른 트랙으로 넘어갔다면** (마지막 묶음을 넣었거나
  // 「나중에」), 남의 트랙 프롬프트를 이 트랙 창에 말없이 띄우지 않는다 —
  // 그 트랙의 검수 창을 통째로 연다. 이름·줄수·파형까지 다 따라온다
  if (지금트랙 && 지금트랙.at >= 0) {
    const 연것 = (마지막상태.works || [])
      .flatMap((w) => w.jobs || [])
      .find((j) => j.at === 지금트랙.at);
    if (연것 && 연것.index !== 보는칸) {
      await 받아쓴것보기(연것.index, null, "all");
      return;
    }
  }

  지금프롬프트 = 것.text;
  지금원문 = 것.plain || "";
  지금묶음 = { 제목: 것.title || "", 번호: 것.number || 0 };
  복사방식되살리기();
  복사방식그리기();
  묶음목록그리기(것);
  // 자동에서 넘어온 묶음이면 **왜** 넘어왔는지 보여 준다. 여태 사유를
  // 쌓기만 하고 안 읽어서, 거절인지 한도인지 모른 채 복붙만 마주했다
  const 사유칸 = $("handoff-reason");
  사유칸.hidden = !것.reason;
  // 사유가 이미 「AI 가 이 묶음을 거절했습니다」 라고 말한다. 앞에 「자동 번역이
  // 이 묶음을 넘겼습니다:」 를 붙이면 같은 말이 두 번이다
  if (것.reason) 사유칸.textContent = 것.reason;
  // 몇 번 묶음을 복사하는지 밝혀 둔다. 창을 여럿 열어 놓고 오가면
  // 지금 손에 든 것이 몇 번인지 헷갈린다
  $("send-count").textContent = 것.total > 1
    ? `${것.number}번 묶음 · ${것.lines}줄`
    : `${것.lines}줄`;

  $("save-file").hidden = !것.prefers_file;
  $("copy").textContent = 것.is_retry ? "빠진 줄만 복사하기" : "복사하기";
  await 호칭그리기();
  await 검수그리기줄();

  const 미리보기 = $("preview");
  미리보기.innerHTML = "";
  (것.preview || []).forEach((줄) => {
    const row = document.createElement("div");
    row.className = "line";
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = 줄.n;
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = 줄.ja;
    row.append(n, t);
    미리보기.appendChild(row);
  });
  미리보기.scrollTop = 0;
  await 내컴퓨터단추그리기();
  $("paste").focus();
  주단추맞추기(true);
}

// 묶음 목록.
//
// 3시간짜리는 묶음이 열여덟 개다. 예전에는 1번을 넣기 전에 2번이 아예 안
// 보여서, 열여덟 번을 **줄 세워서** 기다려야 했다. 사용자는 채팅 세션을
// 여럿 열어 한꺼번에 돌리고 싶어 한다.
//
// 그래서 전부 늘어놓는다. 아무거나 눌러 복사하고, 답은 아무 순서로나 넣는다.
// **끝난 것도 빼지 않는다.** 무엇을 했고 무엇이 남았는지 보여야 헷갈리지 않는다.
// 복사해서 다른 창에 물려 둔 묶음. **창을 다섯 개 열어 놓고 오가면 어느
// 묶음을 어디 보냈는지 머리로 기억해야 했다.** 복사한 순간을 여기 적어 두고
// 보라 딱지로 남긴다. 답이 들어와 done 이 되면 딱지보다 ✓ 가 이긴다.
// 다시 그려도 살아남아야 하므로 화면 밖 변수다.
let 복사해둔묶음 = {};
let 지금묶음 = { 제목: "", 번호: 0 };

function 복사표시(번호) {
  if (!지금묶음.제목 || !번호) return;
  복사해둔묶음[`${지금묶음.제목}:${번호}`] = true;
}

function 묶음목록그리기(것) {
  const 칸 = $("batch-list");
  const 자리 = $("batch-chips");
  if (!칸 || !자리) return;
  const 묶음들 = 것.batches || [];
  // 하나뿐이면 목록이 아무것도 안 알려 준다. 자리만 먹는다
  칸.hidden = 묶음들.length < 2;
  if (칸.hidden) return;

  const 복사한수 = 묶음들.filter(
    (b) => !b.done && 복사해둔묶음[`${것.title}:${b.number}`]).length;
  $("batch-tally").textContent = `묶음 ${것.batch_done}/${것.total} 끝남`
    + (복사한수 ? ` · 복사해 둔 것 ${복사한수}` : "");
  자리.innerHTML = "";
  묶음들.forEach((묶음) => {
    // 지금 보는 묶음도 복사했으면 표시한다. 「복사하기」 를 누르는 것은 늘
    // 지금 묶음이라, 지금 것을 빼면 누른 그 순간 아무것도 안 바뀌어 보인다
    const 복사됨 = !묶음.done && 복사해둔묶음[`${것.title}:${묶음.number}`];
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip-b"
      + (묶음.done ? " done" : "")
      + (묶음.now ? " now" : "")
      + (묶음.is_retry ? " retry" : "")
      + (복사됨 ? " out" : "");
    b.textContent = 묶음.done ? `✓${묶음.number}` : String(묶음.number);
    b.title = 묶음.is_retry
      ? `${묶음.number}번 · ${묶음.missing}줄이 빠져서 다시 물어볼 것`
      : `${묶음.number}번 · ${묶음.lines}줄 · ${묶음.span}`
        + (복사됨 ? " · 복사해 둠 — 답을 기다리는 중" : "")
        + (묶음.reason ? ` · 자동에서 넘어옴: ${묶음.reason}` : "");
    b.onclick = 안전하게(async () => {
      const 결과 = await api().look_at_batch(묶음.number);
      if (!결과.ok) { 알림(결과.message, "bad"); return; }
      await 번역칸그리기();
    }, "그 묶음을 열지 못했습니다");
    자리.appendChild(b);
  });
}

// 남은 묶음을 한 번에 파일로 내보낸다.
//
// 열여덟 개를 하나씩 복사해 오가느니 한 번에 받아 두고 창마다 나눠 넣는 편이
// 훨씬 빠르다. 사용자가 실제로 하는 방식이 그것이다.
async function 전부파일로() {
  const 것 = await api().all_batches_text();
  if (!것.ok) { 알림(것.message, "bad"); return; }

  // 복사 토글을 그대로 따른다. 화면에서는 번역기용인데 파일만 지시문이
  // 붙어 나오면, 파일 쓰는 사람이 또 손으로 지우게 된다
  복사방식되살리기();
  const 글 = 것.batches.map((b) =>
    `${"=".repeat(60)}\n[ ${b.number}번 묶음 · ${b.lines}줄 · ${b.span} ]\n`
    + `${"=".repeat(60)}\n\n${복사할글(b)}\n`
  ).join("\n\n");

  const 꼬리 = 복사방식 === "plain" ? "원문" : "번역요청";
  내려받기(`${것.title} ${꼬리} ${것.batches.length}묶음.txt`, 글);
  // 내보낸 묶음 전부가 창으로 나간 것이다. 목록에 「복사해 둠」 으로 남긴다
  것.batches.forEach((b) => { 복사해둔묶음[`${것.title}:${b.number}`] = true; });
  알림(`${것.batches.length}묶음을 파일로 내보냈습니다. 창을 나눠서 돌리세요.`, "ok");
  await 번역칸그리기();
}

// ---- 복붙 화면에서 바로 내 컴퓨터 AI 로 넘기기 ----
//
// 브라우저에서 제미나이가 거절하는 것은 이 프로그램이 **볼 수가 없다.**
// 아예 다른 창에서 일어나는 일이다. 그래서 거절을 알아채고 넘겨주는 것이
// 아니라, 단추를 늘 띄워 두고 사용자가 보고 누르게 한다.

let 내컴퓨터돌리는중 = null;

async function 내컴퓨터단추그리기() {
  const 단추 = $("local-now");
  const 한마디 = $("local-now-note");
  if (!단추) return;

  const 것 = await api().local_helper();
  단추.dataset.next = 것.next;
  단추.dataset.url = 것.url || "";
  단추.textContent = 것.label || "";
  단추.className = 것.next === "ready" ? "go" : "go needs";
  // 글 없는 단추는 초록 테두리만 남은 「—」 로 보인다. 창구가 이름을 안 주면 숨긴다
  단추.hidden = !것.label;
  단추.disabled = !!것.busy;
  한마디.textContent = 것.note;
  // 「이 트랙 대기열에 넣기」 단추가 있었는데 뺐다 — 쓴 적이 없다고 했다
}

// 대기열이 지금 어떤지. 나무와 단추가 같은 것을 보고 그린다
let 지금대기 = null;
let 대기시계 = null;

async function 대기열보기() {
  const 것 = await api().queue_state();
  if (!것) return;
  지금대기 = 것;
  // 도는 동안만 들여다본다. 끝났는데 계속 물으면 괜한 일이다
  if ((것.busy || (것.queue || []).length) && !대기시계) {
    대기시계 = setInterval(안전하게(async () => {
      await 대기열보기();
      await 번역칸그리기();
      const 지금 = 지금대기 || {};
      if (!지금.busy && !(지금.queue || []).length) {
        clearInterval(대기시계);
        대기시계 = null;
      }
    }, "대기열을 읽지 못했습니다"), 1500);
  }
}


async function 내컴퓨터로번역(전부) {
  const 단추 = $("local-now");
  const 다음 = 단추.dataset.next;

  // 아직 준비가 안 됐으면 그 단계를 먼저 밟는다. 설정 화면에 안 들어간다
  if (다음 === "install") {
    if (단추.dataset.url) window.open(단추.dataset.url, "_blank");
    알림("설치가 끝나면 다시 눌러 주세요.", "ok");
    return;
  }
  if (다음 === "start") {
    단추.disabled = true;
    단추.textContent = "켜는 중…";
    const 결과 = await api().start_local("");
    알림(결과.message, 결과.ok ? "ok" : "bad");
    await 내컴퓨터단추그리기();
    return;
  }
  if (다음 === "pull") {
    await api().pull_model("", "");
    받는거보기();
    return;
  }

  // **누르자마자** 도는 표시를 띄운다. 창구가 답하기까지 잠깐 비는데,
  // 그동안 아무 반응이 없으면 안 눌린 줄 알고 또 누르게 된다
  단추.disabled = true;
  단추.innerHTML = '<span class="spin"></span>번역을 시작하는 중…';
  진행칸보이기(true);

  const 결과 = await api().translate_locally(!!전부);
  if (!결과.ok) {
    진행칸보이기(false);
    알림(결과.message, "bad");
    await 내컴퓨터단추그리기();
    return;
  }
  내컴퓨터진행보기();
}

function 내컴퓨터진행보기() {
  if (내컴퓨터돌리는중) return;
  const 단추 = $("local-now");
  단추.disabled = true;

  // 첫 물음을 곧바로 한 번 한다. 0.5초를 기다리면 그동안 화면이 멈춰 보인다
  const 물어보기 = 안전하게(async () => {
    const 것 = await api().local_progress();
    진행칸그리기(것);
    if (!것.finished) return;

    clearInterval(내컴퓨터돌리는중);
    내컴퓨터돌리는중 = null;
    진행칸보이기(false);

    // 답을 **칸에 넣어 주기만 한다.** 담는 것은 사용자가 「넣기」를 누를 때다.
    //
    // 예전에는 번역해서 곧바로 담고 자막까지 만들었다. 화면에는 아무 일도
    // 안 일어나 보여서 「단추가 안 눌린다」 로 느껴졌고, 정작 눈으로 볼
    // 기회도 없이 자막이 나와 있었다. 로컬 모델은 밖의 AI 보다 자주 틀린다
    if (것.answer) {
      $("paste").value = 것.answer;
      $("paste").focus();
      알림(것.message, 것.ok ? "ok" : "bad");
      await 내컴퓨터단추그리기();
      return;
    }

    알림(것.message, 것.ok ? "ok" : "bad");
    // 「남은 것 전부」는 담는 길이다. 다음 묶음으로 넘어갔으니 다시 읽는다
    await 번역칸그리기();
  });

  // **차례가 중요하다.** 먼저 걸어 두고 나서 물어본다. 거꾸로 하면, 첫
  // 물음에서 곧바로 끝났을 때 아직 없는 것을 끄려다 놓치고, 그 뒤에 건
  // 것이 영영 안 꺼진다
  내컴퓨터돌리는중 = setInterval(물어보기, 500);
  물어보기();
}

function 진행칸보이기(켤까) {
  const 칸 = $("local-run");
  if (!칸) return;
  칸.hidden = !켤까;
  if (켤까) {
    // 아직 한 줄도 안 왔다. 흐르는 막대로 「멈춘 것이 아니다」만 알린다
    칸.classList.add("waiting");
    $("local-fill").style.width = "";
    $("local-run-note").textContent = "모델을 깨우는 중…";
  }
}

// 받은 줄 수만큼 막대가 찬다.
//
// 한 묶음에 1~3분이 걸린다. 그동안 화면에 아무 표시가 없으면 사용자는 멈춘
// 줄 알고 창을 껐다 켠다. **껐다 켜면 처음부터다.**
//
// 진짜로 몇 줄이 왔는지 센다 — 창구가 답을 흘려 받으면서 세어 준다.
// 지어낸 숫자를 보여 주느니 흐르는 막대만 띄우는 편이 낫다.
function 진행칸그리기(것) {
  const 칸 = $("local-run");
  if (!칸 || 칸.hidden) return;
  const 한것 = 것.done || 0;
  const 모두 = 것.total || 0;
  const 단위 = 것.unit || "묶음";

  if (한것 > 0 && 모두 > 0) {
    칸.classList.remove("waiting");
    $("local-fill").style.width = Math.min(100, Math.round((한것 / 모두) * 100)) + "%";
    $("local-run-note").textContent = `${한것} / ${모두}${단위}`;
  } else {
    칸.classList.add("waiting");
    $("local-run-note").textContent = 것.message || "모델을 깨우는 중…";
  }
  // 아직 한 줄도 안 왔으면 도는 표시를 그대로 둔다. 글자만 바뀌면
  // 멈춘 것처럼 보인다
  const 단추 = $("local-now");
  const 글 = 것.message || "번역 중…";
  if (한것 > 0) 단추.textContent = 글;
  else 단추.innerHTML = '<span class="spin"></span>' + 글;
}

// **한 묶음에 파란 주 단추는 하나다.** 「복사하기」 와 「넣기」 가 둘 다 파랬다 —
// 다음 행동은 하나여야 한다. 붙여넣기 칸이 처음 그려질 때 값에서 달라졌으면
// (뭔가 붙였으면) 「넣기」 가, 아니면 「복사하기」 가 주다.
//
// 세 자리가 같은 짜임이다 — 번역칸 · 검수 통째로 · 제목 번역.
const 주단추짝 = [
  ["copy", "submit", "paste"],
  ["viewer-all-copy", "viewer-all-submit", "viewer-all-ko"],
  ["titles-copy", "titles-submit", "titles-paste"],
];

function 주단추맞추기(스냅) {
  for (const [복사id, 넣기id, 칸id] of 주단추짝) {
    const 복사 = $(복사id), 넣기 = $(넣기id), 칸 = $(칸id);
    if (!복사 || !넣기 || !칸) continue;
    // 그려질 때의 값을 기준으로 삼는다. 검수 통째로는 칸에 번역이 이미 들어
    // 있어서 「비었나」 로는 못 가른다 — 「바뀌었나」 로 가른다
    if (스냅 || 칸.dataset.처음 === undefined) 칸.dataset.처음 = 칸.value;
    const 바뀜 = 칸.value.trim() !== (칸.dataset.처음 || "").trim();
    넣기.classList.toggle("primary", 바뀜);
    넣기.classList.toggle("ghost", !바뀜);
    복사.classList.toggle("primary", !바뀜);
    복사.classList.toggle("ghost", 바뀜);
  }
}

function 알림(글, 어떤것) {
  const box = $("submit-msg");
  box.hidden = false;
  box.className = `notice ${어떤것 || "ok"}`;
  box.textContent = 글;
  // **번역칸이 안 보일 때 부르면 어디에도 안 떴다.** 이 상자는 번역칸 안에
  // 있는데, 작업 화면의 「번역 이어서」(`묶어서복사`) 도 여기로 알린다 —
  // 그 주 단추가 실패해도 조용했다. 상자가 그려지지 않는 자리면(offsetParent
  // 가 없으면) 떠 있는 상자로 한 번 더 알린다
  if (!box.offsetParent) 뜬말(글, 어떤것);
}

async function 글복사(글) {
  try {
    await navigator.clipboard.writeText(글);
  } catch (e) {
    // 클립보드를 막는 환경이 있어 손으로 고를 수 있게 대비한다
    const 임시 = document.createElement("textarea");
    임시.value = 글;
    document.body.appendChild(임시);
    임시.select();
    document.execCommand("copy");
    임시.remove();
  }
}

// 어디에 넣을 것인가. "ai" 는 지시문까지, "plain" 은 번호와 원문만.
// 기계 번역기는 지시문까지 번역해서 돌려주므로 빼 줘야 한다
let 복사방식 = "ai";

const 복사방식이름 = { ai: "AI용", plain: "번역기용" };

/** 고른 길이 정한다. **여기서 따로 담아 두지 않는다** — 같은 뜻을 두 군데
    적으면 언젠가 어긋나고, 그러면 화면에는 「지시문 붙임」인데 나가는 것은
    맨 원문이 된다 */
let 복사방식읽었나 = false;
function 복사방식되살리기() {
  if (복사방식읽었나) return;
  복사방식읽었나 = true;
  복사방식 = 지시문붙이나() ? "ai" : "plain";
}

/** 지금 길에서 지시문을 붙이는가 */
function 지시문붙이나() {
  const 길 = (마지막상태.route || {}).지금 || {};
  return 길.지시문 !== false;
}

/** 이 손잡이가 잠겨 있나. 번역기 길에서는 지시문·가리기를 못 켠다 */
function 잠겼나(이름) {
  return ((마지막상태.route || {}).잠근것 || []).indexOf(이름) >= 0;
}

function 복사방식그리기() {
  const 단추 = $("copy-style");
  if (!단추) return;
  가리기그리기();
  단추.textContent = "복사 형식: " + (복사방식이름[복사방식] || "AI용");
  // **번역기 길에서는 못 바꾼다.** 지시문을 붙여 보내면 번역기가 그것까지
  // 번역해서 돌려준다. 눌러 보고 나서야 알게 두지 않는다
  if (잠겼나("지시문")) {
    단추.disabled = true;
    단추.title = "번역기 길에서는 지시문을 붙이지 않습니다. "
      + "설정 › 번역에서 길을 바꾸면 됩니다";
    return;
  }
  단추.disabled = false;
  단추.title = 복사방식 === "plain"
    ? "번호와 원문만 복사합니다 (구글·파파고용). 누르면 AI용으로 바꿉니다"
    : "지시문까지 복사합니다 (클로드·제미나이용). 누르면 번역기용으로 바꿉니다";
}

/** 민감한 낱말을 표(KW01)로 가려서 보낼지. 거절을 줄이는 대신, 돌아온 답에서
 *  표를 되돌려야 한다. 번역기 길에서는 아예 못 쓴다 */
function 가리기그리기() {
  const 단추 = $("mask-style");
  if (!단추) return;
  const 켬 = ((마지막상태.route || {}).지금 || {}).가리기 !== false;
  단추.textContent = "대체 단어: " + (켬 ? "켬" : "끔");
  if (잠겼나("가리기")) {
    단추.disabled = true;
    단추.title = "번역기는 KW01 같은 표를 그대로 두지 않아 되돌릴 수 없습니다. "
      + "그래서 번역기 길에서는 끕니다";
    return;
  }
  단추.disabled = false;
  단추.title = 켬
    ? "민감한 낱말을 KW01 같은 표로 바꿔 보냅니다. 누르면 끕니다"
    : "낱말을 그대로 보냅니다. 거절당할 수 있습니다. 누르면 켭니다";
}

async function 가리기바꾸기() {
  if (잠겼나("가리기")) return;
  const 켬 = ((마지막상태.route || {}).지금 || {}).가리기 !== false;
  마지막상태 = await api().save_settings(
    { translation: { 고친것: { 가리기: !켬 } } });
  가리기그리기();
  // 이미 짜 둔 묶음을 새 설정으로 다시 짠다. 안 그러면 다음 트랙부터 먹는다
  await 번역칸그리기();
}

async function 복사방식바꾸기() {
  if (잠겼나("지시문")) return;
  복사방식 = 복사방식 === "ai" ? "plain" : "ai";
  복사방식그리기();
  // 손잡이를 건드린 것으로 남긴다. 길을 바꿔도 이것만은 그대로 간다
  마지막상태 = await api().save_settings(
    { translation: { 고친것: { 지시문: 복사방식 === "ai" } } });
}

/** 지금 방식에 맞는 글. `plain` 이 없으면 지시문 붙은 것으로 돌아간다 */
function 복사할글(것) {
  if (!것) return "";
  if (복사방식 === "plain" && 것.plain) return 것.plain;
  return 것.text || "";
}

async function 복사하기() {
  await 글복사(복사방식 === "plain" && 지금원문 ? 지금원문 : 지금프롬프트);
  const 원래 = $("copy").textContent;
  $("copy").textContent = "복사했습니다 ✓";
  setTimeout(() => { $("copy").textContent = 원래; }, 1400);
  // 이 묶음을 어딘가에 붙이러 간 것이다. 목록에 「복사해 둠」 으로 남긴다
  복사표시(지금묶음.번호);
  // **여기서부터 클립보드를 본다.** 복사를 눌렀다는 것은 「이제 답을 받아
  // 올 참이다」 라는 뜻이다. 앱을 켜자마자 보면 남의 것을 볼 뿐이다
  await 감시자동으로켜기();
  await 번역칸그리기();
}

// ---- 클립보드 감시 ----
//
// 켜져 있다는 것이 **늘 보여야** 한다. 몰래 보는 것이 아니다.
// 어디에도 안 적는다 — 클립보드에는 남의 비밀번호도 지나간다

function 감시그리기() {
  const 줄 = $("watch-row");
  if (!줄) return;
  const 것 = 마지막상태.감시 || {};
  // 켠 적이 없으면 아예 안 보인다
  const 켰나 = !!것.켜짐 || !!것.까닭 || !!것.말;
  줄.hidden = !켰나;
  if (!켰나) return;

  const 점 = $("watch-dot");
  if (점) 점.className = "ready-dot" + (것.켜짐 ? " ok" : " warn");

  const 말 = $("watch-text");
  if (말) {
    // **안 켜졌으면 왜인지 그대로 보여 준다.** 「안 된다」 만으로는 못 고친다
    말.textContent = 것.켜짐
      ? (것.말 || "복사한 답을 알아서 넣습니다")
      : (것.까닭 || "감시를 켜지 못했습니다");
  }

  const 물리기 = $("watch-undo");
  if (물리기) 물리기.hidden = !것.되돌릴것;

  // **들어간 줄로 데려가서 번쩍인다.**
  //
  // 이름으로 알리는 데는 한계가 있다. 트랙 이름은 보통 이렇게 길다 —
  // `01 【オホ声】お兄ちゃんの耳元で囁く甘々ASMR ～トラック1～.wav`.
  // 줄이면 못 읽고, 안 줄이면 화면을 잡아먹는다. 읽고 이해하게 만들 것이
  // 아니라 **눈이 그리로 가게** 해야 한다.
  const 횟수 = Number(것.넣은횟수 || 0);
  if (횟수 > 감시가넣은횟수) {
    감시가넣은횟수 = 횟수;
    const 자리 = Number(것.넣은자리);
    if (자리 >= 0) 그줄로데려가기(자리);
  }
}

// 감시가 넣은 것을 몇 번까지 봤는지. 같은 트랙에 두 번 넣어도 알아채야 한다
let 감시가넣은횟수 = 0;

function 그줄로데려가기(자리) {
  // 표의 행은 `trow-<자리>` 다. 트랙을 눌렀을 때 데려가는 것과 같은 자리다
  const 줄 = $(`trow-${자리}`);
  if (!줄) return;
  // 목록 밖에 있으면 데려간다. 번쩍여 봐야 화면 밖이면 못 본다
  if (typeof 줄.scrollIntoView === "function") {
    줄.scrollIntoView({ block: "nearest" });
  }
  번쩍(줄);
}

async function 감시되돌리기() {
  마지막상태 = await api().감시되돌리기();
  감시그리기();
  await 번역칸그리기();
}

async function 감시끄기() {
  // 껐으면 껐다. 다시 그릴 때마다 되살아나면 끌 수가 없다
  감시켰나 = true;
  마지막상태 = { ...마지막상태, 감시: await api().감시끄기() };
  감시그리기();
}

// ---- 첫 실행 ----
//
// **설정을 나열하면 아무도 안 읽는다.** 한 화면에 질문 하나씩 묻고, 고른 것에
// 따라 다음 질문이 달라진다. 고르지 않은 길의 설정은 아예 안 보인다.
//
// 물은 것은 다시 묻지 않는다 — 매번 나오는 인트로는 두 번째부터 방해다.

// 온보딩 화면에 이미 들어갔나. 상태가 0.6초마다 다시 와도 단계를 안 되돌린다
let 온보딩들어감 = false;
let 온보딩길 = "chat";
const 온보딩AI = new Set();

const 길안내 = {
  chat: [
    "음원을 창에 끌어다 놓고 「전부 받아쓰기」 를 누릅니다.",
    "「복사하기」 를 눌러 쓰는 AI 채팅에 붙여넣습니다.",
    "AI 답을 복사해 오른쪽 칸에 붙여넣으면 끝입니다. 몇 번 것인지 안 골라도 됩니다.",
    "거절당하면 다른 AI나 번역기로 넘길 수 있습니다.",
  ],
  translator: [
    "음원을 창에 끌어다 놓고 「전부 받아쓰기」 를 누릅니다.",
    "「복사하기」 를 누르면 설명글 없이 원문만 담깁니다. 번역기에 그대로 넣으세요.",
    "번역기 답을 오른쪽 칸에 붙여넣습니다.",
    "번역기는 줄을 합치는 버릇이 있어, 줄이 빠지면 앱이 짚어 줍니다.",
  ],
  endpoint: [
    "설정 › 번역에서 내 컴퓨터 AI(Ollama)나 API 주소를 넣습니다.",
    "음원을 끌어다 놓고 「전부 받아쓰기」 를 누르면 받아쓰기와 번역이 이어서 돕니다.",
    "거절당한 묶음만 복붙으로 넘어옵니다.",
  ],
};

function 온보딩그리기() {
  const 칸 = $("onb-how");
  if (!칸) return;
  칸.textContent = "";
  for (const 말 of 길안내[온보딩길] || []) {
    const 줄 = document.createElement("li");
    줄.textContent = 말;
    칸.appendChild(줄);
  }
}

// 지금 몇 번째 단계인가. 다음 단계가 **어느 쪽에서 들어올지** 정하는 데 쓴다
let 온보딩지금 = 1;

function 온보딩단계(번호) {
  // 뒤로 가는 중이면 화면이 왼쪽에서 돌아온다. 앞으로 가면 오른쪽에서
  // 들어온다. 어느 쪽으로 움직였는지가 「되돌릴 수 있다」 를 말해 준다
  const 판 = $("screen-onb");
  if (판 && 판.classList && 판.classList.toggle) {
    판.classList.toggle("뒤로", 번호 < 온보딩지금);
  }
  온보딩지금 = 번호;
  for (const n of [1, 2, 3]) {
    const 칸 = $(`onb-${n}`);
    if (칸) 칸.hidden = n !== 번호;
  }
  if (번호 === 3) 온보딩그리기();
}

function 온보딩붙이기() {
  // **id 로 붙인다.** 화면 시험의 가짜 DOM 은 `querySelectorAll` 을 모른다
  for (const 길 of ["chat", "translator", "endpoint"]) {
    const 단추 = $(`onb-route-${길}`);
    if (!단추) continue;
    단추.onclick = () => {
      온보딩길 = 길;
      // 복붙 길만 「어떤 AI 쓰세요」 를 묻는다. 나머지는 물을 것이 없다
      온보딩단계(길 === "chat" ? 2 : 3);
    };
  }
  for (const 이름 of ["chatgpt", "claude", "gemini", "grok"]) {
    const 단추 = $(`onb-ai-${이름}`);
    if (!단추) continue;
    단추.onclick = () => {
      if (온보딩AI.has(이름)) 온보딩AI.delete(이름);
      else 온보딩AI.add(이름);
      if (단추.classList && 단추.classList.toggle) {
        단추.classList.toggle("on", 온보딩AI.has(이름));
      }
    };
  }
  if ($("onb-next")) $("onb-next").onclick = () => 온보딩단계(3);
  // 되돌아갈 길이 없으면 잘못 고른 사람은 앱을 껐다 켜는 수밖에 없다.
  // 3단계에서 뒤로는 **고른 길에 따라** 다르다 — 복붙 길만 2단계를 거쳤다
  if ($("onb-back-2")) $("onb-back-2").onclick = () => 온보딩단계(1);
  if ($("onb-back-3")) {
    $("onb-back-3").onclick = () => 온보딩단계(온보딩길 === "chat" ? 2 : 1);
  }
  if ($("onb-done")) {
    $("onb-done").onclick = 안전하게(async () => {
      마지막상태 = await api().온보딩끝(온보딩길, [...온보딩AI]);
      보이기("work");
      작업그리기();
    }, "시작하지 못했습니다");
  }
}

// ---- 여러 창에 나눠 맡기기 ----
//
// **창을 여럿 열어 트랙을 하나씩 맡기는 것이 실제로 쓰는 방식이다.** 한 세션에
// 여러 트랙을 몰아넣으면 답이 길어져 중간에 끊기고 「계속」 을 쳐야 하는데,
// 트랙마다 다른 창에 맡기면 그럴 일이 없고 동시에 돈다.
//
// 답은 번호만 보고 제자리로 가므로(`_답의주인`) **넣는 차례는 아무래도 된다.**
// 끝나는 대로 복사해서 아무 데나 붙여넣으면 된다.

function 병렬그리기(상태) {
  const 칸 = $("par-list");
  const 요약 = $("par-sum");
  if (!칸) return;

  const 트랙들 = ((상태 || {}).queue || {}).groups || [];
  if (트랙들.length < 2) {
    // 트랙이 하나뿐이면 나눠 맡길 것이 없다
    const 상자 = $("par-box");
    if (상자) 상자.hidden = true;
    return;
  }
  const 상자 = $("par-box");
  if (상자) 상자.hidden = false;

  const 남은것 = 트랙들.filter((g) => !g.done).length;
  if (요약) 요약.textContent = `· 트랙 ${트랙들.length}개 중 ${남은것}개 남음`;

  칸.textContent = "";
  for (const g of 트랙들) {
    const 줄 = document.createElement("div");
    줄.className = "par-row" + (g.done ? " done" : "");

    const 이름 = document.createElement("span");
    이름.className = "par-name";
    이름.textContent = g.track_name || g.title || "";
    줄.appendChild(이름);

    const 상태말 = document.createElement("span");
    상태말.className = "muted par-state";
    상태말.textContent = g.done
      ? "자막 나옴"
      : `${g.lines_done || 0} / ${g.lines_total || 0}줄`;
    줄.appendChild(상태말);

    if (!g.done) {
      const 단추 = document.createElement("button");
      단추.className = "ghost small";
      단추.textContent = "복사";
      단추.onclick = 안전하게(async () => {
        const 것 = await api().prompt(g.key);
        if (!것 || !것.ready) return;
        await 글복사(복사방식 === "plain" && 것.plain ? 것.plain : 것.text);
        단추.textContent = "복사함 ✓";
        setTimeout(() => { 단추.textContent = "복사"; }, 1400);
      }, "복사하지 못했습니다");
      줄.appendChild(단추);
    }
    칸.appendChild(줄);
  }
}

// ---- 제목 번역 ----
//
// 넣어 둔 작품 **전부**의 제목이 한꺼번에 뜬다. 자막과 같은 두 칸 형식이라
// 새로 익힐 것이 없다. 왼쪽에 원문, 오른쪽에 붙여넣기.

let 제목자료 = null;
// **제목은 작품에 딸린 것이다.** 예전에는 나무에서 작품들과 같은 층에 선
// 입구로 들어가 넣어 둔 작품 **전부**가 한꺼번에 떴다. 어느 작품 일을
// 하는 중인지 화면이 말하지 못한다 (규칙 6). 이제는 그 작품 머리에서
// 들어오고, 들어온 작품 것만 뜬다. `null` 이면 지름길 — 전부다
let 제목보는작품 = null;

/** 이 작품의 제목 번역으로 들어간다. 작품 머리의 단추가 부른다 */
function 제목칸열기(열쇠) {
  제목보는작품 = 열쇠 || null;
  보이기("titles");
}

async function 제목칸그리기() {
  제목자료 = await api().titles();
  const 전부 = 제목자료.works || [];
  // 들어온 그 작품 것만. 그 작품이 없어졌으면(빼기 등) 전부로 돌아간다
  const 이것 = 제목보는작품
    ? 전부.filter((w) => w.key === 제목보는작품) : [];
  const 작품들 = 이것.length ? 이것 : 전부;
  const 한작품인가 = 이것.length === 1;
  제목보는작품 = 한작품인가 ? 제목보는작품 : null;

  // **어느 작품 일을 하는 중인지 화면이 말한다.** 「작품 5개」 만 떠 있으면
  // 방금 어디서 들어왔는지 알 수 없다
  $("titles-h").textContent = 한작품인가 ? "이 작품 제목 번역" : "제목 번역";
  $("titles-sub").textContent = 한작품인가
    ? (작품들[0].ko || 작품들[0].ja)
    : `모든 작품 ${작품들.length}개`;
  if ($("titles-all")) $("titles-all").hidden = !한작품인가 || 전부.length < 2;

  const 보낼글 = 한작품인가 ? (작품들[0].text || "") : (제목자료.text || "");
  const 줄수 = 보낼글.split("\n").filter(Boolean).length;
  $("titles-count").textContent = `${줄수}줄`;

  const 미리 = $("titles-preview");
  미리.innerHTML = "";
  보낼글.split("\n").filter(Boolean).forEach((글) => {
    const [번호, ...나머지] = 글.split("\t");
    const row = document.createElement("div");
    row.className = "line";
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = 번호;
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = 나머지.join("\t");
    row.append(n, t);
    미리.appendChild(row);
  });

  제목목록그리기(작품들);
  주단추맞추기(true);
}

function 제목목록그리기(작품들) {
  const 곳 = $("titles-list");
  곳.innerHTML = "";
  작품들.forEach((작품) => 곳.appendChild(제목작품칸(작품)));
}

function 제목작품칸(작품) {
  const box = document.createElement("section");
  box.className = "pane title-work";

  const 머리 = document.createElement("header");
  머리.className = "pane-head";
  const 이름 = document.createElement("strong");
  이름.textContent = 작품.ko || 작품.ja;
  const 곁 = document.createElement("span");
  곁.className = "muted";
  // **상태가 둘이다.** 번역은 했는데 이름은 아직 안 바꾼 것이 정상이고
  // 오히려 기본이다. 하나만 보여 주면 어느 쪽인지 알 수 없다
  곁.textContent =
    (작품.translated ? "번역됨" : "아직") + " · " +
    (작품.renamed ? "이름 바뀜" : "원래대로");
  머리.append(이름, 곁);
  box.appendChild(머리);

  if (작품.too_many) {
    const 말 = document.createElement("div");
    말.className = "notice bad";
    말.textContent = "트랙이 99개를 넘어 번호대가 겹칩니다. 이 작품은 따로 뽑아 주세요.";
    box.appendChild(말);
  }

  const 몸 = document.createElement("div");
  몸.className = "pane-body";
  [{ n: 작품.n, ja: 작품.ja, ko: 작품.ko, 작품제목: true }].concat(작품.tracks || [])
    .forEach((줄) => 몸.appendChild(제목줄(작품, 줄)));
  box.appendChild(몸);

  // **이 작품만** 하는 길. 전부 아니면 한 줄씩밖에 없으면, 작품 열 개를
  // 넣어 두고 한 작품만 다시 하고 싶을 때 길이 없다
  const 혼자 = document.createElement("footer");
  혼자.className = "pane-foot quiet";
  const 혼자복사 = document.createElement("button");
  혼자복사.className = "primary small";
  혼자복사.textContent = "이 작품 제목 복사";
  혼자복사.onclick = 안전하게(
    () => 작품제목복사(작품, 혼자복사), "복사하지 못했습니다");
  const 혼자칸 = document.createElement("input");
  혼자칸.type = "text";
  혼자칸.placeholder = "이 작품 답을 여기에 붙여넣으세요";
  const 혼자넣기 = document.createElement("button");
  혼자넣기.className = "ghost small";
  혼자넣기.textContent = "넣기";
  혼자넣기.onclick = 안전하게(async () => {
    // 번호가 작품마다 갈라져 있어서, 무엇을 넣든 제 작품으로 간다.
    // 그래서 전체 넣기와 같은 창구를 그대로 쓴다
    const 결과 = await api().submit_titles(혼자칸.value || "");
    제목알림(결과.message, 결과.ok ? "ok" : "bad");
    if (결과.ok) 혼자칸.value = "";
    await 제목칸그리기();
  }, "넣지 못했습니다");
  혼자.append(혼자복사, 혼자칸, 혼자넣기);
  box.appendChild(혼자);

  const 발 = document.createElement("footer");
  발.className = "pane-foot";
  const 바꾸기 = document.createElement("button");
  바꾸기.className = "go small";
  바꾸기.textContent = "파일 이름 바꾸기";
  바꾸기.onclick = 안전하게(() => 이름바꾸기(작품.key), "이름을 바꾸지 못했습니다");
  const 되돌리기 = document.createElement("button");
  되돌리기.className = "ghost small";
  되돌리기.textContent = "원래 이름으로";
  되돌리기.hidden = !작품.renamed;
  되돌리기.onclick = 안전하게(() => 이름되돌리기(작품.key), "되돌리지 못했습니다");
  발.append(바꾸기, 되돌리기);
  box.appendChild(발);
  return box;
}

function 제목줄(작품, 줄) {
  const row = document.createElement("div");
  row.className = "line";
  const n = document.createElement("span");
  n.className = "n";
  n.textContent = 줄.n;
  const ja = document.createElement("span");
  ja.className = "ja";
  ja.textContent = 줄.ja;
  const ko = document.createElement("input");
  ko.type = "text";
  ko.value = 줄.ko || "";
  ko.placeholder = 줄.작품제목 ? "작품 제목" : "아직 번역 없음";
  // 손으로 적은 것은 검사하지 않는다. 사용자가 직접 친 것이다
  ko.onchange = 안전하게(async () => {
    const 결과 = await api().save_title(작품.key, 줄.n, ko.value);
    if (!결과.ok) { 제목알림(결과.message, "bad"); return; }
    await 제목칸그리기();
  }, "적은 것을 담지 못했습니다");
  row.append(n, ja, ko);
  return row;
}

async function 작품제목복사(작품, 단추) {
  // 화면에 든 것을 쓴다. 창구를 다시 부르면 그 사이에 목록이 바뀌어
  // 눌러 놓은 작품과 다른 것을 복사할 수 있다
  await 글복사(작품.text || "");
  const 원래 = 단추.textContent;
  단추.textContent = "복사했습니다 ✓";
  setTimeout(() => { 단추.textContent = 원래; }, 1400);
}

async function 제목복사() {
  // **화면에 뜬 것을 복사한다.** 이 작품 것만 떠 있는데 전부가 복사되면,
  // AI 에 붙여넣고 나서야 안다
  const 것들 = (제목자료 && 제목자료.works) || [];
  const 이것 = 제목보는작품 ? 것들.find((w) => w.key === 제목보는작품) : null;
  await 글복사(이것 ? (이것.text || "") : ((제목자료 && 제목자료.text) || ""));
  const 원래 = $("titles-copy").textContent;
  $("titles-copy").textContent = "복사했습니다 ✓";
  setTimeout(() => { $("titles-copy").textContent = 원래; }, 1400);
}

async function 제목넣기() {
  const 결과 = await api().submit_titles($("titles-paste").value || "");
  제목알림(결과.message, 결과.ok ? "ok" : "bad");
  if (결과.ok) $("titles-paste").value = "";
  await 제목칸그리기();
}

async function 이름바꾸기(열쇠) {
  const 결과 = await api().rename_files(열쇠);
  제목알림(결과.message + 막힌것말(결과), 결과.ok ? "ok" : "bad");
  await 제목칸그리기();
  await 새로고침();
}

async function 이름되돌리기(열쇠) {
  const 결과 = await api().revert_names(열쇠);
  제목알림(결과.message + 막힌것말(결과), 결과.ok ? "ok" : "bad");
  await 제목칸그리기();
  await 새로고침();
}

function 막힌것말(결과) {
  // 무엇이 막혔는지 안 보여 주면 사용자는 파일을 하나씩 열어 보게 된다
  const 것들 = 결과.blocked || 결과.fixed || [];
  return 것들.length ? "\n" + 것들.join("\n") : "";
}

function 제목알림(글, 어떤것) {
  const box = $("titles-notice");
  box.hidden = !글;
  box.className = `notice ${어떤것 || "ok"}`;
  box.textContent = 글 || "";
}

// ---- 호칭 ----
//
// 「お兄さん → 언니」 는 한 번 틀리면 그 작품 내내 틀린다. 사람이 한 줄
// 적어 주면 끝나는 일이라 그것만 받는다. 여러 줄을 한 칸에 담으려고
// 세미콜론으로 잇는다 — 칸을 크게 만들면 용어집처럼 보여서 자꾸 늘어난다.

async function 호칭그리기() {
  const 것 = await api().names();
  $("names-row").hidden = !것.ok;
  if (!것.ok) return;
  if (document.activeElement !== $("names-text")) {
    $("names-text").value = (것.text || "").split("\n").filter(Boolean).join(" ; ");
  }
}

/** 호칭도 치는 대로 담는다. **저장 단추를 만들지 않는다**(화면 규칙).
 *
 * 「담기」 단추가 있으면 적어 놓고 안 누른 채 복사해 가는 일이 생긴다.
 * 그러면 호칭이 안 걸린 프롬프트가 나가고, 그것은 자막이 나온 뒤에야 안다. */
let 호칭저장시계 = null;

function 호칭곧담기() {
  if (호칭저장시계) clearTimeout(호칭저장시계);
  호칭저장시계 = setTimeout(안전하게(호칭담기, "호칭을 담지 못했습니다"), 500);
}

async function 호칭담기() {
  if (호칭저장시계) { clearTimeout(호칭저장시계); 호칭저장시계 = null; }
  const 적은것 = ($("names-text").value || "").split(";").join("\n");
  const 결과 = await api().save_names(적은것) || {};
  $("names-msg").textContent = 결과.message || "";
  if (결과.ok) {
    // **치는 중에 칸을 갈아 끼우지 않는다.** 커서가 튀고 친 것이 날아간다
    if (document.activeElement !== $("names-text")) {
      $("names-text").value =
        (결과.text || "").split("\n").filter(Boolean).join(" ; ");
    }
    // 담기만 하면 화면에 떠 있는 프롬프트는 옛것이다. 복사해 가도 안 들어간다
    await 번역칸그리기();
  }
  setTimeout(() => { $("names-msg").textContent = ""; }, 2500);
}

// ---- 검수 ----

async function 검수그리기줄() {
  const 것 = await api().review_prompt();
  $("review-row").hidden = !것.ready;
  $("submit-review").hidden = !것.ready;
  검수할것 = 것.ready ? 것 : null;
}

async function 검수복사() {
  const 것 = await api().review_prompt();
  if (!것.ready) { $("review-msg").textContent = 것.message || ""; return; }
  글복사(복사할글(것));
  $("review-msg").textContent = `${것.number}번 묶음 ${것.lines}줄 · 복사했습니다 ✓`;
  setTimeout(() => { $("review-msg").textContent = ""; }, 2500);
}

async function 검수넣기() {
  const 결과 = await api().submit_review($("paste").value || "");
  $("review-msg").textContent = 결과.message || "";
  if (결과.ok && 결과.changed) $("paste").value = "";
  await 새로고침();
  await 번역칸그리기();
}

async function 내컴퓨터로검수() {
  const 결과 = await api().local_review();
  if (!결과.ok) { 안된까닭(결과, "검수하지 못했습니다."); return; }
  진행칸보이기(true);
  내컴퓨터진행보기();
}

function 내려받기(이름, 글) {
  const blob = new Blob([글], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = 이름;
  a.click();
  URL.revokeObjectURL(a.href);
}

function 파일로저장() {
  // 복사 토글을 그대로 따른다
  const 번역기용 = 복사방식 === "plain" && 지금원문;
  내려받기(
    번역기용 ? "원문.txt" : "번역해주세요.txt",
    번역기용 ? 지금원문 : 지금프롬프트,
  );
}

// 이미 있는 자막을 갈아끼워도 좋다고 했는지. **한 번 더 눌러야 한다.**
// 손으로 고쳐 둔 자막이면 갈아끼운 뒤에는 되돌릴 길이 없다
let 덮어쓰기확인 = false;

async function 넣기() {
  const 답 = $("paste").value.trim();
  const box = $("submit-msg");
  // 빈 칸이면 **말을 하고** 돌아간다. 말없이 돌아가면 단추가 고장 난 줄 안다 —
  // 복사만 하고 붙여넣기를 잊은 때가 대부분이라 무엇이 빠졌는지 알려 준다
  if (!답) {
    box.hidden = false;
    box.textContent = "붙여넣을 것이 없습니다. 위 칸에 AI 의 답을 붙여넣으세요.";
    box.className = "notice bad";
    return;
  }

  const 결과 = await api().submit(답, 덮어쓰기확인) || {};
  box.hidden = false;
  box.textContent = 결과.message || "넣지 못했습니다.";
  box.className = "notice " + (결과.ok ? "ok" : "bad");

  // 물어보는 중이면 담긴 것이 없다. 단추 글자를 바꿔 무엇을 하는지 밝힌다
  덮어쓰기확인 = !!결과.needs_confirm;
  $("submit").textContent = 덮어쓰기확인 ? "덮어쓰기" : "넣기";
  if (결과.needs_confirm) return;

  if (!결과.ok) return;

  $("paste").value = "";
  await 새로고침();
  if (결과.done) {
    // 대기열 전체가 끝났다. 이 트랙의 채워진 번역을 통째로 보여 준다 —
    // 방금 넣은 것이 실제로 들어갔는지 눈으로 보인다
    await 보기탭("all");
    알림(결과.message, "ok");
    return;
  }
  // 화면 안 바뀌고 다음 묶음으로. 이 트랙이 끝났으면 대기열의 다음
  // 트랙 검수 창이 통째로 열린다 (번역칸그리기가 따라간다)
  번역칸그리기();
}

// ---- 설정 ----

function 설정열기() {
  // 저장된 값은 화면을 **열 때만** 읽는다.
  // 그릴 때마다 읽으면, 방금 고른 것을 곧바로 옛날 값으로 덮어써서
  // 눌러도 제자리로 돌아온다. 아무 반응이 없는 것처럼 보인다.
  const s = 마지막상태.settings || {};
  고른공급자 = ((마지막상태.route || {}).지금 || {}).보내는길 || "manual";
  고른길 = ((마지막상태.route || {}).지금 || {}).route || "chat";
  설정그리기(true);
  // 판올림·그래픽카드는 설정 탭 안에 있다. `탭보이기("set")` 이 잰다
  탭보이기("set");
}

// ── 점검 ────────────────────────────────────────────────────────────────
//
// 배치 파일을 찾아 띄우는 것이 귀찮다는 말이 여러 번 나왔다. 하는 일은 전부
// 이 앱과 같은 파이썬이 하는 것이라 안에 넣지 못할 이유가 없다.
//
// 다만 업데이트와 그래픽카드 고치기는 **지금 돌고 있는 파일을 건드린다.**
// 그래서 앱이 스스로 하지 않고, 배치를 띄우고 앱은 죽는다. 눌렀는데 앱이
// 사라지는 것은 놀랄 일이므로 **누르기 전에 무슨 일이 일어날지 적어 둔다.**

function 탭보이기(어느것) {
  [["set", "tab-btn-set"], ["word", "tab-btn-word"]]
    .forEach(([이름, 단추]) => {
      const b = $(단추);
      if (b) b.className = "tab" + (이름 === 어느것 ? " on" : "");
    });
  $("tab-set").hidden = 어느것 !== "set";
  $("tab-word").hidden = 어느것 !== "word";
  // **낱말 칸에서는 거짓말이 된다.** 여기는 저장을 눌러야 저장된다
  $("saved-note").hidden = 어느것 === "word";
  // 글상자가 남는 자리를 다 먹게 한다. 딴 칸은 예전 그대로 흐른다
  $("screen-settings").className = "screen" + (어느것 === "word" ? " 낱말펼침" : "");
  // **판올림·그래픽카드는 이제 설정 탭 안에 있다.** 옛 「점검」 탭에서만
  // 읽어 오게 두면 「알아보는 중…」 이 영영 안 바뀐다
  if (어느것 === "set") 점검그리기();
  if (어느것 === "word") 낱말그리기();
}

// ---- 낱말 ----
//
// 목록이 둘인데 **모양이 같아서 칸 하나로 쓴다.** 하나는 「내 낱말」(내가
// 정한 대로 옮긴다), 하나는 「위험낱말」(미성년 설정을 짚는다).
//
// 글상자 하나로 추가·삭제·수정·끄기가 다 된다. 이 앱은 원래 붙여넣기
// 중심이라 사용자가 새로 배울 것이 없다.
//
// **고른 종류는 여기 들고 있는다.** DOM 에만 두면 다시 그릴 때 날아간다 —
// 검사표 「자세히」가 그것 때문에 안 눌렸다.
let 고른낱말 = "내낱말";
// 마지막으로 그려 넣은 글. 안 저장한 것이 있는지 이것과 견준다
let 낱말그린것 = "";

const 낱말안내 = {
  내낱말: "한 줄에 하나씩. <b>왼쪽이 일본어, 오른쪽이 나올 말</b>입니다."
    + " 오른쪽을 비우면 <b>일본어가 그대로</b> 나옵니다."
    + " 줄 앞에 <code>#</code> 을 붙이면 지우지 않고 <b>끕니다</b>."
    + "<br>보기: <code>ちんぽ → 자지</code> · <code>んちゅ →</code>"
    + " · <code># まんこ → 보지</code>",
  위험낱말: "미성년 설정을 짚는 말입니다. <b>[강한말]</b> 은 하나만 걸려도,"
    + " <b>[약한말]</b> 은 둘 이상 겹쳐야 ⚠ 가 뜹니다."
    + " 줄 앞에 <code>#</code> 을 붙이면 끕니다."
    + "<br><b>이 목록은 번역을 맡기기 전에 멈추라는 신호입니다.</b>"
    + " 느슨하게 하면 계정이 정지될 수 있습니다.",
};

async function 낱말그리기() {
  const 칸 = $("word-text");
  if (!칸) return;
  [["내낱말", "word-kind-mine"], ["위험낱말", "word-kind-risk"]]
    .forEach(([이름, 단추]) => {
      const b = $(단추);
      if (b) b.className = "tab" + (이름 === 고른낱말 ? " on" : "");
    });
  $("word-help").innerHTML = 낱말안내[고른낱말] || "";
  $("word-said").textContent = "";
  try {
    const 것 = await api().word_list(고른낱말);
    칸.value = 것.글 || "";
    낱말그린것 = 칸.value;
  } catch (e) {
    $("word-said").textContent = "읽지 못했습니다: " + (e && e.message ? e.message : e);
  }
}

async function 낱말고르기(어느것) {
  // **묻지 않는다.** 여기 있던 「저장하지 않은 것이 있습니다」 팝업은
  // 저장 단추가 있어서 생긴 것이었다. 이제 치는 대로 저장되므로 「저장 안
  // 한 것」 이라는 상태 자체가 없다.
  //
  // 칸을 옮기기 전에 하던 것을 마저 담는다. 0.4초 기다리던 것이 남아 있을
  // 수 있다
  await 낱말바로저장();
  고른낱말 = 어느것;
  await 낱말그리기();
}

/** 치던 것이 멎으면 담는다. **저장 단추를 만들지 않는다**(화면 규칙).
 *
 * 설정 화면은 처음부터 그렇게 돼 있었는데 낱말 화면만 단추를 달고 있었다.
 * 그 탓에 「저장 안 한 상태」 가 생겼고, 그것을 막으려고 팝업이 붙었다.
 * 규칙 하나를 어기면 그 자리에서 안 끝난다. */
let 낱말저장시계 = null;

function 낱말곧저장() {
  if (낱말저장시계) clearTimeout(낱말저장시계);
  const 말 = $("word-said");
  if (말) 말.textContent = "…";
  // 한 글자마다 담으면 창구를 너무 자주 부른다. 손이 멎으면 담는다
  낱말저장시계 = setTimeout(안전하게(낱말바로저장, "담지 못했습니다"), 400);
}

async function 낱말바로저장() {
  if (낱말저장시계) { clearTimeout(낱말저장시계); 낱말저장시계 = null; }
  const 칸 = $("word-text");
  if (!칸 || (칸.value || "") === (낱말그린것 || "")) return;
  await 낱말저장();
}

async function 낱말저장() {
  const 말 = $("word-said");
  try {
    const 것 = await api().word_save(고른낱말, $("word-text").value || "");
    if (!것.ok) { 말.textContent = 것.message || "저장하지 못했습니다."; return; }
    // **치는 중에 칸을 갈아 끼우지 않는다.** 커서가 튀고 친 것이 날아간다.
    // 창구가 다듬어 돌려준 글은 손이 떠나 있을 때만 넣는다
    if (document.activeElement !== $("word-text")) {
      $("word-text").value = 것.글 || "";
    }
    낱말그린것 = $("word-text").value;
    // 번역 전 트랙은 새 목록을 바로 쓴다. 그랬으면 그렇게 말해 준다 —
    // 복사해 둔 프롬프트가 있으면 다시 복사해야 하기 때문이다
    말.textContent = 것.said ? `저장했습니다 ✓ ${것.said}` : "저장했습니다 ✓";
  } catch (e) {
    말.textContent = "저장하지 못했습니다: " + (e && e.message ? e.message : e);
  }
}

async function 낱말되돌리기() {
  // 안 묻는다. **되돌릴 수 있으면 묻지 않는 것이 낫다** — 팝업은 흐름을
  // 끊는데, 되돌리기 토스트는 안 끊으면서 잘못돼도 8초 안에 물린다
  const 것 = await api().word_reset(고른낱말) || {};
  $("word-text").value = 것.글 || "";
  낱말그린것 = $("word-text").value;
  $("word-said").textContent = "기본으로 되돌렸습니다.";
  if (것.undo) {
    토스트("기본 낱말로 되돌렸습니다", "되돌리기", 안전하게(async () => {
      const 물린것 = await api().undo_last() || {};
      const 다시 = await api().word_list(고른낱말) || {};
      $("word-text").value = 다시.글 || "";
      낱말그린것 = $("word-text").value;
      $("word-said").textContent = "적어 둔 것을 되살렸습니다.";
      void 물린것;
    }, "되돌리지 못했습니다"));
  }
}

// 점검을 못 재도 설정 화면은 떠야 한다. 여기서 터지면 설정이 통째로 안 열린다
async function 점검그리기() {
  let 것 = null;
  try {
    것 = await api().checkup();
  } catch (e) {
    것 = null;
  }
  if (!것) {
    $("ver-now").textContent = "점검하지 못했습니다";
    return;
  }

  // 「모르면 안 보여 줍니다」 — git 이 없으면 판을 모른다고 한다
  $("ver-now").textContent = 것.version || "어느 판인지 알 수 없습니다";
  $("ver-dot").className = "ready-dot" + (것.version ? " ok" : "");

  // 지난번에 실패했으면 **노랑으로 말해 준다.** `git pull` 은 손댄 파일이
  // 있으면 그냥 실패하는데, 앱은 어차피 다시 켜져서 아무 일도 안 일어난
  // 것처럼 보인다. 판이 그대로인 이유를 모른 채 또 누르게 된다
  const 지난번 = 것.last || {};
  const 실패함 = 지난번.job && 지난번.ok === false;
  $("ver-note").className = "hint" + (실패함 ? " warn-box" : "");
  $("ver-note").textContent = 실패함
    ? `지난번 ${지난번.job === "fix_gpu" ? "그래픽카드 고치기" : "업데이트"}가 `
      + `실패했습니다. 아래 기록을 보세요.`
    : "눌러서 최신으로 받습니다. 앱이 한 번 꺼졌다가 다시 켜집니다.";
  if (실패함) $("ver-dot").className = "ready-dot warn";

  const 일 = {};
  (것.jobs || []).forEach((j) => { 일[j.id] = j; });

  const 업 = 일.update || {};
  $("do-update").disabled = !업.can;
  $("update-why").hidden = !업.why;
  $("update-why").textContent = 업.why || "";

  // 그래픽카드는 재 본 값이 있다. 자리가 모자라면 받아쓰기가 오류도 없이 죽는다
  const g = 것.gpu || {};
  const 됨 = !!g.cublas;
  $("fix-gpu-dot").className = "ready-dot " + (됨 ? "ok" : "bad");
  $("fix-gpu-now").textContent = 됨
    ? "쓸 수 있습니다" + (g.vram_total_gb ? ` · ${g.vram_total_gb}GB` : "")
    : "CUDA 를 못 씁니다. 받아쓰기가 느리거나 죽습니다";

  const 고침 = 일.fix_gpu || {};
  $("do-fix-gpu").disabled = !고침.can;
  $("fix-gpu-why").hidden = !고침.why;
  $("fix-gpu-why").textContent = 고침.why || "";

  // 한 번 눌러 둔 확인을 풀어 준다. 안 풀면 탭을 옮겼다 돌아왔을 때
  // **한 번만 눌러도 앱이 꺼진다**
  되돌리개.forEach((f) => f());
  점검한것 = 것;
}

// 확인을 눌러 둔 단추를 처음 상태로 되돌리는 것들
const 되돌리개 = [];

let 점검한것 = null;

// 한 번 더 눌러야 한다. 앱이 꺼지는 일이라 실수로 눌리면 안 된다.
// 「완전 초기화」와 같은 짜임이다 — 새로 익힐 것이 없다
// 판올림은 **앱 안에서** 하고, git 이 한 말을 눈앞에 띄운다.
//
// 예전에는 배치가 받았다. 앱이 먼저 꺼지니 무엇이 됐는지 볼 데가 없었고,
// 배치가 뜨다 말면 사용자가 보는 것은 「앱만 꺼짐」 이 전부였다. 판이 그대로인
// 까닭은 알 길이 없다 — 실제로 그렇게 됐고, 손으로 배치를 눌러야 받아졌다.
function 판올림단추(단추id) {
  const 단추 = $(단추id);
  if (!단추) return;
  단추.onclick = 안전하게(async () => {
    const 처음말 = 단추.textContent;
    // **원래 꺼져 있었으면 꺼진 채로 되돌린다.** 그냥 켜 버리면, 점검이
    // 「git 으로 받은 폴더가 아니라 못 한다」 고 꺼 둔 단추가 한 번 누른
    // 뒤에 켜진다
    const 원래꺼짐 = !!단추.disabled;
    단추.disabled = true;
    단추.textContent = "받는 중…";
    try {
      const 결과 = await api().update_now();
      // 창구가 빈손으로 와도 그리러 가지 않는다
      if (결과 && (결과.works || 결과.jobs)) 마지막상태 = 결과;
      판올림결과보이기(결과 || {});
    } finally {
      단추.disabled = 원래꺼짐;
      단추.textContent = 처음말;
    }
    다시그리기();
  }, "판올림하지 못했습니다");
}

function 판올림결과보이기(결과) {
  const 칸 = $("update-done");
  if (!칸) return;
  칸.hidden = false;
  칸.className = "notice " + (결과.ok ? "ok" : "bad");
  $("update-done-what").textContent = 결과.message || "판올림하지 못했습니다.";

  // git 이 한 말. **못 받았을 때가 제일 중요하다** — 까닭이 여기에만 있다
  const 속 = $("update-detail");
  속.textContent = 결과.detail || "";
  속.hidden = !(결과.detail || "").trim();

  // 새로 받은 것이 있을 때만 다시 켠다. 이미 최신인데 껐다 켜면 헛수고다
  $("update-restart").hidden = !결과.changed;
}

function 껐다켜기단추(단추id, 무엇, 처음말, 다시말) {
  let 확인 = false;
  const 단추 = $(단추id);
  const 되돌리기 = () => {
    확인 = false;
    단추.textContent = 처음말;
  };
  되돌리개.push(되돌리기);
  단추.onclick = 안전하게(async () => {
    if (!확인) {
      확인 = true;
      단추.textContent = 다시말;
      return;
    }
    const 결과 = await api().restart_with(무엇);
    if (!결과.ok) {
      되돌리기();
      안된까닭(결과, "하지 못했습니다.");
      return;
    }
    // 여기서부터 창이 사라진다. 사라지기 전에 무슨 일인지는 보여 준다
    단추.textContent = "앱을 닫는 중…";
    단추.disabled = true;
  }, "하지 못했습니다");
}

let 고른강도 = "whisper";

// 받아쓰기 강도. 말이 자꾸 빠지면 여기를 올리는 것이 가장 크다
function 강도그리기(지금값) {
  if (지금값) 고른강도 = 지금값;
  const list = $("preset-list");
  list.innerHTML = "";
  (마지막상태.presets || []).forEach((p) => {
    const label = document.createElement("label");
    label.className = "radio" + (p.id === 고른강도 ? " on" : "");

    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "preset";
    radio.checked = p.id === 고른강도;
    radio.value = p.id;
    radio.onchange = () => { 고른강도 = p.id; 강도그리기(); 설정저장(); };

    const col = document.createElement("div");
    col.className = "col";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = p.name;
    const why = document.createElement("span");
    why.className = "why";
    why.textContent = p.note;
    col.append(who, why);

    // 2시간짜리를 기준으로 알려 준다. "분/시간" 은 와닿지 않는다
    const 때 = document.createElement("span");
    때.className = "badge";
    때.textContent = `2시간에 ${Math.round(p.minutes_per_hour * 2)}분`;

    label.append(radio, col, 때);
    list.appendChild(label);
  });
}

// `처음` 이 아니면 저장된 값을 다시 읽지 않는다.
//
// 공급자를 고르면 이 함수가 다시 도는데, 예전에는 그때마다 받아쓰기 쪽도
// 저장된 값으로 되돌렸다. 그래서 **강도를 「극한」으로 바꾼 뒤 공급자를 고르면
// 강도가 슬그머니 옛 값으로 돌아갔다.** 그 상태로 저장하면 바꾼 적이 없는 것이
// 된다. 'CPU 로 처리' 같은 체크상자도 마찬가지였다.
//
// 공급자에 대해서는 이미 이 조심을 하고 있었는데 받아쓰기 쪽만 빠져 있었다.
/** 지금 고른 길. 화면에서 눌러 바꿀 수 있다 */
let 고른길 = "chat";

/** 길 셋을 그린다. 고르면 손잡이 넷이 그 길에 맞게 놓인다 */
function 길그리기() {
  const 칸 = $("route-list");
  if (!칸) return;
  const 길정보 = 마지막상태.route || {};
  칸.innerHTML = "";
  (길정보.routes || []).forEach((r) => {
    const label = document.createElement("label");
    label.className = "radio" + (r.id === 고른길 ? " on" : "");

    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "route";
    radio.checked = r.id === 고른길;
    radio.onchange = async () => {
      고른길 = r.id;
      // **길만 담고 손잡이는 안 담는다.** 안 건드린 손잡이는 새 길을 따라간다
      마지막상태 = await api().save_settings({ translation: { route: 고른길 } });
      설정그리기(true);
      저장했다고알리기();
    };

    const col = document.createElement("div");
    col.className = "col";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = r.name;
    const why = document.createElement("span");
    why.className = "why";
    why.textContent = r.note;
    col.append(who, why);

    label.append(radio, col);
    칸.appendChild(label);
  });
}

/** 손잡이 넷. 길이 정한 값이 들어 있고, 잠긴 것은 못 건드린다 */
function 손잡이그리기() {
  const 지금 = (마지막상태.route || {}).지금 || {};
  const 잠근것 = (마지막상태.route || {}).잠근것 || [];

  const 붙임 = $("knob-brief");
  const 가림 = $("knob-mask");
  const 묶음 = $("knob-batch");
  if (붙임) { 붙임.checked = 지금.지시문 !== false; 붙임.disabled = 잠겼나("지시문"); }
  if (가림) { 가림.checked = 지금.가리기 !== false; 가림.disabled = 잠겼나("가리기"); }
  if (묶음) 묶음.value = 지금.묶음 || 300;

  // **왜 못 건드리는지 적는다.** 회색으로만 만들어 놓으면 고장인 줄 안다
  const 말 = $("knob-locked");
  if (말) {
    말.hidden = 잠근것.length === 0;
    말.textContent = 잠근것.length
      ? "번역기는 지시문까지 번역해서 돌려주고, 가림표(KW01)는 뭉개져 "
        + "되돌릴 수 없습니다. 그래서 이 둘은 번역기 길에서 끕니다."
      : "";
  }

  // 「자동」 길에서만 어디로 보낼지 고른다
  const 어디 = $("send-where");
  if (어디) 어디.hidden = 고른길 !== "endpoint";
}

function 설정그리기(처음) {
  const s = 마지막상태.settings || {};

  길그리기();
  손잡이그리기();

  const list = $("provider-list");
  list.innerHTML = "";
  // **「자동」 길에서 「직접 복붙」 은 보낼 곳이 아니다.** 고르면 자동 번역이
  // 조용히 아무것도 안 한다 — 왜 안 도는지 알아낼 길이 없다
  const 보낼곳 = (마지막상태.providers || []).filter((p) => p.id !== "manual");
  보낼곳.forEach((p) => {
    const label = document.createElement("label");
    label.className = "radio" + (p.id === 고른공급자 ? " on" : "");

    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "provider";
    radio.checked = p.id === 고른공급자;
    radio.onchange = () => { 고른공급자 = p.id; 설정그리기(); 설정저장(); };

    const col = document.createElement("div");
    col.className = "col";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = p.name;
    const why = document.createElement("span");
    why.className = "why";
    why.textContent = p.note;
    col.append(who, why);

    label.append(radio, col);
    if (!p.needs_key || p.has_key) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = p.needs_key ? "키 있음" : "바로 됨";
      label.appendChild(badge);
    }
    list.appendChild(label);
  });

  const 고른것 = (마지막상태.providers || []).find((p) => p.id === 고른공급자) || {};
  const 로컬 = !!고른것.local;

  // 내 컴퓨터에서 도는 모델은 키가 없다. 주소와 모델 이름을 받는다
  $("key-field").hidden = !고른것.needs_key || 로컬;
  // Ollama 는 우리가 문맥 창을 넓혀 준다. LM Studio 는 그쪽 앱에서 정해야 한다
  $("lmstudio-note").hidden = 고른공급자 !== "lmstudio";

  $("api-key").value = 고른것.has_key ? "●●●●●●●●●●●●" : "";
  $("key-link").href = 고른것.key_url || "#";
  $("key-link").textContent = `${고른것.name || ""} 키 받으러 가기 →`;

  // **내 컴퓨터 AI 칸은 늘 그린다.** 위에서 「직접 복붙」을 골라도 번역
  // 화면에는 「내 컴퓨터 AI로 번역」 단추가 있다. 그런데 어느 모델을 쓸지
  // 고르는 칸이 숨어 있어서, 눌러 놓고 무엇이 돌아가는지 알 수가 없었다
  if (처음) {
    const t = s.translation || {};
    $("local-url").value = t.url || "";
    $("local-model").value = t.model || "";
    $("local-hint").textContent =
      "비워 두면 받아 둔 것 중에서 알아서 고릅니다";
    $("test-result").textContent = "";
    로컬상태보기();
  }

  if (!처음) {
    강도그리기();          // 고른 것을 그대로 두고 다시 그리기만 한다
    return;
  }

  const asr = s.asr || {};
  강도그리기(asr.preset);
  $("asr-device").checked = asr.device === "cpu";
  $("asr-nonverbal").checked = asr.keep_nonverbal !== false;

  const out = s.output || {};
  $("out-gap").value = out.gap_clear_sec != null ? out.gap_clear_sec : 1.5;
  if ($("lookup-online")) $("lookup-online").checked = (s.works || {}).lookup_online !== false;
}

// ---- Ollama 딸깍 ----
//
// "명령 프롬프트를 열고 ollama pull ... 을 치세요" 라고 말하는 순간 끝이다.
// 프로그램이 대신 켜고 대신 받는다.

// 지금 어디까지 됐는지 **한 줄로** 말한다.
//
// 예전에는 「1. Ollama 받기 / 2. 켜기 / 3. 번역 모델 받기」 세 줄을 늘어놓고
// 각각 끝났는지 표시했다. 세 줄을 다 읽어야 지금 무엇을 눌러야 하는지 알 수
// 있었고, 눌러도 무엇이 되고 있는지 안 보였다.
//
// 알아야 하는 것은 딱 둘이다 — **지금 되나?** 와 **뭘 누르면 되나?**
const 준비말 = {
  install: ["Ollama 가 없습니다", "받으러 가기", "bad"],
  start:   ["깔려 있는데 안 켜져 있습니다", "켜기", "warn"],
  pull:    ["번역할 모델이 없습니다", "모델 받기", "warn"],
  ready:   ["쓸 수 있습니다", "다시 확인", "ok"],
};

async function 로컬상태보기() {
  const 것 = await api().local_status($("local-url").value.trim(), $("local-model").value.trim());
  const [말, 단추말, 빛깔] = 준비말[것.next] || 준비말.install;

  $("local-ready-text").textContent = 말;
  $("local-dot").className = "ready-dot " + 빛깔;
  $("local-do").textContent = 단추말;
  $("local-do").className = 것.next === "ready" ? "ghost small" : "primary small";
  $("local-msg").textContent = 것.message || "";

  가진모델그리기(것);
  더받을것그리기(것);
  들어가는지그리기(것);
  return 것;
}

// 고른 모델이 그래픽카드에 들어가는지.
//
// 안 들어가면 Ollama 는 **오류를 내지 않는다.** 일부 층을 조용히 CPU 로 내리고
// 몇 배 느려질 뿐이라, 사용자는 원래 이런 건 줄 안다. 그것을 말해 준다.
function 들어가는지그리기(것) {
  const 칸 = $("fit-note");
  if (!칸) return;
  // 창구가 VRAM 이나 모델 크기를 못 구했으면 아무 말도 하지 않는다.
  // 모르면서 겁주는 것이 모르는 채로 두는 것보다 나쁘다
  if (!것 || 것.fit === null || 것.fit === undefined) {
    칸.hidden = true;
    return;
  }
  칸.hidden = false;
  칸.textContent = 것.fit_note || "";
  if (!것.fit && (것.better || []).length) {
    칸.textContent += ` 이것들은 들어갑니다: ${것.better.join(", ")}`;
  }
}

// 모델 하나를 그린다. **성격이 제일 크게, 이름은 아래 작은 글씨로.**
//
// 예전에는 이름만 늘어놓았다. `qwen2.5:14b` 와
// `huihui_ai/qwen2.5-vl-abliterated:7b` 를 보고 무엇을 골라야 하는지 알 수가
// 없다. 알고 싶은 것은 「한국어가 자연스러운 것」이지 이름이 아니다.
// 실제로 이름만 보고 그림 보는 판을 골랐다.
function 모델칸(카드, 누를때, 단추말) {
  const 칸 = document.createElement("button");
  칸.type = "button";
  칸.className = "pick"
    + (카드.chosen ? " on" : "")
    + (카드.using && !카드.chosen ? " now" : "")
    + (카드.usable === false ? " off" : "");

  const 위 = document.createElement("span");
  위.className = "pick-title";
  위.textContent = 카드.usable === false ? "번역에는 못 씁니다" : 카드.title;

  const 아래 = document.createElement("span");
  아래.className = "pick-note";
  아래.textContent = 카드.usable === false
    ? "그림을 보는 판이거나 글을 못 만드는 것입니다"
    : 카드.note;

  const 꼬리 = document.createElement("span");
  꼬리.className = "pick-name";
  const 크기 = 카드.gb ? ` · ${카드.gb}GB` : "";
  const 자리 = 카드.fits === false ? " · 그래픽카드에 안 들어감" : "";
  꼬리.textContent = 카드.id + 크기 + 자리;

  칸.append(위, 아래, 꼬리);

  // **눌러 둔 것과 정말 도는 것이 다를 수 있다.**
  // 고른 것이 그래픽카드에 안 들어가면 창구가 다른 것으로 바꿔서 돌린다.
  // 그 판단은 옳지만, 말을 안 해 주면 설정에는 `qwen2.5:14b`, 번역 화면에는
  // `exaone3.5:7.8b` 이 떠서 어느 쪽도 못 믿게 된다
  if (카드.instead) {
    const 대신 = document.createElement("span");
    대신.className = "pick-instead";
    대신.textContent = `안 들어가서 실제로는 ${카드.instead} 로 돕니다`;
    칸.appendChild(대신);
  } else if (카드.using && !카드.chosen) {
    const 지금 = document.createElement("span");
    지금.className = "pick-using";
    지금.textContent = "지금 이것으로 돌고 있습니다";
    칸.appendChild(지금);
  }
  if (단추말) {
    const 딱지 = document.createElement("span");
    딱지.className = "pick-do";
    딱지.textContent = 단추말;
    칸.appendChild(딱지);
  }
  if (카드.usable === false) 칸.disabled = true;
  else 칸.onclick = 안전하게(() => 누를때(카드));
  return 칸;
}

// 이미 받아 둔 모델.
//
// 창구는 목록을 돌려주고 있었는데 화면이 그것을 **버리고 있었다.** 그래서
// 쓸 만한 모델이 여러 개 깔려 있어도 "아직 안 받았습니다" 라며 9GB 를 또
// 받으라고 했다.
function 가진모델그리기(것) {
  const 칸 = $("local-have");
  const 목록 = $("local-have-list");
  if (!칸 || !목록) return;
  const 카드들 = (것 && 것.cards) || [];
  목록.innerHTML = "";
  칸.hidden = !카드들.length;
  if (!카드들.length) return;

  카드들.forEach((카드) => {
    목록.appendChild(모델칸(카드, async (고른것) => {
      $("local-model").value = 고른것.id;
      await 설정저장();          // 고른 것을 실제로 쓰게 남긴다
      await 로컬상태보기();
    }));
  });
}

// 아직 안 받은 것. 「이런 것도 있습니다」
//
// 무엇을 받아야 하는지 모르는 것이 제일 큰 벽이다. `ollama pull` 을 치라고
// 하는 순간 끝난다 — 눌러서 받게 한다.
function 더받을것그리기(것) {
  const 칸 = $("local-more");
  const 목록 = $("local-more-list");
  if (!칸 || !목록) return;
  const 없는것 = (것 && 것.missing) || [];
  목록.innerHTML = "";
  칸.hidden = !없는것.length;
  if (!없는것.length) return;

  없는것.forEach((카드) => {
    목록.appendChild(모델칸(카드, async (고를것) => {
      $("local-model").value = 고를것.id;
      await 설정저장();
      await api().pull_model("", 고를것.id);
      받는거보기();
    }, "받기"));
  });
}

// `qwen2.5` 와 `qwen2.5:latest` 는 같은 것이다. 창구도 같은 규칙으로 견준다
function 같은모델(a, b) {
  const 풀기 = (x) => (String(x || "").includes(":") ? String(x) : `${x}:latest`);
  return 풀기(a) === 풀기(b);
}

async function 로컬딸깍() {
  const 단추 = $("local-do");
  단추.disabled = true;
  const url = $("local-url").value.trim();
  const model = $("local-model").value.trim();
  try {
    const 것 = await api().local_status(url, model);
    if (것.next === "install") {
      window.open("https://ollama.com/download", "_blank");
      $("local-msg").textContent = "설치가 끝나면 다시 눌러 주세요.";
    } else if (것.next === "start") {
      $("local-msg").textContent = "켜는 중…";
      const 결과 = await api().start_local(url);
      $("local-msg").textContent = 결과.message;
    } else if (것.next === "pull") {
      await api().pull_model(url, model);
      받는거보기();
      return;   // 다 받을 때까지 단추를 잠가 둔다
    } else {
      $("local-msg").textContent = "바로 쓸 수 있습니다.";
    }
    await 로컬상태보기();
  } finally {
    // 받는 중이 아니면 단추를 풀어 준다. 받는 중이면 다 받고 나서 푼다
    if ($("pull-track").hidden) 단추.disabled = false;
  }
}

// 위의 `견주는거보기` 와 같은 까닭으로 감싼다. 여기는 더 나쁘다 —
// 터지면 「받기」 단추가 꺼진 채로 남아서 다시 누를 수도 없다.
function 받는거보기() {
  const 끝내기 = (말) => {
    $("pull-track").hidden = true;
    const 단추 = $("local-do");
    if (단추) 단추.disabled = false;
    if (말) $("local-msg").textContent = 말;
  };
  $("pull-track").hidden = false;
  const 보기 = setInterval(async () => {
    let 것;
    try {
      것 = await api().pull_progress();
    } catch (e) {
      clearInterval(보기);
      끝내기("받지 못했습니다: " + (e && e.message ? e.message : e));
      return;
    }
    것 = 것 || {};
    $("pull-fill").style.width = Math.round((것.ratio || 0) * 100) + "%";
    $("local-msg").textContent = 것.message || "받는 중…";
    if (!것.done) return;
    clearInterval(보기);
    끝내기("");
    try {
      await 로컬상태보기();
    } catch (e) {
      $("local-msg").textContent = "받은 뒤 상태를 못 읽었습니다: " + (e && e.message ? e.message : e);
    }
  }, 700);
}

// 바꾸는 즉시 저장한다. 「저장」을 누르게 하지 않는다.
//
// 강도를 「보통」으로 바꾸고 그냥 닫으면 바뀐 적이 없는 것이 됐다. 다시 돌려
// 보고 "왜 안 바뀌지" 하다가, 저장을 안 눌렀다는 것을 나중에야 안다.
// **누를 것을 하나 더 만들지 말고 그냥 저장한다.**
//
// 글자 칸은 한 글자마다 저장하면 창구를 너무 자주 두드린다. 손을 멈추고
// 잠깐 지나면 저장한다.
const 자동저장_기다림 = 500;
let 자동저장_시계 = null;

function 곧저장() {
  clearTimeout(자동저장_시계);
  자동저장_시계 = setTimeout(() => { 설정저장(); }, 자동저장_기다림);
}

// 설정 화면에 있는 칸 전부. 여기 빠진 칸은 바꿔도 저장되지 않는다.
// `test_설정_칸이_모두_자동저장에_걸려_있다` 가 빠진 것을 잡는다
const 자동저장칸 = [
  "api-key", "local-url", "local-model",
  "asr-device", "asr-nonverbal", "out-gap", "lookup-online",
  "knob-brief", "knob-mask", "knob-batch",
];

function 자동저장걸기() {
  자동저장칸.forEach((이름) => {
    const 칸 = $(이름);
    if (!칸) return;
    if (칸.type === "checkbox") {
      칸.onchange = () => 설정저장();     // 체크는 곧바로. 기다릴 이유가 없다
    } else {
      칸.oninput = 곧저장;
      칸.onchange = 곧저장;               // 붙여넣기·화살표로 바뀌는 것까지
    }
  });
}

function 저장했다고알리기() {
  const 칸 = $("saved-note");
  if (!칸) return;
  칸.textContent = "저장했습니다";
  칸.classList.add("on");
  clearTimeout(저장했다고알리기._시계);
  저장했다고알리기._시계 = setTimeout(() => {
    칸.textContent = "바꾸면 바로 저장됩니다";
    칸.classList.remove("on");
  }, 1500);
}

async function 설정저장() {
  const 키 = $("api-key").value;
  const 고른것 = (마지막상태.providers || []).find((p) => p.id === 고른공급자) || {};
  // **손잡이는 「건드린 것」에만 담는다.** 길이 정한 값과 같으면 안 담아야
  // 길을 바꿨을 때 따라온다. 여기서는 화면에 뜬 값을 그대로 담되, 잠긴
  // 것은 빼고 담는다 — 잠긴 것을 담으면 옛 설정에 값이 남는다
  const 고친것 = {};
  if (!잠겼나("지시문")) 고친것.지시문 = $("knob-brief").checked;
  if (!잠겼나("가리기")) 고친것.가리기 = $("knob-mask").checked;
  const 준줄 = parseInt($("knob-batch").value, 10);
  if (준줄) 고친것.묶음 = 준줄;
  if (고른길 === "endpoint") 고친것.보내는길 = 고른공급자;

  const patch = {
    translation: 고른것.local
      ? {
          route: 고른길,
          고친것,
          url: $("local-url").value.trim(),
          model: $("local-model").value.trim(),
        }
      : { route: 고른길, 고친것 },
    asr: {
      preset: 고른강도,
      device: $("asr-device").checked ? "cpu" : "cuda",
      keep_nonverbal: $("asr-nonverbal").checked,
    },
    output: {
      gap_clear_sec: parseFloat($("out-gap").value) || 1.5,
    },
    works: { lookup_online: !$("lookup-online") || $("lookup-online").checked },
    keys: {},
  };
  if (키 && !키.includes("●")) patch.keys[고른공급자] = 키;

  마지막상태 = await api().save_settings(patch);
  // **다시 그리지 않는다.** 글자를 치는 중에 다시 그리면 방금 친 것이
  // 저장된 값으로 덮어써져서 손이 튄다. API 키 칸은 ●●●● 로 돌아가 버린다
  저장했다고알리기();
}

// ---- 파일 넣기 ----

async function 파일고르기() {
  const 창구 = api();
  if (!창구) return;

  try {
    const 고름 = await 창구.pick_files();
    if (고름 && 고름.length) {
      마지막상태 = await 창구.add_files(고름);
      작업그리기();
      return;
    }
  } catch (e) {
    // 창구가 터져도 화면은 살아 있어야 한다. 왜 안 됐는지는 보여 준다
    $("notice").hidden = false;
    $("notice").className = "notice bad";
    $("notice").textContent = "파일 고르기가 실패했습니다: " + e;
    return;
  }
  새로고침();
}

function 끌어다놓기() {
  // **화면 아무 데나 놓아도 받는다.** 파일이 들어오면 큰 드롭존이 사라지므로,
  // 과녁이 드롭존 하나뿐이면 두 번째 파일부터 놓을 자리가 없다
  const 켜기 = () => {
    const d = $("drop");
    const p = $("pick");
    if (d && !d.hidden) d.classList.add("over");
    if (p) p.classList.add("over");
  };
  const 끄기 = () => {
    const d = $("drop");
    const p = $("pick");
    if (d) d.classList.remove("over");
    if (p) p.classList.remove("over");
  };
  ["dragenter", "dragover"].forEach((e) =>
    document.addEventListener(e, (ev) => { ev.preventDefault(); 켜기(); })
  );
  document.addEventListener("dragleave", (ev) => {
    // 자식으로 들어가도 leave 가 온다. 창 밖으로 나갔을 때만 끈다
    if (!ev.relatedTarget) 끄기();
  });

  document.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    끄기();
    // pywebview 는 놓은 파일의 실제 경로를 여기에 담아 준다.
    // 못 받으면 고르기 창으로 돌린다 — 브라우저는 경로를 알려주지 않는다.
    const paths = [...(ev.dataTransfer.files || [])]
      .map((f) => f.pywebviewFullPath || f.path)
      .filter(Boolean);

    if (!paths.length) { await 파일고르기(); return; }
    마지막상태 = await api().add_files(paths);
    작업그리기();
  });
}

// ---- 시작 ----

function 묶기() {
  $("back").onclick = 안전하게(async () => { 제목보는작품 = null; 보이기("work"); }, "돌아가지 못했습니다");
  $("to-settings").onclick = () => 보이기("settings");
  $("pick").onclick = (ev) => { ev.stopPropagation(); 파일고르기(); };
  $("drop").onclick = 파일고르기;
  $("start").onclick = 안전하게(async () => { 마지막상태 = await api().start(); 작업그리기(); }, "시작하지 못했습니다");
  $("stop").onclick = 안전하게(
    () => 되돌릴수있게(() => api().stop(), "멈추는 중입니다"), "멈추지 못했습니다");
  // 되돌릴 수 없는 단추라 한 번 더 묻는다. 멈추기 바로 밑이라 오눌리기 쉽다
  let 비우기확인 = false;
  $("clear").onclick = 안전하게(async () => {
    const 답 = await api().clear(비우기확인);
    // 창구가 빈손으로 오면 그대로 그리러 가지 않는다. 예전에는 여기서
    // 통째로 터져서 단추가 죽은 것처럼 보였다
    if (답) 마지막상태 = 답;
    비우기확인 = !!(답 && 답.confirm);
    $("clear").textContent = 비우기확인 ? "정말 비웁니다" : "목록 비우기";
    작업그리기();
  }, "목록을 비우지 못했습니다");
  $("copy").onclick = 복사하기;
  if ($("mask-style")) $("mask-style").onclick = 안전하게(가리기바꾸기, "바꾸지 못했습니다");
  if ($("local-cancel")) {
    $("local-cancel").onclick = 안전하게(async () => {
      await api().translate_locally_stop();
    }, "그만두지 못했습니다");
  }
  if ($("watch-undo")) $("watch-undo").onclick = 안전하게(감시되돌리기, "되돌리지 못했습니다");
  if ($("watch-off")) $("watch-off").onclick = 안전하게(감시끄기, "끄지 못했습니다");
  $("copy-style").onclick = 안전하게(복사방식바꾸기, "복사 방식을 바꾸지 못했습니다");
  // 가끔 쓰는 단추들은 ⋯ 뒤에 있다. 밖을 누르면 닫힌다
  $("tr-more").onclick = (ev) => {
    ev.stopPropagation();
    $("tr-more-menu").hidden = !$("tr-more-menu").hidden;
  };
  document.addEventListener("click", (ev) => {
    const 메뉴 = $("tr-more-menu");
    if (메뉴 && !메뉴.hidden && !메뉴.contains(ev.target)) 메뉴.hidden = true;
  });
  // 나무의 「제목 번역」 입구는 없앴다 — 제목은 작품에 딸린 것이라 작품
  // 머리에서 들어간다 (규칙 6)
  // 제목 화면의 「← 돌아가기」 는 없앴다(머리띠 ← 와 겹침). 그 단추가 하던 「고른 작품
  // 풀기」 는 머리띠 ← 가 맡는다
  if ($("titles-all")) {
    $("titles-all").onclick = 안전하게(async () => {
      제목보는작품 = null;
      await 제목칸그리기();
    }, "열지 못했습니다");
  }
  $("titles-copy").onclick = 안전하게(제목복사, "복사하지 못했습니다");
  $("titles-submit").onclick = 안전하게(제목넣기, "제목을 넣지 못했습니다");
  $("viewer-tab-one").onclick = () => 보기탭("one");
  $("viewer-tab-all").onclick = 안전하게(() => 보기탭("all"), "통째로 열지 못했습니다");
  $("viewer-all-copy").onclick = 안전하게(통째로복사, "복사하지 못했습니다");
  $("viewer-all-submit").onclick = 안전하게(통째로넣기, "넣지 못했습니다");
  $("viewer-all-reset").onclick = 통째로되돌리기;
  // **담기 단추가 없다.** 치는 대로 담긴다 (화면 규칙: 바꾸면 바로 저장)
  if ($("names-text")) {
    $("names-text").oninput = 호칭곧담기;
    $("names-text").onblur = 안전하게(호칭담기, "호칭을 담지 못했습니다");
  }
  $("names-text").onkeydown = (e) => { if (e.key === "Enter") 호칭담기(); };
  $("review-copy").onclick = 안전하게(검수복사, "검수 프롬프트를 만들지 못했습니다");
  $("review-local").onclick = 안전하게(내컴퓨터로검수, "검수하지 못했습니다");
  $("submit-review").onclick = 안전하게(검수넣기, "검수 답을 넣지 못했습니다");
  $("local-now").onclick = 안전하게(() => 내컴퓨터로번역(false), "내 컴퓨터 AI로 번역하지 못했습니다");
  $("save-file").onclick = 파일로저장;
  $("copy-all").onclick = 안전하게(전부파일로, "묶음을 내보내지 못했습니다");
  $("submit").onclick = 안전하게(넣기, "넣지 못했습니다");
  $("skip").onclick = 안전하게(async () => { await api().skip(); await 새로고침(); 번역칸그리기(); }, "건너뛰지 못했습니다");
  $("giveup").onclick = 안전하게(async () => {
    await 되돌릴수있게(() => api().give_up());
    await 새로고침();
    번역칸그리기();
  }, "포기하지 못했습니다");
  // 붙여넣고 바로 넣을 수 있게. 손이 마우스로 안 가도 된다
  for (const [, , 칸id] of 주단추짝) {
    const 칸 = $(칸id);
    if (칸) 칸.addEventListener("input", () => 주단추맞추기(false));
  }
  $("paste").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) 넣기();
  });

  $("save").onclick = 안전하게(async () => { await 설정저장(); 보이기("work"); });
  자동저장걸기();
  $("local-do").onclick = 로컬딸깍;
  $("test-provider").onclick = 안전하게(async () => {
    const 칸 = $("test-result");
    칸.textContent = "확인하는 중…";
    const 결과 = await api().test_provider(
      고른공급자, $("local-url").value.trim(), $("local-model").value.trim()
    ) || {};
    // 「확인하는 중…」 에서 굳지 않게 한다. 못 물어본 것도 답이다
    칸.textContent = 결과.message || "확인하지 못했습니다.";
    칸.className = "hint " + (결과.ok ? "ok-text" : "bad-text");
  }, "확인하지 못했습니다");
  // 되돌릴 수 없으니 한 번 더 묻는다
  let 초기화확인 = false;
  $("reset").onclick = 안전하게(async () => {
    const 답 = await api().reset_all(초기화확인, !$("reset-keys").checked);
    if (답) 마지막상태 = 답;
    초기화확인 = !!(답 && 답.confirm);
    $("reset").textContent = 초기화확인 ? "정말 지웁니다" : "완전 초기화";
    if (!초기화확인) 설정열기();
    다시그리기();
  }, "초기화하지 못했습니다");
  판올림단추("new-ver-go");
  껐다켜기단추("gpu-fix-go", "fix_gpu",
              "고치고 다시 켜기", "누르면 앱이 꺼집니다");
  // **한 번 눌러 복사하고 그대로 붙여넣는다.** 예전에는 `진단.txt` 를
  // 만들어 놓고 폴더를 열어 줬다 — 사용자가 그 파일을 찾아 첨부해야 했고,
  // 「이상하다」 고 말하려던 사람이 거기서 그만둔다
  $("report").onclick = 안전하게(async (ev) => {
    const 단추 = ev && ev.target ? ev.target : $("report");
    const 결과 = await api().problem_report();
    if (!결과 || !결과.ok) {
      안된까닭(결과, "모으지 못했습니다.");
      return;
    }
    await 글복사(결과.text || "");
    const 원래 = 단추.textContent;
    단추.textContent = 결과.clipped
      ? "복사했습니다 ✓ (뒤는 잘랐습니다)" : "복사했습니다 ✓";
    setTimeout(() => { 단추.textContent = 원래; }, 1800);
  }, "모으지 못했습니다");
  $("tab-btn-set").onclick = () => 탭보이기("set");
  $("tab-btn-word").onclick = () => 탭보이기("word");
  $("word-kind-mine").onclick = () => 낱말고르기("내낱말");
  $("word-kind-risk").onclick = () => 낱말고르기("위험낱말");
  // **저장 단추가 없다.** 치는 대로 담는다 (화면 규칙: 바꾸면 바로 저장)
  if ($("word-text")) {
    $("word-text").oninput = 낱말곧저장;
    // 칸을 떠나면 기다리지 않고 곧바로 담는다
    $("word-text").onblur = 안전하게(낱말바로저장, "담지 못했습니다");
  }
  $("word-reset").onclick = 안전하게(낱말되돌리기, "되돌리지 못했습니다");
  판올림단추("do-update");
  껐다켜기단추("update-restart", "update", "다시 켜기", "누르면 앱이 꺼집니다");
  if ($("update-done-close")) {
    $("update-done-close").onclick = 안전하게(
      () => { $("update-done").hidden = true; }, "닫지 못했습니다");
  }
  껐다켜기단추("do-fix-gpu", "fix_gpu",
              "고치고 다시 켜기", "누르면 앱이 꺼집니다");
  $("viewer-close").onclick = 보기닫기;
  $("wave-canvas").onclick = 파형눌림;
  $("viewer-prev").onclick = 안전하게(() => 옆트랙보기(-1), "앞 트랙을 열지 못했습니다");
  $("viewer-next").onclick = 안전하게(() => 옆트랙보기(1), "다음 트랙을 열지 못했습니다");
  $("play-stop").onclick = 소리멈춤;
  // **「고친 것 저장」 단추가 없다.** 치는 대로 담긴다 (화면 규칙).
  // 창을 닫거나 옆 트랙으로 넘어가기 전에 하던 것을 마저 담는다
  $("viewer-save").onclick = 안전하게(async () => {
    // 파일로 갖고 싶은 사람도 있다. 다만 기본은 창 안에서 보는 것이다
    const 결과 = await api().save_japanese(보는칸) || {};
    if (결과.ok) api().open_folder(결과.path);
    // 읽기 전용 폴더나 디스크가 꽉 찬 경우다. 창 안에서 바로 알려 준다
    else $("viewer-msg").textContent = 결과.message || "저장하지 못했습니다.";
  }, "저장하지 못했습니다");
  // 탐색기의 몸에 밴 키 그대로. 칸에 글을 치는 중에는 끼어들지 않는다
  document.addEventListener("keydown", (ev) => {
    const 치는중 = document.activeElement
      && ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
    if (ev.key === "Escape") {
      메뉴닫기();
      if (치는중) return;
      if (선택트랙.size) { 선택트랙.clear(); 다시그리기(); return; }
      if (보는칸 >= 0) { 보기닫기(); return; }
      return;
    }
    if (치는중) return;
    if (ev.key === "Delete" && 선택트랙.size) {
      빼기실행(new Set(선택트랙));
      return;
    }
    if (ev.key === "Enter" && 선택트랙.size === 1 && 오른쪽모드 === "list" && 보는칸 < 0) {
      const 하나 = 표에보인것.find((x) => 선택트랙.has(x.job.index));
      if (하나) 트랙열기(하나.job);
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === "a" || ev.key === "A")
        && 지금 === "work" && 오른쪽모드 === "list" && 보는칸 < 0) {
      ev.preventDefault();
      선택트랙 = new Set(표에보인것.map((x) => x.job.index));
      다시그리기();
    }
  });
  document.addEventListener("click", 메뉴닫기);
  끌어다놓기();
}

// 감싸지 않은 자리가 남아 있어도 조용히 죽지 않게 하는 그물망
window.addEventListener("unhandledrejection", (ev) => {
  const 까닭 = ev && ev.reason;
  안된까닭({ message: "문제가 생겼습니다: " + (까닭 && 까닭.message ? 까닭.message : 까닭) });
});

window.addEventListener("pywebviewready", () => {
  묶기();
  // 켜면 바로 일감 앞이다. 홈 화면이 없어졌으므로 여기서 한 번 세워 준다 —
  // 안 하면 `#screen-work` 가 hidden 인 채로 빈 창만 뜬다
  보이기("work");
  새로고침();
  // **번역 칸이 떠 있어도 새로 그린다.**
  //
  // 여태는 그 동안 통째로 건너뛰었다. 붙여넣던 글이 날아가지 않게 하려던
  // 것인데, **답을 복사·붙여넣는 자리가 바로 그 번역 칸**이다. 그래서
  // 클립보드 감시가 답을 넣어도 화면은 몰랐다. 다른 탭을 눌렀다 와야
  // 그제서야 들어간 것이 보였고, 그 사이에 손으로 또 넣으면 「이미 들어
  // 있습니다」 경고가 떴다 — 그 경고가 유일한 단서였다.
  //
  // 치던 글이 날아가는 것은 `작업그리기` 가 이미 막는다. 글상자에 손이
  // 가 있으면 그리지 않는다. 여기서 또 막을 까닭이 없었다
  setInterval(새로고침, 600);
});
