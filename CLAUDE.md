# 프로젝트: 업무 녹화 매크로 AI

## 목표
사용자가 웹 브라우저 또는 Excel에서 업무를 한 번 시연하며 녹화하면,
AI가 그 과정을 해석해 (1) 업무 매뉴얼과 (2) 다시 실행 가능한 매크로를 함께 만든다.

## 최종 목표와 데이터 원칙 (→ docs/decisions/006)
- **최종 목표는 실제 업무에서 쓰는 것**이다. 사용자는 회사 시스템 화면·내부 엑셀 파일(내부 정보)을 녹화해 쓸 수 있어야 한다.
- **개발·시험·시연·평가는 가상 데이터로만** 한다: 가상 시스템(mock_system/ — 가나다증권 /login, 새솔대학교 /portal)과
  samples/의 가상 엑셀 파일. 개발 중에 실제 회사·학교 시스템이나 실제 내부 정보로 시험하지 않는다.
- 가상 데이터의 회사명, 고객명, 숫자는 모두 지어낸 값이다.
- AI 연결: 시험·개발은 Gemini 무료 API(외부 서비스). **실제 업무(내부 정보)에서는 회사 내부 AI(API)로 바꿔 쓴다**
  (.env의 LLM_PROVIDER=openai, LLM_BASE_URL — OpenAI 호환). 외부 AI가 연결된 상태에서 실제 업무 대상을 녹화하면 화면에서 경고한다.
- 사용자 데이터(녹화, 올린 파일, 실행 결과, 즐겨찾기, 연결, .env)는 각자 PC에만 두고 커밋하지 않는다.
- 엑셀 원본 파일은 절대 수정하지 않는다. 녹화·실행 모두 복사본에서 작업하고 결과는 새 파일(_결과.xlsx)로 저장한다.
- API 키는 .env에만 두고 코드에 하드코딩하지 않는다.

## 기술 스택
- Python 3.11+
- 가상 업무 시스템: Flask + HTML/CSS (프런트 프레임워크 없이 단순하게)
- 녹화/재실행: Playwright (Python)
- 엑셀 녹화/재실행: 실제 Microsoft Excel을 pywin32(COM)로 조작 (Windows + Excel 필요, → docs/decisions/004)
  - recorder/excel_com.py 한 곳에 Excel 연동 코드를 모은다. 녹화는 0.5초마다 상태를 비교하는 방식
- AI 해석: analyzer/llm_client.py 한 곳에서만 호출. 연결 방식은 .env로 고른다 (코드 수정 없이)
  - gemini: Google Gemini API 무료 등급 (SDK: google-genai) — 시험·개발용
  - openai: OpenAI 호환 API — 사내 AI 게이트웨이·사내 설치 모델 (표준 라이브러리 urllib, 추가 패키지 없음)
  - 모델 이름은 .env의 LLM_MODEL로 정하고 코드에 하드코딩하지 않는다
  - 요청 횟수 제한이 있으므로 요청을 묶어 보내고, 429·5xx 오류는 기다렸다 재시도한다
- 사용자 화면: Flask(app/server.py) + HTML/CSS/JS(app/static/), 프레임워크 없이. start.bat로 실행 (→ docs/decisions/003)

## 폴더 구조
mock_system/   가상 업무 시스템
recorder/      녹화기
analyzer/      AI 해석 (단계 의미, 변수 인식, 판단 기준 질문, 매뉴얼 생성)
               overview.py 업무·화면 개요 / review.py 매뉴얼 수정 검토(코드+AI)
               assistant.py 매뉴얼 질의응답 / edit.py 말로 고치기(변경안→시험 실행→적용)
runner/        매크로 재실행
app/           관리 화면 (Flask API + 정적 HTML)
eval/          평가 스크립트와 시나리오
samples/       엑셀 녹화용 가상 엑셀 파일
chains/        업무 연결 정의 (A 결과 → B 입력, 개인 매크로를 가리키므로 커밋하지 않음)
docs/          meetings, decisions, prompts, results

## 데이터 형식
- 녹화 결과: recordings/<id>/events.json + screenshots/<nnn>.jpg (+ steps.json, questions.json, answers.json)
  - 엑셀 녹화는 events.json에 "kind": "excel", 원본 복사본 input.xlsx와 작업본 work.xlsx가 함께 있다
  - 엑셀 단계·매크로 단계에는 실행 정보가 "excel" 필드(op, sheet, header, 조건 등)로 들어간다
- 실행 결과: runs/<id>_<타임스탬프>/result.json (v1 재생과 매크로가 같은 형식)
- 매크로: macros/<id>.json (단계 목록 + 변수 정의)
- 매뉴얼: manuals/<id>.md (직접 수정·다시 생성 전 내용은 recordings/<id>/manual_history/에 자동 보관)
- 업무 연결: chains/<id>.json, 실행 결과 runs/chain_<id>_<타임스탬프>/chain_result.json
- AI 부가 기능: recordings/<id>/overview.json(개요), chat.json(질의응답), edits/<pid>*(말로 고치기 변경안),
  macro_history/(적용 전 매크로 보관)
- 말로 고치기는 매크로의 허용된 필드(값·설명·대상·엑셀 열/수식/조건)만 바꾸고, 시험 실행이 통과해야 적용을 권한다.
  웹 요소 변경은 실제 화면 요소 목록에서만 고른다 (AI가 선택자를 지어내지 않음).

## 녹화기 주의 (recorder/record.py)
- Playwright 이벤트 처리 함수(바인딩, framenavigated, load) 안에서 로딩 완료를 기다리지 않는다. 서로 기다리다 멈춘다.
  페이지 이동 스크린샷은 바깥 반복(flush)에서, 클릭 스크린샷은 pointerdown 순간에 미리 찍는다.
- 자동 시연 스크립트의 page.click은 _HumanPage가 '페이지 안에서 누르기'로 바꾼다 (Playwright 클릭은 이동 완료를 기다려 같은 문제가 생김).
- 사용자에게 보이는 문장의 조사 표기는 analyzer/josa.py의 fix_josa()를 거친다.

## 설계 원칙 (AI와 코드의 역할 분담)
- 동작 종류, 입력값, 요소 위치(id/label/텍스트/선택자)는 녹화 기록에서 코드가 정한다. AI가 바꾸지 못한다.
- AI는 의미 해석(대상 이름, 설명, 불필요한 클릭 제외, 변수/고정 판단, 질문, 매뉴얼 문장)만 맡는다.
- AI 응답은 항상 검증·보정한다. 빠지거나 틀린 부분은 규칙 기반 값으로 채운다.
- 모든 analyzer 명령은 --no-ai(규칙 기반)로도 동작해야 한다. 키 없이 테스트하고, AI 효과를 비교하는 기준이 된다.

## 작업 방식
- 작은 단위로 만들고, 각 단계마다 실행 방법과 확인 방법을 README에 적는다.
- 확실하지 않은 설계는 임의로 정하지 말고 선택지를 제시하고 물어본다.
- 함수에는 짧은 한국어 주석을 단다.
