# Map ADR-114 Weather 서비스 API 인계

## 구현 PR과 활성화 상태

- W1 위치 참조: #145·#150·#155. canonical catalog를 독립 frozen roster로 제공한다.
- W2 실제 승인 현재 관측: #143·#144·#146·#150·#154·#155·#159.
- W3 예보: #147·#154·#156·#157·#158·#159와 별도 중기 reader #160.
- W4 작은 수집·예정 상태: #149·#151.
- W5 사전 계산 세 구간 요약: #148·#152·#163.
- 실행/공유 artifact 설정 #153, bounded publisher #152, 제한된 동시 읽기 #161,
  reader 수명 #162, 승인 source vector/메타데이터 상한 분리 #164.

이 문서는 구현 계약과 시험 응답을 전달한다. 운영 배포·flag 활성화, 실제 Map
담당자의 수령·소비자 구현 수용, 운영 비용·GC gate 통과를 뜻하지 않는다. 초기 활성화
전에는 아래 준비 조건과 각 PR의 최종 리뷰/CI/실제 HTTP·UI 수용을 확인한다.

격리 PostgreSQL0001→0033/실제 uvicorn HTTP에서 받은 시험 응답 원문은
[current](../examples/map-service/current-response-example.json),
[daily-summaries](../examples/map-service/daily-response-example.json)에 있다.
제품 소스10b691566fd20f43f04ecc1da9261f58d65f2725로 실행했으며 원천 HTTP만
fixture다. 인증 토큰은 포함하지 않는다. 실제 도메인의 운영 데이터 예시는 아니다.
두 번째 파일은 limit2 첫 페이지라 next_cursor가 있고 구간에 관측이 없으면 null이다.

## REST 소비 계약

모든 경로는 `/v1/service/exports/` 아래이고 기존 서비스 인증 헤더
`X-Admin-Token`을 그대로 사용한다. `locations`, `current`, `forecasts`, `status`,
`daily-summaries`에 generated_at/source_collected_at/source_status/item_count가 있다.
Weather 인증 실패는 기존401이며 Transport service token/peer의404 정책과 다르다.
gzip과 약한 ETag/If-None-Match304를 지원한다. 큰 집합은 `meta.has_more`와
`meta.next_cursor`를 따라가며 limit 최대5,000과 페이지4MiB 상한을 함께 적용한다.
`item_count`는 페이지 항목 수다. W4 `status`는 최대128개 상태를 한 응답에 담고
페이지 query를 받지 않는다. Transport T2/T3의 최상위 cursor와 혼동하지 않는다.

서명 cursor의 TTL은15분이다. cursor 도중 필터·limit을 바꾸면422, 만료410이면
첫 페이지에서 새 snapshot을 시작한다. TTL 뒤 같은 의미의 데이터라도 새 handle을
발급하며 이전 cursor를 가진 클라이언트에 잘못된304를 주지 않는다. 원천 실패는
이전 성공 집합을 유지한 stale 응답이며, 최초 필수 승인 미준비는503이다.

W1 `locations`의 실제 catalog 원천 수집 clock은 현재 확인할 수 없어
`source_collected_at=null`, `source_status=stale`다. canonical reference 자체를
읽을 수 있다는 사실과 원천 수집 신선도를 구분한다. updated_at/응답 시각을
수집 시각으로 꾸미지 않는다. Map은 이 정책을 고려해 초기 참조 roster를 받아야 한다.

W3는 미래 target_at만 location×provider의 한 항목으로 묶고, 정렬된 target_at에
metric 배열을 null 정렬한다. SKY 서술 문자열을 숫자로 바꾸지 않는다. 중기는 같은
issued revision의 land/temperature 승인 head 둘이 있어야 완전 묶음이며 불일치·미확인
원천은 이전 완전 vector stale/이전 집합 없으면503이다.

