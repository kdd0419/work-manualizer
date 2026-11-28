# 업무 녹화 매크로 AI

브라우저 또는 **Excel**에서 업무를 한 번 시연·녹화하면, AI가 해석해 **업무 매뉴얼**과 **다시 실행 가능한 매크로**를 함께 만들어 주는 프로젝트입니다.

> **최종 목표는 실제 업무 사용**입니다. 회사 시스템·내부 파일도 녹화할 수 있습니다.
> 개발·시험·시연·평가는 저장소 안의 가상 업무 시스템(`mock_system/`)과 가상 엑셀 파일(`samples/`)로만 하고,
> 실제 업무에서 AI를 쓸 때는 외부 Gemini 대신 **회사 내부 AI(OpenAI 호환 API)** 로 바꿉니다. → [docs/decisions/006](docs/decisions/006-실제-업무-사용과-사내-AI.md)

## 빠른 시작 (더블클릭)

1. **`setup.bat`** 더블클릭 — 처음 한 번만 (가상환경, 패키지, Chromium, 가상 데이터 설치)
2. **`start.bat`** 더블클릭 — 관리 화면(http://localhost:8000)이 브라우저에 열립니다. 가상 업무 시스템(http://localhost:5000)도 함께 켜집니다.
3. 화면에서 ① 녹화 → ② 해석 → ③ 매뉴얼 → ④ 매크로 실행 순서로 사용합니다.

Gemini 키가 없으면 규칙 기반으로 동작합니다(상단에 "AI 키 없음 · 규칙 기반" 표시). 채점·시연용 안내는 [실행안내.md](실행안내.md)를 보세요.

## 설치 (직접 명령으로 할 때, Windows PowerShell)

Python 3.11 이상이 필요합니다. (https://www.python.org/downloads/ 에서 설치할 때 **"Add python.exe to PATH"**를 체크하세요.)

```powershell
python -m venv .venv               # Anaconda만 있고 python이 PATH에 없으면: & "$env:USERPROFILE\anaconda3\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1        # 실행 정책 오류가 나면: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env         # .env를 열어 LLM_API_KEY 값을 채운다 (5단계부터 필요)
```

### 사내 AI로 바꾸기 (실제 업무용)

`.env`에서 `LLM_PROVIDER=openai`, `LLM_BASE_URL=<사내 AI 주소>/v1`, `LLM_API_KEY`, `LLM_MODEL`을 채우면 코드 수정 없이 바뀝니다.
OpenAI 호환 형식(`/chat/completions`, `/models`)을 쓰는 사내 게이트웨이·사내 설치 모델(vLLM, Ollama 등)이면 됩니다.
이미지를 못 받으면 `LLM_SEND_IMAGES=false`, 사내 인증서 문제면 `LLM_VERIFY_SSL=false`. 자세한 내용은 [실행안내.md](실행안내.md)의 "AI 기능".

### Gemini API 키 (무료, 시험·개발용)

AI 해석(5~7단계)에는 Google Gemini API 무료 등급을 씁니다. → [docs/decisions/002-LLM-선택.md](docs/decisions/002-LLM-선택.md)

1. https://aistudio.google.com 에 Google 계정으로 로그인 → **Get API key** → **Create API key**
2. `.env`의 `LLM_API_KEY=` 뒤에 붙여 넣기 (키는 절대 코드·채팅·문서에 붙여 넣지 않기)
3. 연결 확인:
   ```powershell
   python -m analyzer.llm_client          # "연결 성공"이 나오면 OK
   python -m analyzer.llm_client --list   # 쓸 수 있는 모델 목록. 기본 모델이 안 되면 여기서 골라 .env의 LLM_MODEL에 적기
   ```
- 무료 등급은 분당/일일 요청 수 제한이 있습니다. `429` 오류가 계속 나면 잠시 쉬었다가 다시 실행하세요.
- 현재 한도는 AI Studio의 사용량(Rate limit) 화면에서 확인할 수 있습니다.

## 폴더 구조

| 폴더 | 설명 |
|---|---|
| `mock_system/` | 가상 증권사 본부 업무 시스템 (Flask + SQLite) |
| `recorder/` | Playwright 녹화기, 엑셀 녹화기(`excel_record.py`, `excel_com.py`) |
| `samples/` | 엑셀 녹화용 가상 엑셀 파일 (`python -m samples.make_samples`로 다시 만들 수 있음) |
| `uploads/` | 관리 화면에서 올린 엑셀 파일 (커밋하지 않음) |
| `favorites.json` | 녹화 대상 사이트 즐겨찾기 (각자 PC 설정, 커밋하지 않음. 없으면 가상 시스템 3개가 기본) |
| `analyzer/` | AI 해석: 단계 의미, 변수 인식, 판단 기준 질문, 매뉴얼 생성 |
| `runner/` | 녹화 그대로 재생(v1), 변수 매크로 실행(v3+) |
| `app/` | 관리 화면 (Flask 서버 + HTML/CSS/JS) |
| `setup.bat`, `start.bat` | 설치, 실행 (더블클릭) |
| `eval/` | 버전별 평가 스크립트와 시나리오 |
| `docs/` | meetings(회의록), decisions(설계 결정), prompts(프롬프트 기록), results(평가 결과) |
| `recordings/` | 녹화 결과 (커밋하지 않음) |
| `macros/`, `manuals/` | 생성된 매크로 JSON, 매뉴얼 마크다운 |

---

## 가상 대학 포털 (새솔대학교, `mock_system/portal.py`)

주소: http://localhost:5000/portal (첫 화면 http://localhost:5000 에서 두 시스템 중 선택)

| 화면 | 주소 | 요소 id |
|---|---|---|
| 로그인 | `/portal/login` | `portal-id`(학번), `portal-password`, `portal-login-button` |
| 홈 | `/portal/` | `menu-attendance`, `menu-board`, `course-table` |
| 전자출석 | `/portal/attendance` | `course-select`(과목), `attendance-code`(4자리), `attendance-submit`, `attendance-history` |
| 강의 게시판 | `/portal/board` | `board-course`, `board-search`, `post-<번호>`, `no-result` |
| 게시글·댓글 | `/portal/board/<번호>` | `comment-content`, `comment-submit`, `comment-list` |

- 같은 과목은 하루에 한 번만 출석 → 두 번째는 `error-message` ("이미 오늘 출석한 과목입니다") → v4가 감지해 멈춤
- 출석·댓글 등록 버튼은 POST 폼이라 **데이터 변경 단계**로 분류 → 실행 직전 확인
- 실제 학교 포털 대신 가상 포털을 쓰는 이유는 [CLAUDE.md](CLAUDE.md)의 보안 규칙 참고

## v0-a: 가상 증권사 업무 시스템

### 실행

```powershell
python -m mock_system.seed          # 가상 데이터 생성 (업체 20개, 거래 3개월치). 다시 실행하면 초기화됨
python -m mock_system.app           # http://localhost:5000
```

- DB가 없으면 `app` 실행 시 시드가 자동으로 만들어집니다.
- 데이터 기준일을 고정하려면: `python -m mock_system.seed --base-date 2026-11-18` (평가할 때 권장)

### 화면별 확인 방법

| # | 화면 | 주소 | 확인할 것 |
|---|---|---|---|
| 1 | 로그인 | `/login` | 아무 아이디/비밀번호로 로그인 → 메인 메뉴로 이동 |
| 2 | 메인 메뉴 | `/menu` | 업무 3개 링크가 보임 |
| 3 | 위탁업체 점검 현황 조회 | `/inspections/status` | 기준일자·업체 구분·상태 선택 → **조회** → 표 표시 → **엑셀 다운로드** 시 `위탁업체점검_YYYYMMDD.csv` |
| 4 | 일별 거래 집계 조회 | `/trades/daily` | 시작일~종료일·상품 구분 → **조회** → 표·합계 표시 → 다운로드 시 `일별거래집계_YYYYMMDD_YYYYMMDD.csv` |
| 5 | 점검 결과 등록 | `/inspections/new` | 업체·점검일·결과·비고 입력 → **저장** → 초록색 "저장되었습니다" 메시지. 3번 화면에서 해당 업체 상태가 바뀌었는지 확인 |

예외 확인:
- 3번에서 결과가 없게 조회(예: 기준일자 `2020-01-01` — 그 시점엔 계약 업체가 없음) → "조회 결과가 없습니다." (`id="no-result"`)
- 4번에서 시작일 > 종료일 → 오류 메시지 (`id="error-message"`)
- 5번에서 업체를 고르지 않고 저장 → 오류 메시지

### 녹화·재실행을 위한 요소 id 목록

| 화면 | 요소 id |
|---|---|
| 공통 | `home-link`, `logout-link`, `save-message`(성공 메시지), `error-message` |
| 로그인 | `login-id`, `login-password`, `login-button` |
| 메뉴 | `menu-inspection-status`, `menu-daily-trades`, `menu-inspection-new` |
| 점검 현황 | `inquiry-date`, `vendor-category`, `vendor-status`, `search-button`, `result-count`, `result-table`, `download-csv`, `no-result` |
| 거래 집계 | `start-date`, `end-date`, `product-type`, `search-button`, `result-count`, `result-table`, `download-csv`, `no-result` |
| 점검 등록 | `vendor-select`, `inspection-date`, `result-1`(적합) / `result-2`(보완 필요) / `result-3`(부적합), `inspection-note`, `save-button` |

모든 입력 요소에는 `<label for=...>`가 연결되어 있습니다.

---

> 아래 명령은 모두 가상환경을 켜고(`.\.venv\Scripts\Activate.ps1`) 가상 업무 시스템을 실행한 상태에서 씁니다.
> `analyzer` 명령에 `--no-ai`를 붙이면 Gemini 없이 규칙 기반으로 동작합니다 (테스트·비교용).

## v0-b: 녹화기 (`recorder/`)

```powershell
python -m recorder.record --url http://localhost:5000 --name 위탁업체점검조회
```

- Chromium 창이 열리면 업무를 시연합니다. **터미널에서 Enter** 또는 **브라우저 창 닫기**로 종료합니다.
- 기록: 클릭, 입력(변경 완료 시점에 한 번만), 드롭다운 선택, 제출, 페이지 이동, 다운로드 파일명
- 비밀번호 입력값은 `***`로 가려 저장합니다.
- 결과: `recordings/<이름_타임스탬프>/events.json`, `screenshots/001.jpg …`(1280px, JPEG 70), `downloads/`

**확인 방법**: 로그인 → 위탁업체 점검 현황 조회 → 기준일자 입력 → 조회 → 엑셀 다운로드 후 창을 닫고,
`events.json`에서 `input`(기준일자), `click`(조회), `download`(위탁업체점검_YYYYMMDD.csv)가 있는지 봅니다.

## v1: 녹화 그대로 재생 (`runner/replay.py`) — 기준선

```powershell
python -m runner.replay recordings/<id>            # --headless 로 창 없이
```

- 녹화된 CSS 선택자와 입력값을 그대로 씁니다. 실패하면 그 단계에서 멈추고 스크린샷을 남깁니다.
- 결과: `runs/<id>_<타임스탬프>/result.json`, 마지막에 "총 N단계 중 M단계 성공"

## v2: AI 단계 해석 + 매뉴얼 (`analyzer/interpret.py`, `analyzer/manual.py`)

```powershell
python -m analyzer.interpret recordings/<id>       # → recordings/<id>/steps.json
python -m analyzer.manual recordings/<id>          # → manuals/<id>.md
```

- 이벤트와 스크린샷(1024px로 축소)을 8개씩 묶어 Gemini에 보냅니다.
- 코드가 정하는 것: 동작 종류·입력값·요소 위치 / AI가 정하는 것: 대상 이름·설명·의미 없는 클릭 제외·데이터 변경 여부
- 프롬프트는 `analyzer/prompts/<이름>_v1.txt`. 새 버전을 `_v2.txt`로 만들고 `--prompt-version v2`로 비교합니다.

## v3: 변수 인식 + 매크로 (`analyzer/variables.py`, `runner/macro.py`)

```powershell
python -m analyzer.variables recordings/<id>       # → macros/<id>.json
python -m runner.macro macros/<id>.json --var 기준일자=2026-11-20
```

- 변수 기본값 규칙: `today`, `today-1`, `month_start`, `prev_month_start`, `prev_month_end`, `recorded`(녹화값), `직접 입력`
- 요소는 id → label 텍스트 → 버튼 텍스트 → CSS 선택자 순으로 찾습니다.
- 실행 전에 변수 값과 단계 목록을 보여 주고 y/n을 묻습니다. (`--yes`로 생략)

## v4: 판단 기준 질문 + 실행 전 확인 + 실패 감지

```powershell
python -m analyzer.questions recordings/<id>            # 질문 2~5개 → questions.json
python -m analyzer.questions recordings/<id> --answer   # 터미널에서 답변 → answers.json
python -m analyzer.manual recordings/<id>               # 답변이 반영된 매뉴얼 (판단 기준·예외 상황)
```

매크로 실행(`runner.macro`)은 기본으로 v4 동작을 합니다.
- 요소를 못 찾거나, **조회 결과가 0건**이거나, 오류 메시지가 뜨거나, **예상과 다른 페이지**면 즉시 멈추고
  멈춘 단계·원인·스크린샷과 함께 "사람이 직접 처리하세요"라고 안내합니다.
- 저장·등록처럼 **데이터를 바꾸는 단계는 실행 직전에 한 번 더** y/n을 묻습니다. (`--allow-changes`로 생략)
- `--no-checks`를 붙이면 v3 방식(감지 없이 실행)으로 돌아갑니다.

## 엑셀 녹화 (실제 Microsoft Excel 연동) → [docs/decisions/004-엑셀-녹화.md](docs/decisions/004-엑셀-녹화.md)

Windows + Microsoft Excel이 필요합니다. 원본 파일은 바꾸지 않고, 녹화·실행 모두 복사본에서 작업합니다.

```powershell
python -m samples.make_samples                                         # 가상 엑셀 파일 4개 (점검 10·11월, 거래 10·11월)
python -m recorder.excel_record --file samples/위탁업체점검_2026-10.xlsx --name 부적합정리
#  → Excel이 열림. 작업 후 통합 문서를 닫으면 recordings/<id>/events.json ("kind": "excel")
python -m analyzer.interpret <id> --no-ai                              # 엑셀 녹화는 자동으로 엑셀 해석기 사용
python -m analyzer.variables <id> --no-ai                              # 변수: 입력파일, 필터값, 채울 값, 삭제 조건
python -m runner.macro macros/<id>.json --yes --var "입력파일=samples/위탁업체점검_2026-11.xlsx"
#  → runs/<id>_<시각>/위탁업체점검_2026-11_결과.xlsx
```

| 녹화에서 알아내는 것 | 해석 결과 (다른 파일에도 적용되는 규칙) |
|---|---|
| 필터 조건 | `'점검결과' 열을 '부적합'으로 필터링` |
| 행 내용은 같고 순서만 바뀜 | `'점검일자' 열 기준 오름차순 정렬` |
| 사라진 행에만 있는 값 | `'점검결과'가 '적합'인 행을 모두 삭제` |
| 필터로 보이는 행에만 같은 값 | `보이는 행의 '담당자' 열에 '김민지' 채우기` |
| 모든 행에 같은 상대 수식 | `'수수료' 열에 =F2*0.001 채우기` |
| 표 바로 아래 행 | `아래 행 추가` (합계 행, 수식 범위는 실행 파일 행 수에 맞춰 늘어남) |
| 새 시트에 입력 | `셀 입력` (다른 시트 참조 범위도 자동 확장) |
| 그 밖의 한 칸 입력 | 열쇠 열(값이 모두 다른 열, 예: 점검번호)로 행을 찾아 입력 |

실패 감지(v4): 파일 없음 · 머리글이 녹화 때와 다름 · 필터/삭제 뒤 0행 · 수식 오류(#DIV/0! 등) · 찾는 행 없음 →
멈추고 그때까지의 작업을 `_중단.xlsx`로 저장합니다. 기록되지 않는 작업: 피벗, 서식, 차트, 열 삽입.

## 업무 연결 (`runner/chain.py`) — A 결과 → B 입력

```powershell
# chains/<id>.json  {"name": "...", "steps": [{"macro": "<A 매크로 id>"}, {"macro": "<B 매크로 id>", "link_var": "입력파일"}]}
python -m runner.chain chains/<id>.json --values 값.json     # 값.json: {"0": {"비밀번호": "..."}, "1": {}} (읽은 뒤 지움)
#  → runs/chain_<id>_<시각>/chain_result.json
```
- A의 결과 파일(웹 다운로드 파일, 엑셀 `_결과.xlsx`)을 B의 파일 변수에 넣고 B를 실행합니다. 두 번째부터는 파일 변수가 있는 매크로(엑셀)만 가능.
- 중간에 실패하면 거기서 멈춥니다. 관리 화면에서는 ④ 매크로 실행 탭 아래 **🔗 업무 연결**에서 만들고 실행합니다.

## AI 부가 기능 → [docs/decisions/007-AI-부가-기능.md](docs/decisions/007-AI-부가-기능.md)

```powershell
python -m analyzer.overview <녹화 id> [--no-ai]            # 업무·화면 개요 → recordings/<id>/overview.json
python -m analyzer.edit <녹화 id> --request-file 요청.txt  # 말로 고치기 변경안 + 시험 실행 → recordings/<id>/edits/
```
- 관리 화면: 분석 탭 **업무·화면 개요** 카드, 매뉴얼 탭 **💬 물어보기**, **✍️ 말로 고치기**, 매뉴얼 저장 시 **수정 검토** 창.
- 확인: 매뉴얼에서 단계 하나를 지우고 저장 → "7단계가 빠졌습니다" 같은 경고가 뜨는지. 말로 고치기 → 변경안 표 + "모든 단계 성공" → [적용] 후 매크로 값이 바뀌는지.

## 시작 화면 시연 영상 (`tools/make_demo_videos.py`)

```powershell
python -m tools.make_demo_videos          # 관리 서버가 켜져 있어야 함. site / excel 하나만 만들 수도 있음
#  → app/static/videos/site_demo.webm, excel_demo.webm (+ 미리보기 jpg)
```
숨김 브라우저로 가상 포털 전자출석 시연·해석·매뉴얼·자동 실행(사이트), 엑셀 녹화 화면·해석·11월 파일 실행·결과(Excel)를
자막과 함께 녹화합니다. 화면이 바뀌면 다시 만들면 됩니다. (사이트 영상은 가상 데이터를 오늘 기준으로 초기화한 뒤 녹화)

## 녹화 스크린샷 시점 (`recorder/record.py`)

- 보이는 화면만 찍습니다(페이지 전체 X). 외부 사이트의 긴 페이지를 통째로 찍으면 느리고 고정 메뉴가 그림 중간에 끼었습니다.
- 브라우저 자체 캡처(CDP)를 써서 웹 글꼴 로딩을 기다리지 않습니다. (예전 방식은 글꼴을 기다리느라 사이트 이동 때 몇 초씩 멈춤)
- 클릭은 **누르는 순간**(페이지 이동 전) 화면, 페이지 이동은 **로딩이 끝나고 0.35초 뒤** 화면. 누른 요소는 빨간 상자로 표시.
- 규칙 기반 문장의 `을(를)`, `이(가)`, `(으)로`는 `analyzer/josa.py`가 앞말 받침을 보고 하나로 고칩니다.

## 관리 화면 (`app/`)

```powershell
python -m app.server          # 또는 start.bat. http://localhost:8000
```

| 탭 | 하는 일 |
|---|---|
| 🧭 사용 안내 | 처음 열면 나오는 시작 화면. `실행안내.md` 내용을 섹션별로 보여 줌. **건너뛰기**, "다음부터 바로 시작" 선택 가능 |
| ① 녹화 | 녹화 대상 **[🌐 사이트] / [📊 Excel]** 선택 → 사이트는 즐겨찾기 카드 또는 주소 입력(☆로 즐겨찾기 추가, ★·✕로 삭제), Excel은 파일 선택·올리기 → 녹화 시작 → 시연 후 그 창을 닫으면 저장 → 녹화 목록 |
| ② 해석 | 단계 해석 → 단계 표 + 스크린샷 미리보기 → 변수 인식·매크로 생성 |
| ③ 매뉴얼 | 판단 기준 질문 생성 → 답변 → 매뉴얼 생성·미리보기·**PDF 다운로드**(스크린샷·쪽 번호 포함, 녹화용 Chromium으로 인쇄). **부분 수정**(섹션·단계별 ✏️ 수정, 전체 편집), **↶ 이전 버전** 되돌리기 |
| ④ 매크로 실행 | 매크로 선택 → 변수 입력(엑셀은 입력 파일 선택·올리기) → 단계 확인 → 실행 → 단계별 결과·다운로드/결과 파일·멈춘 화면. 결과 파일로 **다음 업무 실행 준비 / Excel 녹화** |
| ④ 🔗 업무 연결 | A 업무 결과 파일 → B 업무 입력 파일로 이어지는 연결을 만들고 한 번에 실행 |

- 왼쪽 사이드바: **🔖 업무 사이트** 바로가기(녹화 탭 즐겨찾기와 같은 목록, `favorites.json`) — 사내망 주소도 등록,
  이 PC에서 접속되는지 점 색으로 표시(초록 접속됨 / 빨강 안 됨 / 회색 꺼진 가상 사이트), 이름 클릭 = 새 탭 열기, 🎬 = 바로 녹화.
  AI 사용 스위치(키가 있을 때만 켜짐).
- 시험용 가상 시스템(포트 5000)은 처음부터 켜지 않고, 가상 사이트를 열거나 녹화·실행할 때만 자동으로 켭니다.
  **« 버튼 또는 Ctrl+B로 접기/펴기** (접으면 아이콘만, 마우스를 올리면 이름표. 상태는 기억됨)
- 구조: `app/server.py`가 API를 제공하고, 오래 걸리는 작업(녹화·해석·실행·업무 연결)은 별도 프로세스로 돌리며
  화면(`app/static/app.js`)이 진행 로그를 0.7초마다 받아 보여 줍니다.

## 평가 (`eval/`)

개발 중 버전별 재실행 성공률을 비교하던 도구입니다. 관리 화면에서는 뺐고(2026-11-26), 필요하면 명령으로만 실행합니다.
자세한 내용은 [eval/README.md](eval/README.md), 지난 결과는 `docs/results/`.

```powershell
python -m eval.run_all            # 데이터 초기화 → 시나리오 녹화 → 매크로 생성(AI) → v1/v3/v4 평가. 키 없으면 --no-ai
```
