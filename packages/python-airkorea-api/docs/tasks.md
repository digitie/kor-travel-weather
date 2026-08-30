# 태스크 백로그 (Tasks)

이 문서는 `python-airkorea-api` 프로젝트의 개발 및 기능 고도화 요구사항을 T-NNN 단위의 태스크 백로그로 정리하여 추적한다.

---

## 작업 현황 요약

| 태스크 ID | 제목 | 우선순위 | 상태 | 담당 |
|-----------|------|----------|------|------|
| **T-004** | maplibre-vworld-js 스타일 및 MCP 설정 이식 | 높음 | **진행 중** | Antigravity |
| **T-001** | AirKorea CLI 기능 고도화 (JSON 포맷팅/매핑) | 보통 | 대기 중 | 미정 |
| **T-002** | `@pytest.mark.integration` 테스트 커버리지 확대 | 보통 | 대기 중 | 미정 |
| **T-003** | 캐싱 데코레이터 또는 캐싱 미들웨어 추가 | 낮음 | 대기 중 | 미정 |

---

## 상세 태스크 정의

### T-004: maplibre-vworld-js 스타일 및 MCP 설정 이식

- **목표**: `maplibre-vworld-js` 프로젝트의 에이전트 협업 스타일 및 MCP 개발 환경을 가져와 정착시킨다.
- **체크리스트**:
  - [x] `.gemini/mcp.json`, `.claude/settings.local.json`, `.codex/config.toml` 생성
  - [x] 루트 `antigravity.json`, `claude.json`, `codex.json` 생성
  - [x] 최상위 `CLAUDE.md` 작성 및 `AGENTS.md`, `SKILL.md` 보완
  - [x] `docs/` 내 decisions, journal, tasks, resume 생성
  - [ ] 전체 정적 분석 및 테스트 검증
  - [ ] PR 생성, 머지 및 리모트 푸시
- **결과**: 진행 중.

---

### T-001: AirKorea CLI 기능 고도화

- **목표**: Click 기반의 CLI(`cli.py`)를 고도화하여 일반 개발자가 터미널 환경에서 측정소 정보 및 대기 오염 수준을 유연하게 출력할 수 있도록 개선한다.
- **요구사항**:
  - JSON 출력 옵션(`--json`) 및 정교한 포맷팅 지원
  - 검색 필터 다변화
  - 파라미터 매핑 개선

---

### T-002: 통합 테스트 커버리지 확대

- **목표**: 실 공공데이터 API를 직접 검증하는 `@pytest.mark.integration` 테스트 항목을 추가 발굴 및 안정화한다.
- **요구사항**:
  - API 쿼리 파라미터 변경 시의 예외 케이스 점검
  - 실제 네트워크 환경에서의 에러 정규화 및 exception 매핑 검증

---

### T-003: 캐싱 레이어 추가

- **목표**: 공공데이터 API의 반복 호출로 인한 트래픽 및 쿼터 제한을 절약하기 위해, 경량 캐시 레이어를 도입한다.
- **요구사항**:
  - 메모리 캐시 및 TTL 기반 만료
  - 캐시 바이패스 옵션 지원
