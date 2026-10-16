# Weather86~106 최종 통합 검증 정산

이 기록은 기존 실패·미완 원문을 덮어쓰지 않고 현재 gate를 정산한다.
제품 통합은58230797b1b3c932537b28fc8ca5139e8075c49e, 이후39e26fc까지는 문서 전용이다.

## 제품 및 실제 UI

소유 PostgreSQL의 전체628PASS/49warnings, frontend202/type/lint/Ruff 통과와
제품582 CI python/frontend SUCCESS를 확인했다.39e 문서 HEAD도 두 CI SUCCESS다.
실제 arm64 API/CS/UI582 배포 뒤 같은36개 live UI R25가36PASS/0FAIL이었다.
25초 지도 제한과692identity/12개 정상 응답200을 유지하며 모바일/로그인/메뉴/
Dagster 이력·상세/503·abort 후 복구를 포함한다.
[실측·제품 리뷰](2026-10-17-weather-marker-performance.md),
[JIT·부분 인덱스 비교](2026-10-16-weather-jit-measurement.md)를 함께 읽는다.

## 두 독립 제품 리뷰의 계보

[전체 binding](evidence/2026-10-17-weather-stack-review-bindings.json)은
제품별 검토 HEAD·독립 작성자·원문 SHA를 기록한다. 제품 작성자의 자기 검증과
운영 controller 리뷰를 독립 제품 리뷰 인원으로 대신하지 않는다.
102/103/104/105/106의 원문은 같은 evidence 디렉터리에 보관한다.

기존 감사에서87의 두 제품 FULL 원문은 확인되지 않아 새 독립 A/D 검토를 수행했다.
전체 f60d2c2→acc8c71의3commit/제품3·tests2·docs1을 읽었다.
[A 원문](evidence/2026-10-17-weather-collector-full-a.md)
SHA39a29606016d9428b003baefd771da79b61a515bd0ddc38d2e0d8f06cc339f2a,
[D 원문](evidence/2026-10-17-weather-collector-full-d.md)
SHA1ba8fcdca3b6c28f612109727fbeaec19c57673dc31f738624c44a64a741b144은
각각 SOURCE_PASS/openP0P1P2=0이다. 서로 새 원문을 읽지 않았고87제품 저자가 아니다.
A의 독립15묶음과 D의276조건은 별도 합성 범위이며 실제 PG 경합·수집 성공으로 합산하지 않는다.
산악 gate/streaming/shared lock 핵심은 실제 배포582에도 보존됨을 두 검토자가 대조했다.

## 순차 merge 감사와 현재 상태

[D 원감사](evidence/2026-10-17-weather-stack-merge-audit-d.md)는 당시
87원문/106CI 대기 HOLD를 그대로 남긴다.
[D 후속 확인](evidence/2026-10-17-weather-stack-merge-audit-d-append.json)은
39e 두 CI SUCCESS와86 실제 MERGED(369ee5e5)를 확인했다.
root는 위 새 A/D87 원문을 모두 직접 읽었으므로 두 source blocker는 해소됐다.

현재87~106의 실제 merge는 순차 실행 대상이다. 완료되지 않은 PR을 이 문서가
MERGED라고 표시하지 않는다.86→87→…→106 순서로 main retarget/ready/merge commit하며
squash/rebase/후속 base branch 조기 삭제를 하지 않는다. 감사의21단계 가상 merge는
충돌0/최종39e tree exact였으며 실제 최종main에서 제품 tree를 다시 대조한다.

초기 단기예보 삭제·미래 보존 사후 검증과 같은 서비스 집합 idle CPU 측정,
수집 job 성공·freshness는 별도 운영 범위이며 R25를 이들 증거로 대신하지 않는다.
