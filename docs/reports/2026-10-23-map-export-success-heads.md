# Map export 단계3B: 단일 KMA 실황 성공 head

상태: 구현·검증 중. 기존 collector는 opt-in OFF, 서비스 API는 503이다. W1~W5 공개 완료가 아니다.

- 수집 전 immutable descriptor에 provider/dataset/grid/선택 location/정규화·winner 정책을 고정한다. 정책 변경은 별도 namespace다. 단일 KMA 초단기실황 source만 지원하며 혼합 source·예보·특보는 거부한다.
- public PaginationReceipt의 전체 페이지 완결과 실제 grid/base/semantic 중복을 확인한다. 기존 Weather normalizer 결과의 semantic digest와 결측·이상값 분류를 receipt 전에 고정한다. 원본 fact/capture는 같은 transaction/savepoint를 사용하며 export 오류가 원본 성공을 rollback하지 않는다.
- exact native/Dagster SUCCESS, 계획/epoch/seal 증거와 immutable artifact를 재검증한 뒤 성공 generation과 head/version을 한 transaction으로 기록한다. current pointer를 재조회하지 않는다. 늦은 이전 base는 SUPERSEDED, 같은 base의 근거 없는 값 변경은 CONFLICT다.
- semantic digest는 값/단위/정책/시각을 보존하고 재조회 known_at·collected_at/DB PK/raw alias를 제외한다. 같은 내용은 직전 성공 파일을 참조하며 대형 artifact를 복제하지 않는다. 실제 source freshness는 별도 metadata다.
- run/attempt 이력과 공개 generation은 cascade로 묶지 않는다. candidate/직전 성공 파일을 검증하기 전에 durable pin을 남기고 connection을 반환한다. 파일 검증과 CAS 사이에는 5초 유한 관리 guard를 유지한다. TX statement_timeout은 5초, 원본 ingest lock_timeout을 재사용한다. 외부 unlink/디스크 장애까지 원자적으로 보장하지 않으며 reader는 재검증해야 한다.
- GC/공개 frozen reader/실제 collector·Dagster adapter 배선/전국 scope 완결/성능 gate/W3/W5는 후속이다. 정상0·비정상 소집합을 정상 공개하지 않는다. 현재 계약은 nonempty 단일 source이며 provider 의미상 기대 metric 수까지 증명한 전국 최신 API가 아니다.

검증은 진행 중이며 exact commit별 CI/전체Python/frontend/실제 API·UI 및 독립 두 적대 리뷰 결과를 완료 시 추가한다. 운영 변경은 없다.
