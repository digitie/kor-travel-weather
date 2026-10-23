# Map export 단계3B: 단일 KMA 실황 성공 head

상태: PR143의 내부 성공 head 범위 구현·검증·머지 완료. 기본 collector는 opt-in OFF, 서비스 API는 503이다. W1~W5 공개 완료가 아니다.

- 수집 전 immutable descriptor에 provider/dataset/grid/선택 location/정규화·winner 정책을 고정한다. 정책 변경은 별도 namespace다. 단일 KMA 초단기실황 source만 지원하며 혼합 source·예보·특보는 거부한다.
- public PaginationReceipt의 전체 페이지 완결과 실제 grid/base/semantic 중복을 확인한다. 기존 Weather normalizer 결과의 semantic digest와 결측·이상값 분류를 receipt 전에 고정한다. 원본 fact/capture는 같은 transaction/savepoint를 사용하며 export 오류가 원본 성공을 rollback하지 않는다.
- exact native/Dagster SUCCESS, 계획/epoch/seal 증거와 immutable artifact를 재검증한 뒤 성공 generation과 head/version을 한 transaction으로 기록한다. current pointer를 재조회하지 않는다. 늦은 이전 base는 SUPERSEDED, 같은 base의 근거 없는 값 변경은 CONFLICT다.
- semantic digest는 값/단위/정책/시각을 보존하고 재조회 known_at·collected_at/DB PK/raw alias를 제외한다. 같은 내용은 직전 성공 파일을 참조하며 대형 artifact를 복제하지 않는다. 실제 source freshness는 별도 metadata다.
- run/attempt 이력과 공개 generation은 cascade로 묶지 않는다. candidate/직전 성공 파일을 검증하기 전에 durable pin을 남기고 connection을 반환한다. 파일 검증과 CAS 사이에는 관리 guard를 유지하며 guard 획득 대기만 최대5초다. 전체 승격의 wall deadline/느린 file I/O 중단 보장은 아직 없으며 활성화 전 후속 gate다. TX statement_timeout은 5초, 원본 ingest lock_timeout을 재사용한다. 외부 unlink/디스크 장애까지 원자적으로 보장하지 않으며 reader는 재검증해야 한다.
- GC/공개 frozen reader/실제 collector·Dagster adapter 배선/전국 scope 완결/성능 gate/W3/W5는 후속이다. 정상0·비정상 소집합을 정상 공개하지 않는다. 현재 계약은 nonempty 단일 source이며 provider 의미상 기대 metric 수까지 증명한 전국 최신 API가 아니다.

검증은 진행 중이며 exact commit별 CI/전체Python/frontend/실제 API·UI 및 독립 두 적대 리뷰 결과를 완료 시 추가한다. 운영 변경은 없다.

## 적대 리뷰 수정: source clock provenance

- bound response의 실제 fetched_at을 immutable source plan에 저장하고 seal에 포함한다. 선언 이전/미래 clock을 거부하고 capture의 canonical source별 clock·record fetched_at·모든 receipt source clock이 정확히 같을 때만 승격한다. generation/head freshness는 임의 caller clock 대신 bound plan에서 가져온다.
- 미래2100년/선언 이전 capture clock·bind 이후 record clock 변경·미래 bind clock 회귀를 추가한다. 부적합 export가 거부돼도 기존 원본 fact commit은 보존한다. 공개 reader/전체 wall deadline·GC/pin 회수·비용 gate는 계속 OFF 상태의 후속이다.

## PR143 최종 수용

- 최종 제품43fa8ae, main merge476c9f11ebd338e1be8855704c98158793749ef3. source clock P1 수정 후 독립 James/Popper 두 적대 리뷰 PASS, 열린P0/P1/P2없음.
- 정확43fa git archive의 독립tree/PG 전체812 PASS2129.43초, 집중71 PASS181.26초, exactCI Python/frontend PASS, frontend243/typecheck PASS.
- 정확제품의격리TCP API3(auth/503)+productionUI11 PASS, browsererror0/serviceupstream0. 실제데이터200/운영배포수용이아니다.
- 실제nonempty collector opt-in은후속PR144로별도수용했다. strict8category/공개frozenreader는PR145에서진행중이며전국/전체provider/W3~W5·비용/GC·운영gate는미완이다.
