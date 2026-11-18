# forecast projection cache의 순환 decode 비용 축소

기준 main은0ff1b15885c7c7ad9d8d01c04edf200559579299다. 운영 source435의 첫 forecast/
cursor128chunk·약123MB 읽기 비용을 줄일 수 있는지 로컬로 확인했다. 운영 호출은 추가하지 않았다.

## 원인과 수정

기존 DB cache는 engine별64MiB(67,108,864바이트)/8,192entry LRU다. projection8필드 요청도
전체 decoded point를 저장하고 원본 canonical byte 수로 eviction 무게를 셌다. raw payload
cache는 없고, cache hit도 DB SELECT 뒤 실제 SHA/byte/JSON line count를 재검산한다.

처음 수행한 메모리 SQLite 합성128chunk/123,450,880바이트 probe에서는 매 순회0hit/128miss였다.
끝에69entry/66,547,740바이트만 남고 다음 순회의 앞부분이 뒤쪽 entry를 밀어냈다. 같은
8필드 projection은31,303,808바이트였다. 이 probe는 종료했고 큰 payload를 파일·PG volume에
저장하지 않았다. 공간 제약 확인 후에는 아래 축소 fixture만 사용했다.

기존 LRU의 같은 byte/entry 예산에 engine scope+digest+fieldset별 projection을 저장한다.
전체 읽기는 별도 full entry를 쓰며 projection에서 누락 필드를 복원하지 않는다. 첫 decode는
미사용 tail까지 모든 JSON 행과 point 객체를 검증한다. cache hit도 실제 payload SELECT와
SHA/bytes/line count 검산을 유지한다. fieldset 순서·mutable 반환 격리, read guard와 GC 계약도
그대로다. projection의 ASCII-escape JSON과 key 길이를 무게로 세고 크기 계산 실패 시 보관을
생략한다. 이 무게는 기존과 같이 canonical/JSON 기준이며 Python heap/RSS 상한이 아니다.

## 작은 동등 fixture의 실제 결과

같은 publisher 형태의 point 구조를 유지하면서 집합과 cache 예산을 각각8분의1로 줄였다.
실제 DB store를 사용하는 메모리 SQLite에16chunk/18,400point/15,431,360바이트,
cache8MiB(8,388,608바이트)를 썼다. baseline은0ff 원본 decoder이며 같은 고정 artifact 집합과
manifest를 썼다. 아래 시간은 각1회 관측으로 통계적 중앙값이나 운영 지연이 아니다.

| 로컬 측정 | 0ff baseline | 후보 |
| --- | ---: | ---: |
| 첫 읽기 | 209.14ms | 132.92ms |
| 반복 읽기 | 144.21ms | 48.18ms |
| 반복 cache hit/miss | 0/16 | 16/0 |
| 보관 entry/무게 | 8 / 7,715,680B | 16 / 3,915,920B |
| 매 읽기 payload SELECT | 2 | 2 |
| 매 읽기 payload bytes | 15,431,360B | 15,431,360B |

응답 SHA와 cursor state는 같다. **CPU/decode 비용만 줄인 결과이며 SQL 수·payload bytes는
그대로다. cold123MB I/O 또는 운영52초/60초 timeout 해결은 증명하지 않았다.**
집합이 projection 예산도 넘거나 다른 읽기가 공간을 쓰면 다시 eviction될 수 있다.
전체 forecast snapshot이 cache에 들어간다고 주장하지 않는다. 새 TTL·예산 확대·DDL·producer/
publisher·SDK hook·startup prewarm은 없다.

## 검증과 다음 단계

cache 보관 회귀는0ff 원본에서 반복 decode16회 대 기대8회로 실패했고 후보에서 통과했다.
관련 묶음106PASS/58.31초 뒤 direct approved health UPDATE 회귀1PASS/2.02초를 별도 확인했다.
합계는 중복 없는107개이며, 이 묶음에 들어 있는 기존 metadata19개 등을 다시 더하지 않는다.
새 cache 회귀는12개다. SQLite의 CHECK 없는 warm payload tamper/line count/삭제 거절,
cold corrupt tail, fieldset/full fallback·복사 격리·공유 byte/entry/0 예산·동시성·engine 분리,
기존 GC·head·시간 cutoff와 실제 승인 health 변경 직후 STALE/새 ETag를 포함한다.

루트 Ruff·diff 검사를 통과했다. 운영 호출·빌드·배포 없이 정확 후보 PR CI로 전체 검증을
넘긴다. 실제 배포 뒤의 CPU/I/O·지연·STALE 수용은 부모가 별도로 확인한다. 기존0ff frozen
build/ops와 다른 앱/공통 패키지 작업은 수정하지 않았다.
