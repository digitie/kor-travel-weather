# 실사용 중심 Weather 지도·Map 연동·관리·수집 성능 개선안

작성일: 2026-11-09 KST. 상태: 문서 제안. 이 PR의 변경 파일은 이 문서 하나이며 제품 코드·DB·환경·수집·배포는 변경하지 않는다. 사용자 요청의 현재 단계는 개선안 문서 PR과 순차 세 차례 리뷰다. 구현은 아래 우선순위와 검증 기준을 갖춘 후속 제품 PR로 나눈다.

## 1. 결론과 적용 범위

가장 먼저 해결할 것은 **Map이 처음 받거나 상태 캐시가 만료됐을 때 export를 요청 경로에서 비싸게 다시 만드는 구조**다. 관리 지도는 다른 경로다. 그쪽은 작은 마커 투영·위치 목록 준비·상세 전송·화면별 대기를 각각 줄여야 한다. 수집은 외부 HTTP, 정규화, DB 저장, 파일 준비, 성공 공개의 시간을 분리하고 짧은 충돌 범위만 직렬화한다. 모든 지연을 DB lock으로 해석하거나 캐시 시간을 늘려 가리지 않는다.

선택하는 방향은 다음과 같다.

1. **P0: 실사용 계측과 Map export 최초 구성 제거.** 기존 성공 세대·일별 집계·페이지 캐시를 재사용하여 표준 소비 조건의 완료 페이지를 미리 구성한다. 상태 검증은 승인된 최대60초 안에서 별도로 갱신한다.
2. **P1: 지도용 작은 투영과 관리자 화면 분리.** 원천 fact 전체나 상세 DTO를 대표 마커에 반복 사용하지 않는다. 화면은 필요한 정보부터 표시하고 원문은 클릭한 페이지만 읽는다.
3. **P1: 수집의 단계별 예산·짧은 TX·공개 작업 분리.** 저장과 export 공개를 서로 실패 전파 없이 유지한다. 이미 있는 위치 공유 잠금·정렬 순서·PR209의 파일 단위 guard는 유지한다.
4. **조건부: 집계 테이블·공유 캐시·인덱스.** 실제 쿼리와 변경량에 맞는 것을 선택한다. 전체 원본 MV 갱신·무제한 캐시·timeout 확대·무조건 worker 증설은 채택하지 않는다.

최초 export1초, 모든 위치의 지도 응답1초, 운영 p95 달성은 아직 입증되지 않았다. 아래 목표는 구현 후 수용 기준 제안이지 현재 성능 보장이 아니다.

## 2. 어떤 코드와 운영을 조사했는가

