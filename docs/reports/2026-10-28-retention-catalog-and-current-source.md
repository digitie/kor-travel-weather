# 단기예보 정리: 최신 소스와 카탈로그 실측

2026-10-27 21:22:57Z까지의 저장된 RO 원장을 정산한다. 문서 작업의 새 DB/SSH/HTTP·수집·이미지·설정 변경은0이다. 과거6시간 물리 보존 정리는 **아직 미완료**이며 미래 예보 정책은 변경하지 않았다.

## 현재 API 소스와 원 실패

- 실제 API의 알려진 release env와 OCI revision은 `2107839d3302f6cb938d14380193200421fdf58e`다. fresh remote main·실제 package 세 지문과 일치했고 카탈로그 읽기 전후 API CID/image/start·환경 지문은 같았다.
- PR #186은16:53:43Z에 머지됐고 head912d6e73의 python/frontend CI 성공·main210과 같은 tree를 확인했다. API는19:47:54Z에 외부 작업으로 전환됐다. **이번 Root/W의 배포가 아니며**, code-server·웹을 이번 읽기로 재검증한 것도 아니다.
- 첫 읽기는 과거 repository7a112 지문을 요구해 actual85e에서 DB 연결 전에 차단됐다(stdout0/exit1). 원 실패를 보존하고 current210/CID/image에 다시 결박한 명시적 후속1회가 정상 종료했다. 자동 반복이나 운영 source 변경으로 우회하지 않았다.
- e393→210의 repository 함수 AST 차이는 지도 특보 `_current_value_models_many` 하나다. retention 네 메서드·fact/current 모델의 PK/FK/index AST·전체 alembic·retention.py·settings.py는 같다. 이 소스 계약의 동등성은 옛 APIe393·웹5098의 공개UI21을 **현재210 UI 수용으로 승계하는 근거가 아니다**.

| 파일 | actual API 및 main210 SHA256 |
| --- | --- |
| repository.py | `85eaa0b0d44107074d6c8ef12ef0cc06d01b9195327ccc1ef26a4bc61d0fb1d2` |
| retention.py | `b33ddc3a4631fcd8de8fe877e248732525ad3c911f178452e4b8b605ed42de66` |
| settings.py | `486755d5bc567ede82323c331e1c849d152b07a5a5d89e91633d70e219780f8c` |

정본은 commit210의 `src/kortravelweather/repository.py` 모델287/438줄·retention helper2456–2500줄·purge4088–4344줄과 `alembic/versions/0020_short_forecast_retention_index.py`다. 줄 이동 시 고정commit·지문을 우선한다.

## 카탈로그 1회 읽기

PostgreSQL18(180006) READONLY에서 전체20초·연결3초·문장3초·잠금500ms·최대64관계/128index·출력256KiB로 읽었다. Settings DSN은 API RAM 안에서만 사용했다. COUNT/MINMAX·fact/pointer 행·EXPLAIN·DELETE·VACUUM/ANALYZE·DDL·튜닝은 하지 않았다.

| 관측 | 결과 |
| --- | --- |
| 관계 | current1 + fact parent1 + fact leaf16 = 18개 |
| retention index | parent1 + leaf16 = 17개. 실제leaf OID 집합과 대조해 각각1개·누락/중복0 |
| index 상태 | 관측 index 전부 valid/ready/live. retention parent/child 키순서·predicate 일치 |
| current | value_id PK 존재. 전체index11개 중 INCLUDE4개 |
| FK 검증 상태 | fact parent/leaf 참조 FK17개가 NOT VALID |
| current 통계 추정 | n_live_tup8,992,805 / n_dead_tup1,204,116 / n_mod_since_analyze357,114 |

**retention index 누락/비정상 또는 current value_id PK 부재 가설을 지지하는 카탈로그 근거는 없다.** planner의 실제 index 사용·빠른 삭제를 증명한 것은 아니다. current11/INCLUDE4가 이 시점 관측이며 과거7/INCLUDE0을 현재 비용 추정의 정본으로 재사용하지 않는다.

