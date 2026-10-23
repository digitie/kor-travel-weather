# 중기예보 실제 수집의 export 연결

기본 OFF인 중기 전용 job에 export capture를 추가한다. 기본 격자/특보·기존 OFF
경로는 유지하며 원본 이력·현재값 저장의 unique/upsert 계약은 바꾸지 않는다.

## 원천·메모리·실패 경계

- 같은 육상 지역이 여러 기온 지역에 속해도 `(source_part, region_code)`마다 한 번만
  HTTP를 호출한다. source identity를 위치 청크마다 바꾸거나 중복 owner를 claim하지 않는다.
- 공개 SDK의 최신 중기 발표 회차를 한 번 선택해 육상·기온 요청에 같은 `tm_fc`를
  명시한다. SDK의 격자/발표 정책을 재구현하지 않는다.
- native 시작 INSERT에 export cohort intent를 남기고 실제 지역별 선택 집합과 모든
  source 계획을 첫 HTTP 전에 선언한다. 계획 실패는 원본 수집을 계속하게 하지만
  이전 정상 집합을 새 성공으로 오인하지 않도록 intent를 보존한다.
- 한 지역의 원문 한 행·26/16개 정규화 template만 유지한다. 원문을 location별
  최대50곳·2,500값의 기존 bounded native 저장으로 fan-out한다. 수집 전체의 응답/
  fact 예산과 heartbeat를 적용한다.
- capture 실패는 원본 저장을 한 번 계속한다. DB 실패는 기존 runner로 전파하고
  이미 commit된 fact는 유지한다. 마지막 native 성공 전에는 export head를 승격하지 않는다.
- 일반 fan-out collector의 bind 호출만 override 가능한 공통 seam으로 바꿨다.
  기존 격자 collector의 기본 `_bind_source`는 같은 KMA 검증 메서드를 호출한다.

## 검증 및 남은 수용

- 실제 공개 `DataGoKrClient`+offline httpx transport, 기존 native runner를 실행한
  집중6건 PASS: pre-HTTP 전체 계획, 같은 회차/지역 요청 dedupe,120곳 큰 지역,
  cohort 실패의 원본 저장, 메모리·예산 상한, 중복 위치·역할 거절.
- 기존 OFF KMA runner23건 PASS(실 PostgreSQL1건은 이 집중 실행에서 제외).
- 새 전용 DB의0001→0033 migration 뒤 실제 native capture→3개 head 승격→6개
  역할별 inventory→다음 HTTP 실패 시3개 시도 퇴역·이전6개 승인 보존을 확인했다.
  관련 PostgreSQL10건 PASS(14.01초), 실제 asset ON/OFF21건 PASS.
- 전체 Ruff PASS. REST 중기 배열 연결, 두 독립 적대 리뷰와 정확
  CI 및 HTTP/UI, ARM 운영 비용은 아직 별도 수용 전이다.
- source inventory·mid generic head proof는 선행 PR#155, 스트리밍 template은
  PR#157에 의존한다. 단순히 이 어댑터 파일만으로 운영 API 전체를 활성화하지 않는다.

초기 PostgreSQL 검증의 두 실패는 SDK가 HTTP 예외 문구를 일반화하는 계약과
publisher 결과가 `FAILED`가 아닌 `REJECTED`라는 시험 기대값 불일치였다.
제품 코드를 바꾸지 않고 실제 공개 예외·반환 상태에 맞춰 수정 후10건이 통과했다.