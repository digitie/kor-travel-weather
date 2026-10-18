# Weather 작업 기록

## 2026-10-18 — 비활성·이동 지점의 주소 확인 집계 보정

Transport86 r2에서 찾은 공통 MAX 집계 문제는 Weather 비활성 위치와 좌표 이동에도 해당한다.
실제 WeatherLocationRow를 갱신하는 3개 반례를 이전 Common c067에서 3 FAIL로 확인하고,
Common40의 522525a pin과 잠금을 갱신한 뒤 주소 회귀 4 PASS를 확인했다.
파생 과거 행을 보존하고 source 지역·KMA 격자·관리자 metadata 불변도 검증한다.
전체 CI·두 독립 리뷰·UI 및 운영 수용은 각각 별도다. 앞선111/112 이력을 덮지 않는다.

## 2026-10-18 주소 갱신 구현·로컬 검증

VWorld 우선·Kakao fallback, 위치별 달력 6개월 자동 갱신과 Admin 수동 실행을 구현했다. 두 앱은 같은 Common Python 엔진을 고정 commit으로 사용한다. [설계·설정 및 검증 범위](reverse-address-refresh.md)를 참조한다. 원천 주소·좌표는 보존한다.

로컬 실브라우저 최종 재검증은 앱별 13개(합계 26개) 통과했고 실제 인증·상태 API·Dagster 큐 예약·중복 차단·모바일/키보드·오류 복구를 포함한다. 제공자는 주입 fixture다. Python 전체 검증은 Common 509개, Weather 658개, Transport 838개 통과·환경 조건 10개 skip이다. Common39·Weather111은 두 독립 리뷰/CI 통과 후 머지했다. [최종 리뷰 기록](reviews/2026-10-18-address-refresh-final.md)을 참조한다. Transport 정식 gate는 별도이며 운영 migration·실제 제공자 실호출·배포·운영 UI 수용은 미실행이다. Daemon은 중지 상태를 유지한다.

## 2026-10-17 — 최신109와 남은 운영 수용 문서 정산

- 이 문서 PR 생성 전 main39aafe0/OPEN0, #108/#109 exact head·MERGED·PR/main CI 성공을 공개 GitHub 읽기로 확인했다. 코드/config/tests 변경0.
- root 공개 safe의 CS71ff/139f 배포·API8d/d48c 유지, UI36PASS, native run8d87945 complete_snapshot/영속8필드1Observation, daemonOFF·queue3 보존을 [감사](audits/2026-10-17-remaining-work.md)에 구분했다.
- 11:58 RO의 health90/90·77/16, Weather checkout probe 부재와 대체 source 경로/전체 module 미확인 한계를 기록했다. source 소실이나 미배포로 단정하지 않았다.
- [tasks](tasks.md)/[resume](resume.md)를 새 현행 인덱스로 추가했다. 기존 deploy/n150의 계획/실패/체크와 vendored provider 원장은 보존했다.
- 지역 freshness·운영복구·장시간 CPU/RSS·consumer/정식 build 경로 수용은 잔여다. Manager source/runtime probe drift는 공통 소유 후속이며 Weather에 Geo token 소비를 추가하지 않는다.
- 사용자 승인 문서 commit [skip ci]. 링크·diff·사실 대조만 수행, full suite/이미지 build/새 live UI/서비스 제어0. 독립2리뷰/PR merge는 root가 맡는다.
