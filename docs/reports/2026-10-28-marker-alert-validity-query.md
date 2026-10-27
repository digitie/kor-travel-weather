# 지도 특보의 반복 유효기간 조회 최소화

## 문제와 실제 경로

기본 지도는 종류 여섯 개를 명시한다. 따라서 저장소의 `kinds=None` 후보 최적화였던
PR #180을 기본 지도 경로로 추정한 이전 분석은 잘못이었다. 당시 실패·EXPLAIN·전체
시험 증거는 해당 무필터 범위의 이력으로 보존하며 실제 UI 경로 검증으로 확대하지 않는다.
이번 변경은 명시한 종류를 현재·예보·특보 세 그룹으로 나눈 뒤 실행하는 특보 조회만 다룬다.

운영 첫 실제 수용의 504 여섯 개와 쿠키 잔여 미계측은 그대로 남는다. 별도 읽기 전용
부분 프로파일에서는 실황 약1.509초·예보 약1.906초 뒤, 오래된 특보512키 검산 다섯 번이
모두0행이었다. import 약14.76초를 포함한 프로세스 wall25초 예산으로 중단했으므로
HTTP 504를 다시 재현했거나 특보 전체·DTO 시간을 측정한 것은 아니다.
이 반복을 전체 지연의 확정 원인으로 단정하지 않는다.

## 변경과 보존할 의미

기존 코드는 후보를512행씩 읽고 오래된 특보마다 별도 fact 조회를 수행했다. 새 코드는
같은 후보 SQL에서 아래 조건으로 판정하고 별도 Python 검산 SQL을 제거한다.

```text
target_at >= now - 3일
OR EXISTS fact(value_id = pointer.value_id,
               known_at = pointer.known_at,
               valid_until > now)
```

최근 행은 명시 만료·해제라도 그대로 후보에 남는다. 기존처럼 후보를26개까지 고른 뒤
최종 reducer가 만료·해제 상태를 반영해야 하기 때문이다. 모든 행에 `valid_until>now`를
적용하거나 오래된 행을 시간만으로 제거하면 결과가 달라진다. 오래된 명시 미래 종료는
유지하고, 오래된 NULL·종료시각과 now가 같은 행은 제외한다.

복합키·기존 필터·known/source/value ID 동률 순위·512행 cursor·위치별 top26·최종
reducer와 API 최종 표시 개수는 유지한다. None 경로, DTO, writer, provider, schema,
인덱스0034, timeout, limiter2/UI동시성3, export/GC/publisher 설정은 바꾸지 않는다.

## 재현과 로컬 계획

- 원6312 메서드만 별도 비운영 plugin으로 주입한 같은 입력에서 후보 키·순서와 최종
  reducer는 reference와 같았지만 SELECT14회/옛 검산8회라 새 왕복 제거 검사가 실패했다.
  원문은 `red-final-r2`에 보존했다. 초기 source/provider 불일치와 projection 중복 reference
  오류는 시험 준비 오류이며 제품 RED로 세지 않았다.
- 새 입력은60지점·지점당80개 특보 revision과 제공처 동률 행·실황/예보를 포함한다.
  실제 요청은6종류/limit80이며 None/selected/other/missing 제공처를 비교한다.
  후보 키와 순서·최근 명시 만료·해제 및 최종 reducer 결과를 비교한다.
- 단순 EXISTS 초안은 요청 밖1지점/80개 특보에서도 fact를 검산했다. Hash Join 전에
  필터가 실행되어 loops가2,400→2,480이 된 로컬 실패를 `offscope-red`로 보존했다.
  후보 자체에도 기존 `current_location_ids`의 ANY 조건을 명시해 범위를 고정했다.
  이후 계획은 위치 ANY의 index 조건, fact 복합 PK와 정확히2,400회 검산을 보였다.
  해당 known_at 두 파티션만1,200회씩 실행되고 나머지는0회였다.
- 최종 SELECT는 제공처 None/selected6회, other4회, missing3회이며 별도 오래된 fact
  검산 SQL은0회다. 최대500지점도 cursor512와 최종 winner를 검증한다.
- 다른 known_at 파티션에 같은 value_id와 미래 종료가 있어도 현재 pointer가 살아나지
  않는 회귀, cutoff 등호·1마이크로초 차이·valid_until==now·해제/tombstone·limit1을 검증한다.

시험은 소유한 disk-backed PostgreSQL16.14/max100·2GiB/1CPU의 새 nonce DB만 사용한다.
매회 종료/PID 소멸·연결0·DB 삭제를 검산하며 원 whole DB와 다른 작업 자원은 변경하지 않는다.
로컬 `EXPLAIN ANALYZE`는 합성 DB만 대상으로 한다. 운영 SQL·HTTP·수집·배포는0이다.

## 증거와 남은 게이트

로컬 원장 루트는 `/var/tmp/weather-alert-exists-qa-20261028`이다. `red-r1`/`red-r2` 준비
오류, `red-r3` 초기 RED, `green-r1`/`green-r2`, `focused-r1`6 PASS26.38초,
`focused-r2`53 PASS83.30초는 각각 당시 소스의 증거다. 요청 밖 범위 보완 뒤
`offscope-green`3 PASS9.73초를 확인했다. 요청 밖 범위 보완 뒤 ocused-final은 관련 API·특보·상세 포함53 PASS/1 warning,
84.87초·actual exit0이었다. 최종 정확 소스의 Docker 집중·CI·두 독립 리뷰는
별도 최종 원장과 PR에서 판정한다. 위 수치를 새 소스 전체 통과로 대체하지 않는다.

부모 PR #184의 정확72 whole은 WSL1505 PASS7796.07초, Docker1504 PASS/1 SKIP8043.21초,
actual exit0으로 종료했다. Docker skip은 `docker is not available`이다. cleanup6312는
제품/non-test282파일이72와 같고 집중6 PASS·CI1510 PASS였다. 이 이력은 이번 repository
변경의 전체 검증이나 운영 효과 증거가 아니다.

운영 ARM 데이터 규모·준비된 generic plan·부하에서의 속도와 초기60×3 body25초 목표는
아직 재측정하지 않았다. 왕복 감소와 로컬 파티션 검산만 확인했으며 전체 latency 개선율은
주장하지 않는다. API 단독 이미지 계획은 기존 app-only 가정에서 이 read-only 저장소
변경을 포함하도록 다시 출처·소비 경계를 검토해야 한다. 새 운영 실행은 별도 절차다.
