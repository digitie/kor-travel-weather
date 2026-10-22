# Weather 남은 작업

아래 기존 인덱스는 2026-10-17 main39aafe0 기준 이력이다. 현재 PR #118의 부분 수용과 남은 게이트는 아래 최신 절을 따른다. 문서 PR 생성 전 감사 조회에서 OPEN PR0이었다. [전체 감사·증거·선행 관계](audits/2026-10-17-remaining-work.md)를 함께 읽는다. 과거 이전 백로그는 [deploy/n150.md](../deploy/n150.md)에 보존한다. vendored python-airkorea-api의 tasks/resume/journal은 별도 provider 문서다.

코드·PR merge·실배포·운영 수용은 다른 상태다. 증거 미확인을 미배포 확정으로 쓰지 않는다. daemon OFF는 현재 사용자 정책이며 자동으로 해제하지 않는다.


## 2026-10-23 Map 서비스 export 설계 — proposed, 구현 미착수

[설계 정본](design/map-service-exports.md)을 보존한다. 아래는 **Weather 소유** 작업이며 모두
미착수다. Transport T1~T5는 같은 문서의 외부 연계 절일 뿐 이 저장소의 완료 항목으로 세지 않는다.
설계 문서 PR 머지는 코드/운영 수용이 아니다. W5 관측 범위는 사용자 답변 pending, COW는
실측 exit gate를 통과한 뒤에만 채택한다.

- [ ] W-MAP-01 — W1/W2/W4 DTO·auth·status 계약 준비. 기존 require_admin/x-admin-token 재사용과
  신규 service route의 OpenAPI·타입·오류·bounded query 계약을 준비한다. 이 단계는 W1/W2 데이터
  공개 완료가 아니다. 해제 조건: 설계 §1·2·4·7의 계약 준비 검증, 기능 CI·2인 리뷰.
- [ ] W-MAP-02 — W-MAP-01 뒤 성공 attempt receipt·scope 완결 barrier·독립 성공 export head와
  W1/W2 실제 공개·W3 snapshot을 구현한다. W1/W2 공개 완료는 이 성공 head/barrier가 선행해야 한다.
  zero-new-row·alias 재관측, failed chunk 차단, epoch/CAS·cursor/ETag/gzip·시간 경계와 durable
  checkpoint를 검증한다. 해제 조건: 설계 §3·7의 실패 반증·live 검증 및 COW 합산 실측 exit gate.
- [ ] W-MAP-03 — W5 관측 eligibility 선택 확정. KMA 실제 관측 NOWCAST 포함 여부는 사용자
  답변 pending이며 종속 구현은 대기한다. 답변과 최종 metric/source 범위를 설계에 기록해야 닫는다.
- [ ] W-MAP-04 — W5 bounded 사전 집계·요약 retention·초기 backfill·API. W-MAP-03의 사용자 선택과
  W-MAP-02의 성공 receipt/projection을 모두 선행 조건으로 삼으며 기존 원본 history를 보존한다. 해제 조건: 설계 §4 W5·§7의 slot/강수/준비 상태·실패/중복
  반증과 bounded query·기능 CI·2인 리뷰·live 수용.
- [ ] W-MAP-05 — Map 소비자 계약 전달·수용·배포. 배포 전 실제 응답 예시/OpenAPI/PR을 Map
  담당에게 전달하고, source_status/체크포인트/304/미래 필터 계약과 rollback을 함께 검증한다.
  해제 조건: 설계 §7의 기능 CI·2인 리뷰·live E2E/API/UI·실측·배포 증거. 설계 PR로 완료하지 않는다.


## 완료한 한정 범위

- [x] #107/#108 수집 CPU/복사 경로 개선 source MERGED. 실제 이미지/UI 수용과 동일 workload 전체 수집 성능 측정은 구분한다.
- [x] #109 source MERGED/CI 성공·CS 최신 이벤트 모듈 배포·UI36PASS·native retention complete_snapshot/영속8필드 Observation1개 확인. 정기 스케줄 완주 보증은 아니다.
- [x] KREX/KRForest adapter·산악 gate/streaming/공유 잠금, Common executor·분산예약/recovery, sampling/health 코드 존재와 머지 확인. 실제 최신성·복구/장시간 부하 수용은 별도다.

