# Map export 실제 collector opt-in과 독립 publisher

상태: 구현·검증 중. 기본 실행/배포 설정은 OFF다. PR143 성공 head 기반의 후속이며 전체 W1~W5 공개 완료가 아니다.

- `run_weather_sync(export_artifact_root=절대경로)`를 단일 KMA 초단기실황 job에 명시적으로 지정하면 기존 실제 KmaClient 요청 전에 grid/location/policy를 선언한다. 기존 여러 grid 혼합 batch를 export가 완결 증명한 것처럼 처리하지 않는다.
- opt-in 단일grid는 기존 `GRID_PUBLISH_VALUES` 상한 안에서만 fanout 전체를 한 기존fact+receipt transaction으로 저장한다. 상한을 넘는 grid는 원본 bounded batch를 유지하고 export head를 만들지 않는다. OFF는 기존 batch와 성공 의미를 유지한다.
- public provider pagination이 없거나 계획/파일/봉인이 실패하면 export만 failed로 남긴다. 원본 DB 실패는 그대로 전파하며 재시도 때문에 fact를 두 번 쓰지 않는다. run 성공이 아직 아닌 시점에는 head가 없다.
- `publish_pending_exports`를 collector job 밖에서 별도 호출한다. actual instance의 `get_run_by_id`와 정확 `status.name == SUCCESS`를 사용하며 native 강등이나 `is_finished` 대체는 하지 않는다. missing/running은 보류다. keyset cursor/건수 상한과 후보 사이 시간 예산으로 pending 뒤 후보도 처리한다. 이미 확정된 attempt는 조회에서 제외한다.
- 전국 자동실행 설정·Dagster sensor 배포·W1/W2 frozen reader·W3/W5·비용 gate·운영 활성화는 아직 후속이다. 파일 GC는 없다. API503을 데이터 공개 완료로 세지 않는다.

집중 실제PG32 PASS91.85초: 실제 KmaClient HTTP 계약 경로→기존 normalizer→3location/2grid fanout→원본fact/receipt→별도native publisher, OFF 원본 경로, exact Dagster instance 상태, 파일 실패/증거 누락 시 원본 성공 유지, 기존 grid chunk/prefetch/pagination 회귀. 전체 Python/frontend/CI·독립 두 적대 리뷰는 진행 예정이다. 운영 변경 없음.
