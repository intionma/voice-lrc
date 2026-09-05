# 바탕화면과 시작 메뉴에 바로 가기를 만들고, 개발용 파일을 숨긴다.
#
# 첫 실행 때 `START.bat` 이 한 번 부른다. 나중에 바로 가기를 잃어버렸으면
# 이 파일을 직접 눌러 다시 만들면 된다.
#
# **exe 로 굽지 않는 까닭.** 이 앱의 판올림은 `git pull` 이다. exe 로 싸면
# 그 통로가 사라진다. 서명 없는 PyInstaller exe 는 백신이 자주 오해하기도
# 한다(`build.spec` 에 이미 그 흔적이 있다). 바로 가기는 「이 파일을 이렇게
# 열어라」 라고 적힌 쪽지일 뿐이라, 잘못돼도 지우면 그만이다.
#
# **START.bat 을 가리킨다.** pythonw.exe 를 바로 가리키면 더 빠르지만,
# 그러면 첫 실행 설치와 「고장 난 .py 인지 먼저 본다」 를 건너뛴다. 판올림이
# 망가진 파일을 남겼을 때 아무 말 없이 안 켜지는 것이 제일 나쁘다.
# 대신 창을 최소화(WindowStyle 7)로 띄워서 검은 창이 앞에 안 나선다.
#
# **여기서 나는 오류로 앱이 안 켜지면 안 된다.** 이것은 곁들이지 조건이
# 아니다. `START.bat` 이 실패해도 그냥 넘어간다.

$ErrorActionPreference = "Continue"

$뿌리 = Split-Path -Parent $PSScriptRoot
$켜개 = Join-Path $뿌리 "START.bat"
$아이콘 = Join-Path $뿌리 "app\ui\web\icon.ico"
$이름 = "trans-text"

if (-not (Test-Path $켜개)) {
    Write-Host "[오류] START.bat 이 없습니다: $켜개"
    Write-Host "이 스크립트를 앱 폴더 안의 scripts\ 에 둔 채로 돌려 주세요."
    exit 1
}

# 아이콘이 없어도 바로 가기는 만든다. 그림 하나 때문에 못 만들면 안 된다
$아이콘있나 = Test-Path $아이콘

function 바로가기만들기($놓을곳, $어디라고부를까) {
    if (-not (Test-Path $놓을곳)) {
        Write-Host "  건너뜀 ($어디라고부를까 폴더가 없습니다)"
        return
    }
    $길 = Join-Path $놓을곳 "$이름.lnk"
    $쉘 = New-Object -ComObject WScript.Shell
    $바 = $쉘.CreateShortcut($길)
    $바.TargetPath = $켜개
    # **일하는 폴더를 앱 폴더로.** 안 잡아 주면 바탕화면에서 켰을 때
    # `cd /d "%~dp0"` 전까지 엉뚱한 데를 보고, 상대 경로가 다 어긋난다
    $바.WorkingDirectory = $뿌리
    $바.WindowStyle = 7          # 최소화 — 검은 창이 앞에 안 나선다
    $바.Description = "일본어 음성을 한국어 자막으로"
    if ($아이콘있나) { $바.IconLocation = "$아이콘,0" }
    $바.Save()
    Write-Host "  만들었습니다: $길"
}

Write-Host ""
Write-Host "바로 가기를 만듭니다."
Write-Host ""

바로가기만들기 ([Environment]::GetFolderPath("Desktop")) "바탕화면"
바로가기만들기 (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs") "시작 메뉴"

if (-not $아이콘있나) {
    Write-Host ""
    Write-Host "  (아이콘 파일이 없어서 기본 아이콘으로 만들었습니다."
    Write-Host "   python scripts\make_icon.py 를 돌리면 생깁니다)"
}

Write-Host ""
Write-Host "끝났습니다. 바탕화면의 '$이름' 을 누르면 앱이 켜집니다."
Write-Host "지우고 싶으면 바로 가기를 그냥 삭제하세요. 앱은 그대로입니다."

# ── 개발용 파일 숨기기 ──────────────────────────────────────────
#
# 폴더를 열면 열두 개가 보이는데 그중 사용자가 쓸 것은 `START.bat` 하나다.
# 나머지는 개발용이다.
#
# **지우거나 옮기지 않는다.** 윈도우의 「숨김」 표시만 켠다 — git 은 이 표시를
# 안 보므로 `git pull` 도 그대로 되고, 탐색기에서 「숨긴 항목」 을 켜면 다시
# 보인다. 파일은 그 자리에 그대로 있다.

$숨길것 = @(
    "AGENTS.md", "CLAUDE.md", "LICENSE", "README.md",
    ".gitattributes", ".gitignore", ".pytest_cache",
    "_relaunch.bat", "android", "docs", "scripts", "app",
    # 켤 때·터질 때 적는 기록. 폴더에 두 개가 늘 보였다. 필요할 때는 「문제 알리기」 가 실어 낸다
    "_start.log", "_crash.log",
    # 파이썬을 깔아 둔 곳. 사용자가 열 일이 없다
    ".venv"
)

$숨긴수 = 0
# **반복 변수를 `$이름` 으로 쓰면 안 된다.** 위의 앱 이름을 덮어쓴다 —
# 지금은 뒤에서 안 써서 티가 안 나지만, 나중에 한 줄 붙이면 그때 터진다
foreach ($숨길이름 in $숨길것) {
    $길 = Join-Path $뿌리 $숨길이름
    if (-not (Test-Path $길)) { continue }
    try {
        $것 = Get-Item $길 -Force
        $것.Attributes = $것.Attributes -bor [IO.FileAttributes]::Hidden
        $숨긴수++
    } catch {
        # 하나 못 숨겨도 넘어간다. 이것 때문에 앱이 안 켜지면 안 된다
    }
}

if ($숨긴수 -gt 0) {
    Write-Host ""
    Write-Host "개발용 파일 $숨긴수 개를 숨겼습니다 (지운 것이 아닙니다)."
    Write-Host "다시 보려면 탐색기에서 [보기] > [숨긴 항목] 을 켜세요."
}

Write-Host ""
Write-Host "이제 이 폴더를 열 일이 없습니다. 바탕화면 아이콘만 쓰시면 됩니다."
Write-Host ""
Write-Host "바로 가기를 지웠다가 다시 만들고 싶으면"
Write-Host "  .venv\.desktop_done  파일을 지우고 앱을 한 번 켜세요."
Write-Host ""
