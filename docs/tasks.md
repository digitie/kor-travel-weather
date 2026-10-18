# Weather 남은 작업

2026-10-17 main39aafe0 기준 현행 인덱스다. 문서 PR 생성 전 감사 조회에서 OPEN PR0이었다. [전체 감사·증거·선행 관계](audits/2026-10-17-remaining-work.md)를 함께 읽는다. 과거 이전 백로그는 [deploy/n150.md](../deploy/n150.md)에 보존한다. vendored python-airkorea-api의 tasks/resume/journal은 별도 provider 문서다.

코드·PR merge·실배포·운영 수용은 다른 상태다. 증거 미확인을 미배포 확정으로 쓰지 않는다. daemon OFF는 현재 사용자 정책이며 자동으로 해제하지 않는다.

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
- [ ] W-OPS-05 / P2: 같은 workload CPU/RSS·3h/12h 포함24h OOM/swap·실제 memory 정책 수용.
- [ ] W-DOC-01 / P2: 실제 경로/효과 증거 확정 뒤 옛 runbook 현행 절차만 최소 갱신. T-WEA-03/04는 조건부 후보로 유지한다.

이번은 docs4개뿐이다. 사용자 승인 [skip ci]로 링크·diff·사실 검토만 하며 독립2문서 리뷰/머지는 root 단계다.


## 2026-10-18 수집 카탈로그 CPU/할당 peak 개선

metadata 포함 카탈로그를 ORM 대신 DTO 컬럼 projection으로 조회한다. 실제 DB→DTO→target
1,450행 로컬 비교와 한계를 [측정 기록](collector-catalog-cpu-20261018.md)에 기록했다.
두 명의 리뷰·운영 배포·운영 CPU/RSS 실측은 아직 완료되지 않았다.