## 남은 단계

- [ ] W-OPS-01 / P1: 휴게소·산악 개별 native run/관측 최신성·누락 원인 RO 정산.
- [ ] W-OPS-02 / P1: NVMe canonical source/build 경로·consumer/관측 효과 증거 정산. source probe 부재와 대체 경로 미확인을 구분한다.
- [ ] W-DEP-01 / P1: Manager 정본의 옛 Weather probe와 실제 안전 override drift를 공통 소유자가 최신 main에 통합. #486/#488의 구 head를 blind merge하거나 현재 운영이 kill-probe라 단정하지 않는다.
- [ ] W-OPS-03 / P2: native/Common 단일 retry 소유권·선택 센서 상태·첫 평가/중단재개 수용. OFF에서 자동 활성화하지 않는다.
- [ ] W-OPS-04 / P2: 별도 승인된 재개 뒤 정기6시간 정리 처리량/deferred·미래 보존/진행 이벤트 수용.
- [ ] W-OPS-05 / P2: 같은 workload CPU/RSS·3h/12h 포함24h OOM/swap·실제 memory 정책 수용. PR #118의 고정 격자 입력 RSS 약15% 감소·DB 포함 CPU 동등 및 복구2건은 아래와 같이 부분 수용했으며 전체·장시간 조건은 남는다.
- [ ] W-DOC-01 / P2: 실제 경로/효과 증거 확정 뒤 옛 runbook 현행 절차만 최소 갱신. T-WEA-03/04는 조건부 후보로 유지한다.
- [ ] W-OPS-06 / P2: 회수 sensor 예산·정체 경보(2026-10-21 PR) 배포 뒤 수용. code-server 재빌드, 규칙 reload, `ktw_sync_recovery_stalled` series(dagster job, 0) 존재, sensor tick이 `stall:null` cursor로 성공하는지 확인. daemon 재개는 별도 승인.
- [ ] W-OPS-07 / P2: 외부 provider run group 분리(2026-10-21 PR) 배포 뒤 수용. 공용 instance `tag_concurrency_limits`에 `kortravelweather/run_group=external_weather_slow` `limit: 1` 추가(Manager), slow 항목을 code-server 갱신 **전에** 넣는다(없으면 slow run이 group 상한 없이 돈다). 갱신 뒤 fast provider run이 `open_meteo` 진행 중에도 바로 시작하는지, 외부 run이 최대 2개(fast 1 + slow 1) 겹칠 때 odroid CPU·load가 견디는지 확인.

2026-10-17 당시 작업은 docs4개뿐이었다. 당시 사용자 승인 [skip ci] 문서 검토 범위이며 현재 PR #118의 제품 검증을 뜻하지 않는다.


## 2026-10-18 수집 카탈로그 CPU/할당 peak 개선

metadata 포함 카탈로그를 ORM 대신 DTO 컬럼 projection으로 조회한다. 실제 DB→DTO→target
1,450행 로컬 비교와 한계를 [측정 기록](collector-catalog-cpu-20261018.md)에 기록했다.
두 명의 독립 소스 리뷰·exact-head CI·ARM64 API/CS native build·r4 prepare는 통과했다.
ARM 격리 fixture 10표본에서 CPU 중앙값 약 12.2%, Python 할당 peak 약 16.0% 감소했고
RSS의 실질적 저감은 주장하지 않는다. API/CS 배포065661ca PASS와 배포 후 WSL
Chromium1243 UI36 PASS/exit0를 확인했다. 이 수용 기록은 PR #117에 포함한다.
실제 provider 전체 수집 CPU/RSS·장시간 안정성·Kakao 실제 fallback/주소 전체 sweep 및
Track 1/2의 미검증 수용은 별도로 남긴다. 이전 pending 문장은 각 기록 시점의 이력이다.
Dagster daemon OFF를 유지한다.


