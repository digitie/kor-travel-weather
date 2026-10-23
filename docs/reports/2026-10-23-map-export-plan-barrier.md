# Map export 단계3A: 예상 계획·완결·terminal 검증

상태: 구현·검증 중. 새 모델·내부 lifecycle만 추가하며 기존 collector는 여전히 opt-in OFF다. API는 계속 EXPORT_NOT_READY이고 head·COW·frozen pagination·W5를 구현/공개한 것이 아니다.

- source roster는 attempt 생성 때 고정한다. 원본 receipt 전에 명시 scope epoch를 claim하고 예상 chunk의 typed normalization digest/count/source 집합을 등록한다. 각 source의 완결 선언과 예상/실제 chunk의 정확한 집합을 비교한 뒤 계획을 닫는다. 저장된 receipt 개수만 역산해 완결로 보지 않는다.
- 초기 scope owner registry를 소유해야 예정 chunk를 기록할 수 있다. 새 attempt claim은 epoch를 전진시키고 옛 worker의 export만 거부한다. 이 오류는 기존 savepoint 안에 있어 원본 fact 성공을 취소하지 않는다. 새 owner는 insert0 replay를 정상 후보로 만들 수 있다.
- 원본과 같은 run→attempt→정렬 scope 잠금 순서와 bounded lock_timeout을 쓴다. file와 Dagster 조회 중 DB connection을 반환한다. chunk point5000/8MiB, 예상chunk10000/source참조50000, tick최대8chunk/16MiB 한정이다.
- native는 실제 SyncRun success가 필요하다. Dagster는 동일 orchestrator ID에 대한 성공 전용 lookup의 정확한 SUCCESS를 요구한다. missing/unknown/오류/2초 timeout은NOT_READY, FAILURE/CANCELED는REJECTED다. is_finished boolean을 성공으로 읽거나 native로 강등하지 않는다. Common deadline과 전용1slot을 재사용해 timeout thread가 쌓이지 않게 한다. 실제 Dagster instance adapter/sensor 배선은 아직 없다.
- 파일 digest/count 검증과 terminal 증거를 모은 뒤 sealed/ELIGIBLE만 기록하며 공개 head는 전진시키지 않는다. ELIGIBLE은 검증 당시의 증거이며 파일의 영구 생존 보장이 아니다. 후속 publisher/reader가 파일 존재/hash를 다시 검증해야 하고 아직GC는 없다. terminal 이후 누락 artifact는 원본 ingest로 재생하지 않고 새 수집이 필요하다.
- legacy stage2 unplanned receipt를 사후 계획으로 승인하지 않는다. 현재3A는 nonempty 후보만 지원한다. 정상0의 provider별 positive empty proof 통합은 후속이며, 불명0을 성공으로 공개하지 않는다. 이는 기존 원본수집의 성공 의미를 바꾸지 않는다.

전용PG 기존receipt24+새lifecycle16=40 PASS30.59초. native/Dagster 정확status·누락파일·미계획/누락segment·입력변경·tick분할·scopeepoch/insert0·계획불변·실제scope행잠금과 원본commit을 검증했다. 추가 source누락/0후보/파일중pool반환/계획byte상한 회귀와 migration·전체회귀·CI·2리뷰는 진행 중이다. 비용exitgate/실제collector/운영API 수용은 미완이다.


## 독립 적대 리뷰 반영

- 입력alias가 canonical source key로 바뀌어 정상replay0이 거부되는 P2를 수정했다. typed normalization digest는 raw payload·source key alias를 제외하고, 실제canonical lineage는 별도 artifact/receipt로 계속 검증한다. planned alias replay가 원래 canonical key와1point/insert0으로 ELIGIBLE이 되는 실제PG 회귀를 추가했다.
- 다른 source의1point가0point source를 감추는 P2를 수정했다. source별 unique canonical point 수를 원본과 같은 transaction의 receipt에 기록하고 모든선택source가 양수인지 검사한다. A1/B0 혼합chunk는 정상0 proof 없이 barrier를 통과하지 못한다. nullable 가산 column은 migration0024에 포함한다.
- roster×전체chunk의 이중 집계는 source-point 참조를 한 번 순회하는 선형 집계로 바꿨다. capture/expect/close_source는 해당 source의 owner만 잠그고 최종close/reconcile은 전체roster fence를 유지한다. seal은 owner epoch·예상input count/digest·source counts에 묶인다. CPU 절감 실측을 했다는 의미는 아니다.
- 수정집중PG46 PASS74.49초(추가selective-lock 전)와 기존회귀44 PASS48.57초를 확인했다. selective-lock·선형/manifest보강 최종집중과 전체/migration/CI/리뷰 closure는 진행 중이다.