W3 `known_at`은 원본 fact의 원천 시각이다. 동일 의미의 재수집·재승격에서도
원본 값을 보존한다. `published_at` 및 `meta.next_since`는 **단조 공개 watermark**로
별개다. 클라이언트는 모든 페이지를 끝까지 받은 뒤 마지막 next_since를 저장해
다음 since로 보낸다. known_at 최댓값을 다음 커서로 추측하면 안 된다. 빈 델타에도
검증된 집합과 watermark가 있으며 API/DB 시계 보정 후에도 발급 watermark를 재사용한다.

W5는 observed와 KMA 초단기실황(nowcast)을 포함한다. 기본14일/최대31일,
KST morning[05,11)/noon[11,17)/evening[17,23), 미완 구간partial을 제공한다.
sample_count는 metric 행수가 아닌 채택된 관측 시간 bucket 수다. 복수 제공처·재수집을
중복 합산하지 않으며 의미가 확인되지 않은 누적 강수는 합산하지 않는다.
원천별 최신 승인 generation의 reducer 진도가 부족하면 이전 완전 roster를
stale로 유지하며 이전 성공 집합이 없는 선택만503이다. 기존 cursor도 새 원천의
미처리를 stale로 표시하고 TTL 뒤 fallback은 새 handle로 발급한다.
관측 dataset 대상이 아닌 예보 전용 위치는 null/sample_count0이며 빌려 쓸 원천 clock이
없으면 null/stale다. 요청은 작은 summary/coverage 표만 읽고 날짜 이력 전체를 집계하지 않는다.

## 제공처 bootstrap과 운영 비용 gate

기본 current의 지원 범위는 실제 설정된 KMA 실황·AirKorea·산림청 산악/청정넷·KREX·
HTTP3종이다. KHOA는 external forecast 전용이다. 비활성 제공처만 제외하며 필수 최초
미준비를 작아진 정상 집합으로 내보내지 않는다. 청정넷 별도 catalog 권한이 없는 최초
상태는 degraded/stale다. 새 SDK station은 native catalog 저장 뒤 다음 수집에서
사전 roster에 포함되어 승인돼야 공개된다. 전국 완결 증명은 단일행 parser와 구분한다.

설정별 실제 provider/dataset/선택 위치 수로 bootstrap 진행도를 확인해야 한다.
catalog/page 및 개별 provider/dataset cohort는5,000곳, 승인 source vector는8,192개,
승인inventory의 scalar projection은20,000행으로 서로 다른 상한이다(#164).
전체 manifest JSON은8MiB에서 상태128B를 예약하고 artifact chunk는8MiB/5,000points를
유지한다. [메타데이터 상한·합성 비용과 한계](2026-10-23-export-capacity.md)를 따른다.
운영의 실제 설정과 원천 대상 수가 각 상한 안에 있는지 측정하고, 전체·델타·최대
페이지 응답의 CPU/RSS/쿼리/latency와 수집 writer 영향을 확인한 뒤 활성화한다.
소수 fixture 통과는 이 운영 규모 gate를 대체하지 않는다.

`KOR_TRAVEL_WEATHER_SERVICE_EXPORTS_ENABLED` 기본값은 false이며 API·code-server의
설정을 일치시킨다. publisher의 등록/정지 정책과 정상 승인 source bootstrap은 별도 수용한다.
API/code-server/publisher는 같은 `KOR_TRAVEL_WEATHER_SERVICE_EXPORT_ARTIFACT_ROOT`의
NVMe artifact 절대 경로·권한을 공유해야 한다.
승인 inventory/W5 coverage/checkpoint/frozen snapshot/cursor TTL 및 실행 중 reader가
필요한 파일을 GC가 지우지 않도록 live-root와 pin 계약을 수용해야 한다. 동시 읽기
slot/파일 잠금만으로 metadata에서 파일 읽기로 넘어가는 pin/GC 경쟁이 해결됐다고
보지 않는다. 이 gate가 미완이면 자동 ON/운영 준비 완료로 기록하지 않는다.