## 2026-10-18 PR #118 비특보 예보 배치 메모리 개선 — 배포·UI 수용

제품 a8949778은 다음 위치가 상한을 넘기기 전에 기존 묶음을 게시하고,
격자·중기 예보의 staging·게시 상한을2500으로 분리했다. 특보 staging과
다른 provider의 공용 게시 상한은5000을 유지하며 한 위치는 나누지 않는다.
격리 PostgreSQL 전체667개·Ruff·최종 Python/frontend CI·독립 소스 리뷰2건이 통과했다.

Odroid의 고정 합성 입력(792fact×10위치)에서 기준/후보 각3회 비교를 두 측정군으로
수행했다. PostgreSQL을 포함한 측정군은 worker RSS 중앙값 약14.99% 감소,
합산 CPU 약1.29% 감소, 처리 시간 약2.02% 증가였다. CPU는 작은 표본에서 동등
수준으로 판단한다. SQL은107→125이며 전체 provider·전체 job·호스트의 개선율은 아니다.
실측은6421a4b에 상수2500을 주입한 결과이고, 실제 a8949778 기본값은 별도로
rollback+alias 및 commit 후 응답 오류의 복구2건을 통과했다.
각각2376+5544,4752+3168건으로 총7920건을 복구하고 정상 기준의 원천·fact·current
의미 digest 일치와 다음 재수집0건을 확인했다.

[상세 측정·원시 증거·복구 및 한계](reviews/collector-bounded-stage-20261018.md)를 따른다.
이번 제품의 ARM64 API/CS native 이미지 빌드와 각 이미지의 설치 소스67파일 검증이
통과했다. 운영 전환 f5cc3880에서 API/CS READY·기존 큐와 env 파일 보존·버전 외 runtime
환경 동일·daemon OFF를 확인했다. 배포 후 WSL Chromium1243 live UI는36/36 PASS,
exit0이며 Weather 세 역할의 실행 상태가 유지됐다.
[배포·UI 수용 증거](reviews/collector-bounded-stage-deployment-20261018.json)를 따른다.
PR #118은 머지·배포·UI 수용을 완료했으며 daemon OFF를 유지한다. 전체 수집·24시간 OOM/swap·자동 복구·Track1/2의 미검증
운영 수용을 완료로 바꾸지 않는다. PR #117의 RSS 미입증 문장은 당시
카탈로그 조회 범위의 기록이며 이번 격자 저장 실측과 구분한다.

## 2026-10-19 지역 관측 정산 진행

휴게소·산악 최근 성공 native 실행과 실제 저장 행을 대조했다. [개별 제외 원인과 최신성 한계](audits/2026-10-19-regional-acceptance.md)를 따른다. 좌표 없는 응답과 관측값 없는 응답은 별도로 집계한다. 당시 자동 재개·신규 live 수집 수용은 대기 중이었다. 신규 두 asset 수동 수용은 아래 후속 기록으로 완료했고 자동 재개는 보류한다.

지역 제외 집계 제품683e446d의 전체668·frontend214·CI·두 독립 소스 리뷰와 운영 API/CS 배포·UI36은 완료했다. 이 배포 수용과 후속 실제 신규 지역 수집·자동 주기 검증은 구분한다.


## 지역 수동 수집 후속 수용

[수동 지역 수집 수용](audits/2026-10-19-regional-live-collection.md)을 완료했다. 첫 probe 실패와 두 번째 DB6403건 저장 뒤 native IO 실패를 보존했고, 보강 후 r3의 두 asset native SUCCESS·DB 연결6403건/신규5256건과 수집 후 UI36 PASS를 확인했다. 휴게소 신규0건은 정상 재수집이며 산악은 좌표59·빈값16 제외를 실측했다. 제품 변경과 daemon 재개는 없다. 전체 주소 수동 갱신은 미검증, 예약 주소 갱신·보관·자동 복구·장시간 전체 수집은 daemon OFF 제약 아래 별도 수용으로 남는다.
