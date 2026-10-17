# Weather 현재 인수인계

2026-10-17 main39aafe0/109까지 MERGED. 이 문서 PR 생성 전 OPEN PR0 시점의 정산이다. [감사](audits/2026-10-17-remaining-work.md)와 [남은 작업](tasks.md)을 읽는다.

root 11:58:07 UTC RO는 API8d2777b·CS8cdb78d healthy/restart0/OOMfalse·점검90/90초, UI기존 noHC, 전체77/16running·공유 daemon OFF를 확인했다. label을 전체 module 검증으로 바꾸지 않는다.109 UI36PASS, native retention run8d87945 SUCCESS/complete_snapshot·8필드 Observation1개는 완료된 한정 수용이다.

다음은 지역 freshness·정식 NVMe source/build 경로·consumer/관측 증거의 RO 정산이다. Manager 정본의 옛 probe와 운영 안전 override 차이는 공통 소유자가 후속 통합한다. 현재 운영 kill-probe라고 주장하지 않는다.

이미 있는 adapter/sampling/recovery/retention을 재구현하지 않는다. daemon 자동 복구/스케줄 start·새 SQL/DDL·초기정리 반복0. 별도 수집 재개 승인 뒤 장시간 자원·단일 retry 소유권·정기 처리량을 수용한다.

이전 백로그와 당시 상태는 deploy/n150.md에 그대로 남기며 vendored provider 문서는 편집하지 않는다. 이번 docs4개 commit은 사용자 승인 [skip ci]이고 full suite/새 build·live UI0, 독립2문서 리뷰 후 root merge한다.
