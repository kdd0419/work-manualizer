# 프로젝트: 업무 녹화 매크로 AI

## 목표
사용자가 웹 브라우저에서 업무를 한 번 시연하며 녹화하면,
AI가 그 과정을 해석해 (1) 업무 매뉴얼과 (2) 다시 실행 가능한 매크로를 함께 만든다.

## 절대 규칙 (보안)
- 실제 회사 시스템, 실제 데이터는 절대 사용하지 않는다.
- 녹화 대상은 이 저장소의 가상 시스템(mock_system/)뿐이다: 가나다증권 업무시스템(/login), 새솔대학교 포털(/portal).
- 실제 학교 포털(출석·댓글 등)은 학칙·개인정보 문제로 녹화·자동화하지 않는다. 같은 흐름을 가상 포털로 시연한다.
- 가상 데이터의 회사명, 고객명, 숫자는 모두 지어낸 값이다.
- API 키는 .env에만 두고 코드에 하드코딩하지 않는다. .env, recordings/ 원본은 커밋하지 않는다.

## 기술 스택
- Python 3.11+
- 가상 업무 시스템: Flask + HTML/CSS (프런트 프레임워크 없이 단순하게)
- 녹화/재실행: Playwright (Python)
- AI 해석: Google Gemini API 무료 등급 (SDK: google-genai). analyzer/llm_client.py 한 곳에서만 호출
  - 모델 이름은 .env의 LLM_MODEL로 정하고 코드에 하드코딩하지 않는다
  - 무료 등급은 요청 횟수 제한이 있으므로 요청을 묶어 보내고, 429 오류는 기다렸다 재시도한다
- 사용자 화면: Flask(app/server.py) + HTML/CSS/JS(app/static/), 프레임워크 없이. start.bat로 실행 (→ docs/decisions/003)

## 폴더 구조
mock_system/   가상 업무 시스템
recorder/      녹화기
analyzer/      AI 해석 (단계 의미, 변수 인식, 판단 기준 질문, 매뉴얼 생성)
runner/        매크로 재실행
app/           관리 화면 (Flask API + 정적 HTML)
eval/          평가 스크립트와 시나리오
docs/          meetings, decisions, prompts, results

## 데이터 형식
- 녹화 결과: recordings/<id>/events.json + screenshots/<nnn>.jpg (+ steps.json, questions.json, answers.json)
- 실행 결과: runs/<id>_<타임스탬프>/result.json (v1 재생과 매크로가 같은 형식)
- 매크로: macros/<id>.json (단계 목록 + 변수 정의)
- 매뉴얼: manuals/<id>.md

## 설계 원칙 (AI와 코드의 역할 분담)
- 동작 종류, 입력값, 요소 위치(id/label/텍스트/선택자)는 녹화 기록에서 코드가 정한다. AI가 바꾸지 못한다.
- AI는 의미 해석(대상 이름, 설명, 불필요한 클릭 제외, 변수/고정 판단, 질문, 매뉴얼 문장)만 맡는다.
- AI 응답은 항상 검증·보정한다. 빠지거나 틀린 부분은 규칙 기반 값으로 채운다.
- 모든 analyzer 명령은 --no-ai(규칙 기반)로도 동작해야 한다. 키 없이 테스트하고, AI 효과를 비교하는 기준이 된다.

## 작업 방식
- 작은 단위로 만들고, 각 단계마다 실행 방법과 확인 방법을 README에 적는다.
- 확실하지 않은 설계는 임의로 정하지 말고 선택지를 제시하고 물어본다.
- 함수에는 짧은 한국어 주석을 단다.
