# 작업 일지 (Journal)

## 2026-09-14: async 전용 전환과 공통 TPS

사용자 요청에 따라 기존 비동기 구현을 AirKoreaClient로 통합한다. 동기 클라이언트와
aio facade를 제거하고 public 조회·debug·페이지 순회를 await/async for로 제공한다.
공통 AsyncTokenBucket을 패키지 내부에 포함하고 서비스·재시도·redirect 예산을 공유한다.
이 결정은 ADR-5의 동기/비동기 병행 결정을 대체한다. 오프라인 147개·커버리지 91.45%·리뷰 2인·live 재검증 2개를 통과했다. 자세한 범위는 `docs/verification-async-tps.md`를 참고한다.

이 문서는 `python-airkorea-api` 프로젝트의 개발 히스토리와 주요 변경 사항을 역시간순(최근 순)으로 상세히 기록한다.

---

## 2026-05-31: maplibre-vworld-js 스타일 및 MCP 설정 이식

- **작업 내용**:
  - `maplibre-vworld-js` 프로젝트의 완성도 높은 에이전트 협업 스타일과 로컬 개발자/에이전트 설정을 이식했다.
  - `.gemini/mcp.json`, `.claude/settings.local.json`, `.codex/config.toml` 등의 로컬 설정과 루트 설정들을 완비했다.
  - 최상위 세션 요약 컨텍스트인 `CLAUDE.md`를 생성했다.
  - `AGENTS.md`와 `SKILL.md`를 디렉토리 지도, DO NOT 규칙, 자주 묻는 작업 매핑, 작업 후 체크리스트 등으로 리팩토링 및 보완했다.
  - 에이전트 히스토리 추적용으로 `docs/decisions.md` (ADR), `docs/journal.md`, `docs/tasks.md`, `docs/resume.md`를 신설하여 다중 에이전트 연속성 체계를 구축했다.
- **결정 사항**:
  - `codegraph` 등 로컬 MCP 환경 디렉토리를 현재 프로젝트 경로(`F:\dev\python-airkorea-api`)로 단일화했다.
- **다음 작업**:
  - 품질 게이트 검증을 거쳐 PR 생성, 병합 및 리모트 푸시 수행.

---

## 2026-05-22: 문서 한글 작성 원칙 반영

- **작업 내용**:
  - 소스코드 주석, docstring, 그리고 저장소의 모든 `.md` 문서를 한글로 통일 및 재작성했다.
  - 공식 식별자와 명령어를 제외한 컨텐츠를 한글로 가다듬어 유지보수 효율성을 극대화했다.
- **결정 사항**:
  - `AGENTS.md`에 공식적으로 Markdown 및 RST 한글 통일 원칙을 명문화했다.

---

## 2026-05-21: data.go.kr 서비스키 환경변수 연동 및 보안 강화

- **작업 내용**:
  - 공공데이터포털 실제 호출용 인증키가 코드베이스에 평문 유출되는 사고를 방지하기 위해 `DATA_GO_KR_SERVICE_KEY` 환경변수 주입 메커니즘을 적용했다.
  - `tests/conftest.py` 등 테스트 fixture 전반에 마스킹을 강화했다.

---

## 2026-05-19: httpx 비동기 파사드 클라이언트 전환

- **작업 내용**:
  - AirKoreaClient에 `httpx` 비동기 통신을 전면 적용하고 동기 호출도 호환될 수 있는 하이브리드 파사드를 구현했다.
- **결정 사항**:
  - 대기오염 및 측정소 정보 API에 대한 convenience method를 `AirKoreaClient`에 다수 신설했다.

---

## 2026-05-18: 프로젝트 시작 및 좌표/모델 기초 설계

- **작업 내용**:
  - 한국환경공단 AirKorea API에 대응하는 비공식 Python SDK 개발 프로젝트를 런칭했다.
  - Pydantic을 이용한 정밀한 스키마 파싱 및 `pyproj` 라이브러리를 사용한 WGS84 ⇄ TM 좌표계 변환 모듈(`coords.py`)을 설계했다.
