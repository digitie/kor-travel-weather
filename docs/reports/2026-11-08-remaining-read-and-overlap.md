# 남은 조회 지연과 대표 카드 겹침

## 2026-11-08 — 남은 조회 지연·대표 카드 겹침 수정

실제 프로파일에서 current 렌더44.85초·freeze37.81초와 캐시 무결성 JOIN의SQL57014를 관측했다. 같은 RR의 PK두조회로 같은 존재 검증을 유지하고, 비압축 plan전체8MiB가 증명된 때만1024행 fetch를 허용하며 그 외128행을 유지한다. snapshot 참조는 순서·FK·TX를 유지한단일UNNEST INSERT로 저장한다. 후보 렌더30.49초 관측 뒤 느린ref크기예비조회는제거했다. WSL/DockerPG18최종54개씩통과,SQL5초·상태60초·원본/파일검증은보존한다. UI도 원좌표/표시값/특보·키보드를보존한대표pill충돌분리와스크롤목록을수정하고WSL/Docker32개씩·타입/린트·28대표Firefoxfixture를검산했다. 아직 새 API/UI 이미지 배포 전이다.

실제 새 이미지·Manager/일정·HTTP·n150 PC/모바일 결과는 배포 뒤 별도로 정산한다. 기존 실패와 속도 목표 미달은 유지한다. 원장 weather-transport-remains-20261108/plan.json, UI 원문 fuel-region-overlap-20261108/manifest.json. 새 리뷰/SDK설치/백업복원 게이트는 추가하지 않는다.

## 첫 실제 반영과 후속 실패 재현

첫 최소3이미지(Weather API48e81c38·web86ebc25b, Transport UIff4aaecd)는 정상G/R에서 실제 반영했다. 첫 Invocation6237ef93은 done/Main0/cgroup부재,70개 소유 자식 종료0, Manager 양 앱 healthy·원27/20 일정·환경4파일/CS·다른 역할 보존이다. 이 배포 성공과 아래 수용 실패를 구분한다.

실제 HTTP 첫 current는13.394초503/statement_timeout이었다. 최소62초 뒤 current33.180초200, daily18.394초200/조건부7.852초304/만료 뒤14.508초200이며 원천 stale는 그대로다. 재시도/수동 수집/SQL 제한 확대는 없다. n150 R2는 Transport 카드 픽셀 겹침0·전5유종/목록 일치지만 일부 중심 클릭 가림, Weather PC 통과/모바일 마지막 복합 assert 실패다. 모바일 별도 좁은 관측은 문서375px/viewport375px·pageerror0이므로25열 상세표에 추측 clip을 추가하지 않는다. 첫 관측 보조의 os import 누락/원 실패·R2·정상 logout/브라우저 종료·비공개 자격 삭제 증거를 보존한다.

추가 native 실행에서 승인 상태 복합 CTE 도중 SQLSTATE57014/20.495초 중단을 재현했다. 이를 같은 RR의 소유·시도·공개 세대·서로 다른 run 키 집합4조회로 분리했다. 원본 owner_epochs JSON의 Python32자 비교·시도/세대/run 유실·실패 판정은 유지한다. current의 두3,305개 IN(약101/112KB SQL)과 daily 소유자 목록은 PostgreSQL 배열1개로 바꾸며 다른 dialect는 기존 IN을 유지한다. 실패 로그에는 자체 파일명·함수·행만 보태고 메시지/SQL/DSN/파라미터는 쓰지 않는다.

후속 WSL/Docker PG18은 각각86개 통과했다. 먼저 실행한70개/85개와 겹침은 합산하지 않는다. run 조회 대상 계수 시험의 원1실패는 보존하고 관련 대상만 세도록 고쳤다. NULL run·epoch 형식·유실/실패·캐시 만료·개별 예산·cursor/ETag/API도 대조했다. 후속 native 후보는34.286초에 같은 current100행 응답 생성을 완료했으나 이는 운영HTTP/p95나 코드 단독 개선율이 아니다.

Transport 실제 차단 노드는 투명한 kt-map-stage-summary 전체 폭이었다. 기존 SDK marker host는 이미 pointer-events:none이므로 그 가설의1줄은 버렸다. summary wrapper의 빈 영역 입력을 통과시키고 실제 내용은 입력 가능하게 유지한다. 두 앱은 화면 용량 초과 목록의 자동 열기를 data/viewport effect당 최초1회로 제한해 명시 접기가 유지되게 한다. 원 좌표·원점 연결·전체 값/5유종/키보드·미제공·특보는 보존한다. 이 후속 소스는 아직 새 이미지 반영 전이며 다음 실제 원장으로 정산한다.