NOT VALID는 기존 데이터의 검증 상태다. 이것만으로 orphan·FK 미강제·삭제 병목이라 단정하지 않으며 제약 검증/삭제/변경도 하지 않았다. FK/heap/WAL·index 유지 비용은 후보 가설이고 측정된 원인이 아니다.

stats_reset은 null로 초기화 경위가 불명이다. 약899만live·120만dead는 정확한 행수나 단기예보 잔여량이 아니다. 과거 카운터와 빼서 삭제/유입률이나 vacuum 정체를 계산하지 않는다.

## 자연 진척과 남은 수용

별도21:05Z 메타데이터에서 native 일정은 `*/10 * * * *`/Asia/Seoul/RUNNING이었다. 최근8 run은7 SUCCESS·1 STARTED, 완료7개의 wall156.189–165.899초다. 시작 간격 중앙값605.526초로, 과거 두 event의 약20분 간격을 실제20분 일정으로 해석하지 않는다.

최근8 materialization은 합계210,000 facts를 삭제했다(각19,000–38,000). **모두 remaining=true·budget_exhausted**다. facts/asset wall124.363–248.153/s는 CPU나 유입률이 아니다. 가장 오래된1개 event는 최근8 run 집합 밖이므로 최종run 상태를 만들지 않는다.

마지막 lag384,646.479132초(약4.452일)는 삭제 위치 기반 **상한**이며 실제oldest age·잔여행수·정체 기간이 아니다. 정시valid_at bucket 안에서는 진척 중에도 오를 수 있다. stall최대2만으로 정체를 확정하지 않는다.

취소된 `weather_export_publisher`는 관측에서 STOPPED이며 **desired STOPPED를 유지**한다. 다른 일정·daemon·export API/설정·Map 소비자를 바꾸거나 재개하지 않았다.

- [ ] 과거6시간 물리 정리 완료·미래값 보존. SUCCESS/진척만으로 완료 처리하지 않는다.
- [ ] 한 가지 비용 가설을 별도 검토된 측정으로 확인. 카탈로그만으로 원인·CPU/RSS 감소를 주장하지 않는다.
- [ ] 현재210 공개UI·마커 성능은 별도 소유자의 실제 수용으로 확인. 과거UI21·외부 배포·합성 시험을 대체 증거로 쓰지 않는다.

[기존 보존 정책](../shortterm-retention.md)과 [당시 source·취소·UI 수용](2026-10-27-current-dagster-and-export-cancellation.md)을 함께 읽는다. 원 실패와 과거 관측은 각각의 시점·범위로 보존한다.

## 저장 원장 지문

원장은 저장소 밖 감사 경로에 보존한다. 비밀/DSN/운영 주소는 싣지 않는다.

| 근거 | SHA256 |
| --- | --- |
| 실제 카탈로그 r2 | `c24f7979e70b77ccbc028e1287c2eab43f81262beb14a70532943d5ba911c9d1` |
| 해석 | `20ede4848c88256e2eefd09f0e73b202f91e3febebd3e8e4f81153e54c0520ae` |
| 독립 카탈로그 검산 | `3034ae657fe9d97fb238d0d4e8e4b21fd850ad228bec47ec01e6eccf4e723455` |
| 최근 자연 metadata r2 | `17284661d04f9e34acdf6720ed826fa2ea66d482e7cfba4768b14848f3bc7a20` |
| 첫 source guard 실패 | `cecfcbd5aad9162064737353949cab722d188d19e6d821118090560d50b1a11e` |
| actual release metadata | `f3173efbe3bdc3c3bf8d30daf53955748b184186704479a9d3c26162da620452` |
| Git/AST 정합 | `6616730aca74f831d600d3eddb8c66e07808a9b366ae7da6dade732d9651c721` |
