# 2026-11-19 — 최초 export capture 실패의 안전한 진단

최초 export savepoint 예외를 고정 capture_failed로만 남겨 원인을 잃던 경고에
예외 클래스·원인 클래스·SQLSTATE·SDK provider result code와 attempt/run 상관관계를 추가한다.
예외 원문·repr·SQL·파라미터·원천/provider key·URL·traceback은 기록하지 않는다.

기존 export_collector.failure_reason의 타입/SQLSTATE 패턴을 확인했다. 이 모듈은
repository를 import하므로 역방향 import를 만들지 않고 repository 안의 작은 private
추출기만 사용한다. Common에서 바로 사용할 대응 helper는 확인되지 않았다.

## 필드 경계

- 클래스 이름은 ASCII identifier 80자 이하만 남긴다. orig와 명시 cause는 최대8회만
  방문하고 순환을 끊는다. 진단 property가 실패해도 원래 cleanup을 방해하지 않는다.
- SQLSTATE는 정확히5자의 ASCII 대문자/숫자다. provider_result_code는 실제 SDK
  KmaError 계층의 result_code 중 ASCII 숫자2자리만 허용한다. 원문에서 코드를 추측하지 않는다.
- attempt는 생성된 UUID 형태, run은 생성된 run_ + 32자리 lowerhex 형태만 남긴다.
  모호하거나 유효하지 않은 값과 없는 진단 필드는 대체 표기만 사용한다.
- 최초 경고는 보조 실패 표시 전에 남긴다. cleanup 예외가 발생해도 최초 진단이 사라지지
  않는다. 기존 session 활성 검사, export savepoint, fact commit, 실패 표시/전파 분기는 유지한다.

공개 capture_failed 코드·DB schema·head/receipt 무결성·native ingest·수집 주기·quota·TTL은
바꾸지 않는다. 이 변경은 기존2건의 capture 원인을 복구하거나 과거6건의 취소 주체를
판정하지 않는다. 과거 로그의 같은-attempt proof는 후속 계획/상태 거절로만 확인됐다.

## 검증 범위

순수 시험은 SQLAlchemy orig/명시 cause/직접 SDK 오류, 잘못된 코드·Unicode, 일반 예외의
가짜 result_code, 순환과 고장난 property, str/repr 접근 금지, 비밀 sentinel을 검증한다.
실제 소유 PG 시험은 원문/SQL/params/SDK metadata의 비밀 비노출과 최초 타입/코드,
원래 fact commit·failed capture·receipt rollback, 실제42P01 savepoint 복구,
cleanup 실패 때 최초 진단과 기존 preparing/no-receipt 상태를 확인한다.
기존 잘못된 capture 문맥·전체 rollback·정상 replay receipt 계약도 함께 확인한다.

시험 DB는 기존 합성 로컬 PG의 별도 소유 DB만 사용한다. 운영 DB/HTTP/provider/프로파일은
사용하지 않는다. 전체 저장소 CI와 정상 이미지 적용은 별도 후속이다.

최종 로컬 결과는 순수17건(0.31초), 신규 실제 PG5건(61.65초), 기존 receipt 계약5건
(56.03초) 통과다. 전체 Ruff·diff 검사도 통과했다. 첫 실행의 module/function fixture
scope 오류5건은 시험 fixture를 기존 환경변수 패턴으로 고친 뒤 위 결과로 재검증했고
초기 로그를 보존했다. 소유 시험 DB는 삭제하고 공용 로컬 fixture container는 보존했다.