| 구분 | 고정 기준 | 해석 |
| --- | --- | --- |
| 문서 PR의 base | Weather main `f337b6ff50e97cf7573b49404c70589b24ae3104` | PR209 포함. 문서만 추가한다. |
| 실제 API·수집기 제품 | `8723afcf54997d3c449edc7d0558ab36f092cb54` | 통합 Draft [PR208](https://github.com/digitie/kor-travel-weather/pull/208)에 있다. main과 같다고 쓰지 않는다. |
| API / 수집기 이미지 | `f3f7a4a8…` / `68c093c9…` | API12201·CS12206, PG18.6·schema0039_page_liveness. |
| 실제 웹 | `d59e7083c74908403d05f7aef2c1a4f3cef024cf` | 조사 제품8723과 frontend 트리의 Git diff가 없다. API 변경을 웹 재배포로 표현하지 않는다. |
| 상태 점검 후 관측 | 2026-11-09 09:31:56 KST | API·CS healthy, Workspace LOADED, 예약·센서27개 중26RUNNING/1STOPPED. 중지1개는 native_incarnation_recovery다. |
| Map 소비 코드 | main `9ed72f040f18ae37e463a798f63ceaf119a07e5f` | 로컬 작업 브랜치0213b02의 오래된 Weather 구현과 미커밋 변경은 조사 정본에서 제외했다. Map 운영 이미지·실효 설정·실행 여부는 이번에 확인하지 않았다. |
| 새 DB 관측 | 2026-11-09 10:57:50 KST | 실제 API810b4885…에서 읽기 전용 TX·SQL3초·lock500ms로 통계만 읽었다. 수집이나 DB 설정을 변경하지 않았다. |

운영 수용 원장과 과거 프로파일은 보존한다. 새로운 문서 작성 시점의 HTTP 부하 시험·파일 캐시 비우기·전체 fact 스캔은 하지 않았다. 과거 측정은 아래처럼 요청·행수·관측 시점·계측 유무를 나눠 사용한다.

## 3. 지금까지 확인한 실사용과 증거

| 사례 | 실제 흐름·관측 | 확인된 문제 / 아직 모르는 부분 |
| --- | --- | --- |
| U1. PC/모바일 첫 지도 진입 | 카탈로그를1000개씩 순차 수집하고 제공처 목록을 별도로 읽는다. 지도는 대표 지역/화면 안 지점으로 좁혀60개씩 동시2배치를 요청한다. [W:UI] [W:CLIENT] | 화면에 안 보이는 전체 위치 목록 준비가 선행한다. 최신 운영의 첫 마커·전체 파도 완료 시간은 별도 측정이 필요하다. |
| U2. 지도 이동·확대·필터 전환 | 같은 필터에서는 진행 batch를 이어받고 다음 배정만 현재 범위로 좁힌다. 필터/모드 변경은 AbortController로 취소한다. 저배율은 이미 시도·시군구 대표 지점만 고른다. [W:UI] | 대표값·viewport 제한·배치·취소는 이미 구현되어 있다. 새 기능처럼 제안하지 않는다. 현재 viewport에서 벗어난 진행 요청의 서버 잔여 비용과 재방문 비용을 측정한다. |
| U3. 위치 상세 열기·연속 선택 | 현재200행과 예보 최대1000행을 독립 요청한다. 예보는 현재 UTC 정시부터 읽으며 상한 `to`는 보내지 않는다. 위치 변경 때 이전 요청·표시를 격리한다. [W:UI] [W:CLIENT] | 현재/내일/시간별 화면에 필요한 구간보다 많은 예보·lineage 전송 가능성. 종류·제공처별 실제 건수/bytes가 미측정이다. |
| U4. Map 최초 전체 동기화 | W1 위치, W2 현재, W3 style별 예보, W5 일별 요약의 모든 cursor 페이지를 받은 뒤 Valkey 세대를 교체한다. [M:LOAD] [M:STORE] | 첫 페이지가 늦으면 소비 전체가 시작되지 않는다. 100행 실측을 Map 기본2000행의 실측으로 바꾸지 않는다. |
| U5. Map 반복 동기화 | 첫 페이지에 저장한 ETag를 보내고304면 기존 세대의 확인 시각을 갱신한다. W3는 `since` 증분이 아닌 전체 snapshot 소비다. [M:LOAD] | ETag/304가 이미 있다. 요청 기반 완료 캐시가 만료되면 데이터가 같아도 비싼 재구성을 거칠 수 있다. |
| U6. 관리 홈·수집 이력·원문 클릭 | 홈은 위치 개수 두 요청·최근 run10개·health를 Promise.all로 기다린 뒤 한 번에 표시한다. run 원문은 키 목록 이후 키마다 `get_source_record`를 호출한다. [W:HOME] [W:ADMIN] | 느린 한 요청/실패 하나가 홈 전체 표시를 지연시킬 수 있다. 원문 목록에는 행수에 비례한 왕복과 전체 payload 전송이 있다. 현재 병목 기여도는 계측 대상이다. |
| U7. 수집과 조회 동시 실행 | 과거4시간 publish 뒤 다른 KMA가2시간 이상 대기한 사례와 긴 export guard로 writer가5초 상한에 실패한 사례가 코드에 기록돼 있다. PR209는 파일 읽는 동안만 공유 guard를 유지한다. [W:INGEST] [W:FILES] | 과거 실패를 현재 재현으로 쓰지 않는다. 현재 CPU/I/O/DB pool/행 잠금/파일 guard/큐 대기를 따로 확인해야 한다. |
| U8. 중지·실패·이전 성공 데이터 | 이번 배포 때 공식 강제 종료4건의 최종 상태는3CANCELED/1FAILURE였고 이미 저장된499,968건을 보존했다. [W:DEPLOY] | 부분 commit을 terminal 성공으로 바꾸거나, 수집 중인 데이터를 성공 export로 승격해 빠르게 보이게 해서는 안 된다. |

### 3.1 실제 serving API와 별도 프로파일을 구분한다

제품8723, 실제 serving API의 단일 요청 표본이다. current는100개, daily는10개 항목이며 원천은 `stale`였다. current 항목 SHA는 `3085291e5fe2b8a6258d7ea6c11785e0979e26f153c8d7e50d6cc4350324724f`, 원천 시각은2026-11-08 18:03:26.448664 KST로 동일했다. 응답 생성 시각을 원천 시각으로 덮어쓰지 않았다. [W:DEPLOY]

| 요청 | 상태 | 전체 경과 |
| --- | --- | --- |
| current 최초 페이지 | 200, generated | 52.435초 |
| current 조건부 재사용 | 304, reused | 172.79ms |
| daily 최초 페이지 | 200, generated | 13.114초 |
| daily 조건부 재사용 | 304, reused | 99.70ms |
| 최대60초 상태 만료 뒤 current | 200, generated | 23.840초 |

별도 API 프로세스의 계측 없는 current29.262초/daily6.222초는 serving 수치와 다르다. 구제품 프로파일 current49.404초에서 SQL42회16.274초·CPU17.527초를 관측했지만 계측 비용을 포함하며 합산 가능한 단계별 wall 분해가 아니다. 후보 프로파일 current97.897초503도 보존한다. 부하·세대·재사용 상태가 다른 단일 표본으로 코드 단독 개선율·p95·OS cold 성능을 계산하지 않는다.

### 3.2 운영 DB의 이번 짧은 관측

- 관측 TX를 제외한 blocked session0, active I/O 대기1, idle8이었다. 이는 해당 순간의 관측이며 지속적인 잠금 경합이 없다는 증거가 아니다.
- `pg_stat_statements`가 없어 쿼리별 운영 p95·누적 상위 쿼리를 이 자료로 판정할 수 없다.
- `weather_current_values`의 통계 추정치는 live 약876만·dead 약137만이다. `weather_sync_runs`의 누적 update 약176만, DB 누적 temp_bytes 약40.0GB를 보았다. 모두 통계값이며 정확한 row count·현재 bloat 크기·이번 요청 spill·현재 쓰기 속도가 아니다.
- table/partition별 통계가 서로 다른 범위를 가질 수 있다. 부모의 n_live_tup를 전체 위치/원본 개수로 쓰지 않는다. 누적 통계만으로 인덱스 삭제·VACUUM FULL·prune를 실행하지 않는다.

## 4. 비용 구조와 이미 반영된 개선

### 4.1 Map export 최초 구성은 아직 요청의 책임이다

현재 요청은 인증 → 서비스 limiter → 상태/선택 확인 → roster·scope/head·ref·manifest 및 snapshot pin → 페이지 구성 → DTO 검증 → ETag 비교 → 직렬화/gzip으로 이어진다. ETag가 같아도 `read()`와 DTO 검증 뒤에 비교한다. 같은 키의 완료 페이지는 재사용하지만 만료·프로세스 재시작·서로 다른 query에서는 최초 비용이 발생한다. [W:HTTP] [W:SNAPSHOT] [W:CACHE]

이미 있는 것: 선택 캐시의 최대60초·16MiB 상한, 페이지 캐시의 총64MiB·단건8MiB 상한, 같은 프로세스/같은 키의 Event 기반 단일 생성, metadata 선택 투영0038, page liveness0039, 고정 batch 조회, 짧은 원자적 pin commit. 이것을 미구현으로 쓰거나 같은 것을 새 레이어로 중복 구현하지 않는다.

남은 후보 비용: 다수 scope 해석·전체 선택 manifest·불변 파일 읽기/해석·페이지 DTO 재검증·gzip, 캐시 만료 때의 재구성이다. SQL 한 문장5초가 전체 요청5초라는 뜻은 아니다. 페이지 cache의 대기 상한60초도 별도 예산이다. 우선 각 단계의 배타 시간을 측정한다.

### 4.2 지도의 기본 조회와 Map export는 분리되어 있다

지도는 `/v1/weather/markers`·위치 latest/forecast와 위치 목록을 사용한다. 마커는 작은 metric allowlist·projection/정확한 fact 키를 쓰고 API limiter2·500위치 상한을 갖는다. UI가 여섯 종류를 명시하면 저장소는 현재/미래예보/특보 세 종류 그룹을 따로 조회한다. 이 경로와 kinds 미지정 경로의 SQL 계획이 다르므로 둘 다 측정한다. [W:MARKER]

상세는 현재와 예보를 이미 독립 표시하며 인증도 별도 worker limiter를 쓴다. 따라서 Promise.all 제거·인증 격리·단순 마커 배치 도입은 상세의 신규 해결책이 아니다. 홈의 Promise.all 장벽은 별도로 남아 있다.

### 4.3 이미 있는 집계를 먼저 사용한다

`weather_current_values`는 이미 논리 point 최신값의 투영이다. `weather_daily_summaries`, `weather_daily_summary_coverage`, `weather_daily_summary_progress`도 이미 있고 성공 export head를 받아 증분 갱신한다. W5를 빠르게 하겠다고 같은 일별 통계를 별도 이름으로 다시 만들거나 매 요청 원본 전량 GROUP BY하지 않는다. [W:DAILY]

## 5. 실사용별 변경 설계

### P0-A. Map이 사용하는 완료 페이지를 공개 단계에서 준비

**U4/U5를 해결한다.** 선택된 성공 head vector·catalog revision·정책 revision에 대응하는 불변 manifest와 표준 페이지를 publisher 후속의 별도 유한 작업으로 만든다. 기존 receipt·head·daily summary를 입력으로 하며 provider 재호출·전체 raw 이력 재스캔은 하지 않는다. 원본 ingest의 성공과 페이지 준비 성공은 분리한다.

- 처음에는 실제 Map 조건을 확인한 W2/W5 표준 프로필만 지원한다. Map 코드 기본값은 page_size2000·daily14일이지만 실제 운영 값이라고 단정하지 않는다. W1·W3 및 임의 limit/필터는 기존 경로로 호환한다.
- 키: 리소스·style·정렬된 제공처/종류/범위·선택 vector·catalog revision·정책 revision·페이지 limit·cursor 위치. `Accept-Encoding`은 wire 표현 키에 포함한다. 데이터 페이지와 상태 envelope를 분리한다.
- 새 페이지는 검증·hash·행수·완결성을 확인하고 기존 head/manifest와 CAS로 공개한다. 늦게 끝난 builder가 새 세대를 덮지 못하게 한다. 실패하면 이전 검증된 payload를 보존하고 실패/신선도 metadata로 나타낸다.
- 상태는 데이터가 안 바뀌어도 마지막 실패·다음 예정·정책 변경을 반영해야 한다. 승인 최대60초는 **최신 상태 반영 지연**의 상한이며 데이터 나이를 정상으로 바꾸는 허용치가 아니다. 만료된 상태로200/304를 내지 않는다. stale 원천은 stale로 표시하고 초도 실패/유실은 기존503 계약을 유지한다.
- 요청은 인증 후 작은 상태 proof와 페이지 존재/무결성을 검산한다. 같은 상태·표현의 ETag일 때만304. `generated_at`은 응답 시각, `source_collected_at`·`as_of`·snapshot 시각은 원래 의미를 유지한다.
- DB metadata 구성에 파일 guard를 장시간 잡지 않는다. 새 pin은 현재와 같이 최종 파일 존재 확인·ref 삽입·commit을 같은 짧은 guard로 묶는다. 파일 검산의 생략·mtime만 신뢰·GC 참조 생략으로 속도를 만들지 않는다.
- 기존 캐시로 직접 재사용 가능한지 먼저 비교한다. 새 표가 필요하면 `weather_export_materializations`(제안명)에는 작은 manifest 위치·checksum·선택 vector·완료 상태만 저장한다. 큰 wire body는 NVMe 불변 파일에 둔다. 파일/DB 순서·orphan·누락·재시작 복구는 기존 artifact 계약을 따른다.
- 무한 prewarm 대신 실제 소비 프로필만 준비한다. builder는 namespace별 중복을 합치고 단일 작업의 CPU/RSS/입출력·전체 deadline을 제한한다. 미준비 시 동기 전체 재구성을 폭증시키지 않고 기존 bounded fallback 또는 명시적 일시503을 사용한다.

**완료 기준:** 같은 실제 Map 프로필의 첫 페이지뿐 아니라 모든 cursor 페이지·완료까지의 wall/bytes/CPU와 stale/실패·세대 교체 중 결과를 비교한다. 우선 목표 첫 페이지 p95≤1초·304 p95≤300ms, 문장 SQL≤기존5초. 아직 보장된 수치가 아니다.

### P1-B. 지도 마커용 작은 투영과 재방문 재사용

**U1/U2를 해결한다.** 우선 대표 지점·60개 배치의 실제 반환 metric·payload bytes·SQL/fact fetch·DTO 비용을 측정한다. `weather_current_values`를 그대로 빠르게 읽는 Core projection이 목표를 만족하면 새 표는 만들지 않는다.

부족할 때 `weather_marker_projection`(제안명)을 검토한다. 위치·provider·kind·metric·대상 구간·정책 revision별 필요한 scalar, 원 fact 복합 키/lineage·기준 시각·유효기간만 둔다. 전체 raw payload나 수천 개 예보를 복제하지 않는다. 기존 observed/nowcast 우선·미래예보 분리·선택 제공처·종류별 예산·특보 위치 일치와 유효기간을 보존한다.

public marker/latest의 현재 투영과 service export의 **terminal 성공 전용** 선택은 다르다. 하나의 새 head로 두 경로의 의미를 조용히 통일하지 않는다. 마커는 기존 public winner와 동등해야 하며 export는 실패 run의 부분 commit을 포함하지 않는다. success만 사용하도록 public 의미를 바꾸려면 별도 계약 변경으로 다룬다.

- projection은 원 canonical 값 변경과 연결된 durable dirty 표식/영수증으로 갱신한다. 같은 위치·범위의 변경을 합치되 누락·중복·순서 역전을 복구할 수 있어야 한다. 새 표의 변경 실패가 원본을 잃게 만들면 안 된다.
- 지역 대표값은 현재의 대표 지점 규칙을 사용한다. 평균 기온으로 바꾸지 않는다. 위치/제공처/종류·catalog 변경으로 대표 지점이 바뀌는 것도 cache key에 반영한다.
- 새 request cache를 무기한 쓰지 않는다. 우선 같은 화면 세션의 bounded 재방문 재사용을 적용하고 refresh·filter·선택 변경을 검산한다. public 마커/특보의 새 지연 허용을 export60초 승인으로 간주하지 않는다. 특보 만료·다음 예보 시각을 넘겨 이전 상태를 보여주지 않는다.
- 현재60×2·500위치 상한을 곧바로 키우지 않는다. 대량 상세 DTO를 보내지 않는 compact marker opt-in endpoint를 먼저 비교한다. 기존 응답 필드를 조용히 지우지 않는다.

### P1-C. 카탈로그와 선택 상세의 필요한 구간만 먼저 제공

**U1/U3를 해결한다.** 목록1000개씩 직렬 수집과 매 페이지 count·지역 투영, 제공처/종류 필터 변경의 전체 roster 재조회 비용을 분리한다. 안정된 catalog·활성 source/kind revision을 갖는 versioned 목록 또는 viewport/대표 지역 전용 가산 endpoint를 검토한다. 카탈로그 수정·disable·source coverage 변경을 revision에 반영하고 검색 전체 결과와 화면 안 결과를 혼동하지 않는다.

상세는 `현재 → 내일 → 필요한 시간별 구간`의 표시 우선순위를 유지한다. 작은 preview용 endpoint 또는 명시 `from/to`를 추가해 첫24~48시간을 먼저 읽고 더 보기는 기존 예보/history 계약으로 이어간다. 1000행을 상시 가져와 브라우저에서 버리는 비용을 줄인다. 단, 예보 발표판·KST 일 경계·시각 정렬·provider/kind 필터·결측/미제공을 보존하며 중기 예보를 임의로 잘라 없애지 않는다.

기존 응답 필드 축소·유효하지 않은 위치의 대체값·뇌우로 오인하는 특보 혼입·없는데도 보이는 특보 설명을 성능 최적화로 되살리지 않는다. 실패한 패널만 오류를 표시하고 늦은 과거 선택 응답이 새 선택을 덮지 않아야 한다.

### P1-D. 관리 첫 화면·원문·수집 상태 조회

**U6를 해결한다.** 홈의 네 요청은 이미 병렬이다. 더 병렬화하는 것이 아니라 각 카드의 loading/error/완료를 분리하여 health·최근 run부터 그린다. 취소·중복 새로고침·마지막 요청 승자도 구현한다. Dagster 관리 화면의 기존 overview/detail 취소 처리는 보존한다.

- run 원문 목록의 키당 source 재조회는 묶음 조회로 바꾸고, 메타 목록/페이지와 원문 본문을 구분하는 가산 `view/limit/cursor` 계약을 검토한다. 현재 route를 말없이 일부만 반환하도록 바꾸지 않는다. 특정 원문 클릭 때만 bounded 본문을 읽고 기존 redaction·권한을 유지한다.
- `weather_admin_summary`(제안명)는 실제 화면에 반복 필요한 catalog 개수·provider별 마지막 성공/실패·처리량·지연 집계만 담는다. 실패·시작·종료 상태는 즉시 갱신/무효화하거나 authoritative run 조회로 보완한다. export의60초 허용을 관리 조작/상태에 자동 적용하지 않는다.
- 전체 fact COUNT/큰 payload 정렬을 화면마다 하지 않는다. count 정확성·활성 필터·source/kind 정의를 고정하고 미확인 값은 미확인으로 표시한다. 실제 집계가 싸면 새 요약 표 없이 묶음 endpoint만 쓴다.

### P1-E. 수집 처리량과 DB 쓰기 충돌을 함께 줄이기

**U7/U8을 해결한다.** `대기열 → provider HTTP → 파싱·정규화 → chunk TX → 파일 fsync → terminal → publisher → Map 수용`의 시간을 run·scope별로 분리한다. wall 전체를 provider 속도 또는 lock 하나로 부르지 않는다.

이미 구현된 사항: 원천/값의 chunk 원자 저장, replay/alias 정규화, SyncRun 행의 terminal 차단, 위치별 정렬된 shared advisory·KEY SHARE, 파일 발행은 DB commit 뒤, publisher의 짧은 재시도·skip_locked, current/forecast job 분리다. [W:INGEST] [W:PUBLISHER]

개선 후보는 다음 순서다.

1. 순수한 파싱·DTO·문자열/hash 중복 계산은 TX 전 단계에서 줄인다. DB canonical alias/재관측 증명·fact identity·owner epoch 확인은 TX 경계를 넘겨 추측하지 않는다.
2. chunk별 rows·bytes·TX 경과·대기/재시도를 계측하고 `run/source link·기존 fact 조회·current upsert`의 반복 왕복을 묶는다. canonical source key와 zero-new-row 재관측 영수증을 보존한다. COPY는 검증된 staging 적재 후보이며 current/receipt의 조건부 upsert를 대체하는 무조건 bulk insert가 아니다.
3. run heartbeat·진척을 fact마다 hot row update하지 않게 같은 chunk 기준으로 합친다. heartbeat 지연으로 정상 작업을 stalled로 오판하지 않도록 기존 lease·복구 주기보다 짧게 보장한다. 이번 누적 update만으로 현재 heartbeat가 병목이라고 확정하지 않는다.
4. 동일 위치라도 같은 논리 point를 쓰는 writer끼리는 CAS/unique 의미가 필요하다. global lock 제거라는 명분으로 run terminal/위치 삭제/retention 보호를 지우지 않는다. 새로운 요약·캐시용 global counter를 fact마다 갱신하지 않는다.
5. provider별 HTTP 제한·일일 quota·발표 회차·중복 run 방지를 유지한다. global run 수·job 내 병렬도·provider 동시 요청 수는 별개다. 코드의 job executor max_concurrent1이나 예제 deploy yaml 값을 실제 공용 daemon 전체 한도로 단정하지 않는다. 올바른 처리량은 같은 quota·입력 범위의 terminal 성공과 공개까지로 잰다.
6. dispatcher/dirty/outbox를 추가하면 같은 namespace의 여러 변경을 합치고 재시도·재시작을 멱등하게 한다. reader가 준비 worker를 무한 제출하거나 raw ingest가 API를 동기 호출하지 않는다. 원본 저장 완료와 export 실패를 별도로 기록한다.

## 6. 잠금·캐시·DB 구조 선택

### 6.1 서로 다른 대기를 분리한다

| 자원 | 현재 의미 | 보존/개선 방향 |
| --- | --- | --- |
| API limiter·AnyIO worker | marker2·export2·auth 별도2. CPU/DB 입장 제어 | API 프로세스별 한도다. 인증 limiter 분리가 별도 DB pool 보장을 뜻하지 않는다. worker 증설 전에 pool checkout·event-loop 지연·대기 취소를 측정한다. |
| DB pool | 저장소 생성당 size10·overflow10·timeout15초 | 프로세스/worker 수만큼 늘 수 있다. 읽기/쓰기 pool 분리 후보는 전체 connection 예산 안에서만 검증한다. pool 확대를 기본 해결책으로 삼지 않는다. |
| 일반 SELECT의 MVCC | 보통 INSERT/UPDATE와 행 잠금으로 서로 막는 경로가 아님 | 느린 SELECT를 곧바로 writer block으로 부르지 않는다. DDL·행/CAS·advisory·I/O·pool 대기를 구분한다. [PG:LOCK] |
| run 행·location advisory/KEY SHARE | terminal 이후 저장 금지·catalog 변경/retention과 정합성 | 고정 순서·짧은 chunk. 단일 logical writer 불변조건을 먼저 입증한 뒤 세분화한다. |
| 파일 guard/GC | PR209 파일 한 개의 공유 읽기와 짧은 pin commit, writer turnstile | 두 OS reader slot·동일 FD 검산·짧은 writer 재시도 유지. lock 파일 unlink/우회·강제 해제 금지. [W:FILES] |
| cache lock·inflight Event | 같은 프로세스/같은 키의 중복 구성 합치기 | 다른 프로세스에는 별도 캐시다. metadata lock 밖에서 비싼 load/검산. 예외·취소·owner 종료 뒤 대기자 해제와 메모리 상한 검증. |
| 성공 공개 clock | 기존 한 행의 작은 metadata 직렬화 | 새 global per-fact counter를 더하지 않는다. scope별 outbox와 기존 전역 순서의 정합성은 별도 검증하며 clock 삭제는 하지 않는다. [W:HEAD] |

### 6.2 DB와 캐시 후보의 선택 기준

| 후보 | 실제로 줄이는 일 | 결정·조건 |
| --- | --- | --- |
| 기존 일별 집계 확장 | W5 coverage·summary 반복 해석 | 우선 사용. 이미 있는 summary/progress의 변경분·바뀐 날짜만 갱신. 집계 없음/미완을 정상0으로 만들지 않는다. |
| 작은 marker read model | U1/U2의 큰 current/fact 조회·hydration | P1 비교 실험. provider/kind 조합과 현재/예보/특보 winner 동등성 필수. |
| immutable 완료 페이지+작은 상태 head | U4/U5의 최초 freeze·해석·전체 manifest 반복 | P0 우선. 기존 snapshot/refs/liveness를 재사용하고 새 표는 입증된 누락만 채운다. |
| 관리 통계 테이블 | 홈 반복 count·대시보드 요약 | 동등한 묶음 조회보다 비쌀 때만. 상태/실패 반영과 집계 watermark를 구분한다. |
| request 내 memo / 검증된 bounded LRU | 같은 객체의 중복 해석·검증·압축 | 이미 적용한 것과 중복하지 않는다. bytes·TTL·세대·삭제/정책 무효화·다중 worker를 검증한다. |
| Valkey/Redis 공용 캐시 | API worker 간 중복 구성 | Weather에 새 의존성·장애면을 추가하므로 로컬 immutable 페이지로 부족할 때만. Map은 이미 Valkey 세대 저장소를 사용한다. Map 캐시를 더해 Weather 최초 구성 시간을 없앴다고 쓰지 않는다. |
| 일반/CONCURRENT MV | 관리자용 낮은 빈도 통계 | 전체 갱신 비용이 작고 최신성 요구가 맞을 때만. CONCURRENTLY도 전체 쿼리를 실행하고 적합한 UNIQUE index·기존 populated 상태가 필요하며 같은 MV 동시 refresh는1개다. 요청 중 refresh는 금지. [PG:MV] |
| 복합/부분/covering index·확장 통계 | 실제 WHERE/ORDER/JOIN의 잘못된 계획 | 실제 kinds 명시/미지정·대표/전체·generic/custom 계획과 BUFFERS로 선택한다. 기존 index·0038 투영부터 확인. live/dead 추정만으로 삭제하지 않는다. [PG:EXPLAIN] |
| partition·Timescale·별도 읽기 DB | 큰 history 정비/자원 격리 | 현재 최초 export는 history 전량 GROUP BY 문제가 아님. 별도 근거 전 보류. 복제 지연·성공/GC proof가 다른 DB에서 섞이지 않아야 한다. |

새 serving 표는 typed 키·TIMESTAMPTZ·PK/unique·필요 FK index·필터에 맞는 소수 index를 사용한다. JSON은 보조 metadata로 한정하며 큰 원문 복제로 I/O를 키우지 않는다. 데이터+선택/정책 revision을 함께 공개하고 시간 만료 특보·예보는 wall clock만 바뀌어도 재평가한다. 불변 본문을 재사용할 수 있어도 상태 확인을 영구 생략하지 않는다.

## 7. 같은 사용 사례로 전후 비교하는 수용 계획

운영 쪽은 원본·설정·키·수집·실행을 유지한 읽기 전용 관측부터 한다. 경합/삭제/파일 손상 주입은 WSL 및 Docker PG18의 격리 환경에서 수행한다. docs-only PR에 제품 전수 시험·수집 재실행·배포를 추가하지 않는다.

| 사용 사례 | 입력 고정 | 기록·동등성 | 제안 수용 기준 |
| --- | --- | --- | --- |
| U1 첫 화면 | 실제 PC1440/모바일375·동일 viewport/필터·대표 지점 | 위치 목록/첫 유효 마커/현재 viewport 완료/타일을 각각 측정. 요청수·행수·bytes·JS long task | 유효 대표 날씨 표시 p95≤2초 제안. 타일/DB/source 상태가 같은 비교가 필요. |
| U2 이동·확대 | 같은 위치 이동 경로·저배율/시군구/고배율·종류/제공처 전환 | 이전 응답 역전0·불필요 요청/DB 시작수·취소 후 잔여 시간 | 화면 밖 추가 파도0, 같은 revision 재방문 중복 구성0. 특보/대표값/접근성 보존. |
| U3 상세 | 실제 확인했던 감일·강릉·가평과 실제 마커 선택 | 현재·내일·시간별을 별도 측정; DTO·원천시각·발표판/결측·필터 비교 | 현재 먼저 표시, 나머지 실패 격리. 첫 핵심 정보 p95≤2초 제안, 더 보기 완결성 유지. |
| U4 최초 동기화 | 실제 Map 실행 설정·모든 endpoint/style·실제 page_size/days | 첫 페이지와 전체 페이지 순회, 최종 row hash/count·순서·snapshot vector·Valkey swap까지 | 표준 첫 페이지 p95≤1초 제안, 전체 wall은 동일 데이터량/페이지수로 개선. |
| U5 반복/만료/재시작 | 같은 ETag와 상태 변경 없음/실패/시각 만료·API 재시작 | 동일 결과는304; 상태가 바뀌면 metadata 반영. 만료된 proof로200/304 없음 |304 p95≤300ms 제안. 상태 확인 최대60초·기존 파일/DB 삭제 검산 유지. |
| U6 관리 화면 | 홈4요청·최근10run·대량 원문을 가진 기존 run | 카드별 first paint·쿼리수·원문 선택 페이지/bytes·권한/마스킹 | 빠른 카드가 느린 카드에 막히지 않음. 원문 목록 SQL이 행수 비례 왕복하지 않음. |
| U7 동시 처리 | marker/상세/export·수집·publisher·GC의 같은 혼합 비율 | queue/worker/pool/SQL/row/advisory/file guard의 대기·보유 시간을 구분; CPU/RSS/I/O/WAL | writer starvation·deadlock·누락0. 기존 timeout/예산을 늘리지 않은 처리량 개선. |
| U8 실패·복구 | 부분 commit·cancel/epoch loss·파일 유실·늦은 builder·source empty | 마지막 성공/원본 보존·멱등 replay·generation/cursor 경계 | 실패를 성공으로 위장하지 않음, partial을 완결 집합으로 공개하지 않음. |

계측 이름은 낮은 cardinality의 endpoint/단계/결과만 라벨로 쓴다. 좌표·location ID·run ID를 무제한 Prometheus 라벨로 넣지 않으며 토큰·URL query·SQL/바인드·원문 payload를 로그에 기록하지 않는다. 단일 request correlation은 비공개 제한된 로그에서만 한다.

반복 측정 전에는 표본 수·동시성·예열 정의·같은 데이터 세대·수집 부하·실효 CPU/메모리/pool 설정을 원장에 고정한다. 향후 p95는 각 핵심 조건에서 최소100개 완료 요청과 별도 오류율·timeout을 함께 기록하고, 처리 못한 요청을 제외해 빠르게 보이지 않게 한다. 표본100개가 전국/장기간 보장은 아니다. SQL/CPU/frame 누적시간을 서로 더해 wall 원인으로 주장하지 않는다.

## 8. 실행 순서와 범위

| 순서 | 후속 제품 변경 범위 | 종료 조건 |
| --- | --- | --- |
| 0 | 위 사용 사례의 단계 계측·실제 Map 설정/실행 여부 확인 | 운영 HTTP/브라우저 trace·DB wait·입력 vector의 연결. 미측정 admin/수집 비용을 확정 원인과 분리. |
| 1 | W2/W5 완료 페이지의 사전 구성·상태 head 재검증 | cold/만료/재시작/동시 writer·GC 비교와 Map 전체 순회 수용. 실패 시 이전 경로 보존. |
| 2 | 지도 compact 투영·카탈로그/상세 preview·관리 홈/원문 | PC/모바일 실사용 회귀와 응답/선택·권한·누락 검산. 필요 없는 표/캐시 후보 폐기. |
| 3 | 확인된 수집 SQL/TX·heartbeat/공개 큐 비용 | 같은 provider quota·범위·예산에서 terminal 성공→공개까지의 처리량 개선. |
| 4 | 위 결과에도 남는 index/자원 격리/공유 캐시 | EXPLAIN·총 메모리/연결/WAL/운영 복구 비용이 개선을 입증할 때만 선택. |

구현은 작은 feature PR로 나누고 실제 새 이미지 검증까지 범위를 명시한다. 문서가 머지돼도 제안된 새 endpoint·테이블·cache policy가 이미 구현·배포됐다는 뜻은 아니다. 이 문서 PR에서는 source 수정·migration·새 수집·운영 취소·키 회전·백업/복원·prune를 하지 않는다. 후속 배포는 최신 실제 CID·Common 격리 live health·원 예약 상태를 읽어 보존한다. 과거 global G/R 상태나 old CID를 그대로 재사용하지 않는다.

## 9. 증거 보존과 세 차례 리뷰

Git 밖의 원장은 비밀·원문 query를 싣지 않고 다음 digest로 식별한다. 이 문서는 원장 내용을 요약해 독자가 파일을 받지 않아도 수치의 범위와 한계를 판단할 수 있게 한다.

| 원장 | SHA256 | 의미 |
| --- | --- | --- |
| weather-cold-read-20261109/http-after-result.json | `123f4e9515252fe293641a475515aa7316157727ed443b5c3784014828b41a25` | 실제 serving 단일 HTTP 표본·행수/상태/시각·소유 helper 종료0 |
| weather-cold-read-20261109/baseline-profile.json | `d7085ffced331283ffe1207ea7c5ff73a17748cc3af1346f96539e321d801281` | 구제품 별도 프로세스 계측 표본 |
| weather-cold-read-20261109/candidate-profile-result.json | `e3c14e1dfddba40b52c289e44bc5de7ce9d008757f02449613c4faebdf8ee690` | 후보97.897초503를 포함한 원 실패 |
| weather-cold-read-20261109/final-acceptance.json | `5596c9bc70fa11f02977c32e663224c2df7371baea07a1c00ce075c5c84afefe` | 새 API/CS·원27복원·최초 부모실제0·499,968건 보존 |
| weather-cold-read-20261109/manager-health-followup-readonly.json | `01db5e5cedab590668bc940f3da6e23fa7af5c49b3aa71a448f7f79d2005cfff` | 후속 상태 점검 변경 뒤 실제 상태 일치 |
| weather-performance-plan-20261109/readonly-db-observation.json | `ab85d27afa80d309da787121868163138548118b499b705a6a1d7cc9164c44e5` | 이번 읽기 전용 누적 통계·한 순간의 wait 관측, helper0 |

| 리뷰 | 초점 | 상태 |
| --- | --- | --- |
| 1차 | 실사용→원인→변경→검증의 연결, 현행/제안·main/운영·실측/추론 구분 | 대기 |
| 2차 | 원본/신선도/부분 실패·잠금/GC/재시작·Map 계약의 모순 | 대기 |
| 3차 | 반영 후 전체 재검토, 링크·수치·소스·우선순위·남은 미확인 | 대기 |

리뷰에서 발견한 내용·보완 위치·잔여 제약은 이 표 아래에 기록한다. 검토 전에 완료/PASS를 만들지 않는다.

## 10. 고정 소스와 기술 근거

아래 Weather 링크는 별도 표기가 없으면 운영 제품8723, Map 링크는 조사 main9ed72 기준이다. 본문에서 언급한 함수와 경계를 해당 소스에서 대조할 수 있다.

[W:UI]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/packages/kor-travel-weather-admin/frontend/components/weather-map.tsx
[W:CLIENT]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/packages/kor-travel-weather-admin/frontend/lib/api.ts
[W:HOME]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/packages/kor-travel-weather-admin/frontend/app/page.tsx
[W:ADMIN]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/packages/kor-travel-weather-api/src/kortravelweather_api/routers/weather.py
[W:MARKER]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/repository.py#L3124
[W:HTTP]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/packages/kor-travel-weather-api/src/kortravelweather_api/routers/service_exports.py#L296
[W:SNAPSHOT]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/export_snapshots.py#L493
[W:CACHE]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/export_page_cache.py
[W:INGEST]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/repository.py#L1995
[W:FILES]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/export_artifacts.py#L278
[W:DAILY]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/export_daily_store.py#L28
[W:HEAD]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/export_heads.py#L1036
[W:PUBLISHER]: https://github.com/digitie/kor-travel-weather/blob/8723afcf54997d3c449edc7d0558ab36f092cb54/src/kortravelweather/export_collector.py
[W:DEPLOY]: https://github.com/digitie/kor-travel-weather/blob/04edc424af5f681051a1b451e4ca93d9e49fa3aa/docs/reports/2026-11-09-cold-export-read.md
[M:LOAD]: https://github.com/digitie/kor-travel-map/blob/9ed72f040f18ae37e463a798f63ceaf119a07e5f/packages/kor-travel-map-dagster/src/kortravelmap/dagster/weather_state.py#L464
[M:STORE]: https://github.com/digitie/kor-travel-map/blob/9ed72f040f18ae37e463a798f63ceaf119a07e5f/src/kortravelmap/infra/valkey_state.py#L829
[PG:LOCK]: https://www.postgresql.org/docs/18/explicit-locking.html
[PG:MV]: https://www.postgresql.org/docs/18/sql-refreshmaterializedview.html
[PG:EXPLAIN]: https://www.postgresql.org/docs/18/using-explain.html
