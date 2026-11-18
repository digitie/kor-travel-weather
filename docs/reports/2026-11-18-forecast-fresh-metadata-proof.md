# 첫 예보 조건부 응답의 fresh metadata 중복 조회 축소

기준 main은4350798949a5649eb92ff6c382ef2f8650474eb3이다. 운영435의 조건부 forecast는
artifact0인데도45SQL/6~9초였다는 부모 QA를 출발점으로 삼았다. 운영 요청은 추가하지 않았다.

## 확인한 원인과 수정

로컬 실제 PostgreSQL의3위치/2grid fixture에서 같은 ETag의304는38문장이었다.
freeze16, 전체 artifact 존재 확인4, health11, 최종 reusable proof7이었다. 위치마다 단건을
조회하는 N+1은 아니며, freeze와 health가 inventory/generation/plan/head/owner/attempt/run/
cohort bulk 조회를 반복했다. 운영45문장의 차이를 이 소형 fixture로 확정하지 않는다.

첫 조건부 proof가 이번 RR freeze에서 이미 읽은 건강도 판정만 요청 안에서 재사용한다.
모든 승인 위치 행의 source_collected_at/data_revision_at과 기존 source 신선도·24시간 창·
승인 만료 규칙으로 다음 재검산 경계를 계산한다. scope 대표 시각으로 대체하지 않는다.
경계를 넘거나 시계가 역행하면 기존 full-health를 다시 읽는다. 발급 재시도마다 proof를
초기화하고 capacity/이전 snapshot fallback에서는 버려 이전 성공 판정을 이어 쓰지 않는다.

매 요청의 fresh 선택, head/owner epoch/native/cohort 실패 판정, 전체 refs와 artifact 존재,
최종 snapshot·삭제 epoch·사용 artifact 검산은 유지한다. 첫200/cursor의 기존 health 경로와
metadata/body TTL, 절대 target/snapshot cutoff, STALE, GC·불변성 검사는 바꾸지 않는다.
새 DB table/index/publisher/SDK/DTO/의존성은 없다.

## 같은 fixture의 짝 비교

435 원본 validate_cached_first_page 메서드와 후보를 같은 DB·client cache·고정 wall cutoff에서
7쌍 교대 실행했다. SQLAlchemy event의 SET/lock 포함 문장 수이며 server fetch 시간 전체를
따로 분리한 결과는 아니다.

| 로컬 측정 | 435 원본 경로 | 후보 |
| --- | ---: | ---: |
| inventory SELECT | 2 | 1 |
| 전체 SQL 문장 | 38 | 27 |
| HTTP304 wall 중앙값 | 50.27ms | 34.62ms |
| SQL hook 시간 중앙값 | 27.55ms | 18.43ms |
| 요청별 artifact read calls / payload SELECT | 0 / 0 | 0 / 0 |

ETag·같은 cached body·cursor가 같다. 운영45→34 SQL 또는 운영6~9초 개선은 아직 증명하지 않았다.
첫 forecast의128chunk/123MB·52초 및 모든 원천 STALE도 이 수정으로 해결됐다고 주장하지 않는다.

## 최종 검증과 다음 단계

최종 관련 묶음은 새19개와 기존45개, 합계64PASS/60.16초다. 처음 통합 실행의57PASS/1FAIL은
경계 시험의 새 snapshot UUID/JSON datetime 직렬화 비교 문제였고, 내부 page의 정확한 ETag
입력으로 수정한 뒤 최종 묶음을 한 번 다시 확인했다. 기존 assertion은 완화하지 않았다.

단일 fresh 선택, 같은 cutoff의 full-health 동등성, failed attempt/native/new cohort,
head/plan/generation/artifact 삭제, 신선도·승인 만료·시계 역행, 미래 source 시각 통과,
발급 재시도/fallback proof 초기화와 기존 slow-body/절대 cutoff/GC 관련 회귀를 검증했다.
전체 로컬 suite는 반복하지 않고 정확 후보 PR CI에 맡긴다. 실제 source/image 배포 후
지연·SQL·STALE와 payload0 수용은 부모가 별도 확인한다. 이 작업의 운영 호출·빌드·배포는0이다.
