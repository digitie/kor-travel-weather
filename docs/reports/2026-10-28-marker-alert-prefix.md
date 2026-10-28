# 지도 특보의 적격 후보 제한과 검증

## 현재 운영과 미해결 목표

2026-10-28 01:05 UTC에 요청 예산 변경 a707의 API·웹 배포가 실제 종료 코드 0으로 끝났다.
API 이미지 e90a35a2, 웹 6d2ba2c5이며 CS e393과 공용 daemon은 보존했다.
API·CS의 기존 export 설정 True와 publisher 중지·소비 flag OFF는 서로 다른 상태이며 그대로 유지한다.
이번 특보 후보 변경은 그 뒤의 별도 소스 작업으로, 아직 배포하지 않았다.

첫 cold Firefox 측정 `9c71a7bf96`의 첫 60지점 세 응답은 모두 200이었지만
본문 완료 26.817/25.539/25.316초로 25초 목표를 **실패**했다. 실패 원문은 유지한다.
후속 R17 진단은 고정 진단 실패 한 항목 외 29개를 통과했다. PC 지도 top 152/높이 824,
375px 모바일 top 193/높이 615, 타일·지도 위 상세·℃·풍속·자동 초점·Escape·초점 복귀를 확인했다.
로그아웃 쿠키 잔여 0, 소유 브라우저 프로세스 잔여 0과 runtime 불변도 확인했다.
그때의 14.613/15.576/14.921초는 warm 진단이며 최초 cold 성공으로 바꾸지 않는다.
이전 초점 실패는 검사기가 목록 선택 뒤 지도 탭을 다시 눌러 초점을 이동시킨 문제였다.
올바른 흐름에서는 통과했으므로 제품 키보드 처리는 바꾸지 않는다.

## 근거와 제한된 원인

기본 지도는 여섯 종류를 명시해 관측·예보·특보 세 그룹을 순차 조회한다.
무필터 `kinds=None` 최적화를 기본 지도 경로로 간주했던 PR180 분석은 잘못이었다.
그 당시 시험·실패·계획은 무필터 경로의 증거로만 보존한다.

기존 특보는 적격 행을 전부 읽어 Python에서 위치별 26개를 고른다.
오래된 행의 미래 `valid_until`은 복합 fact 키로 검산한다.
실제 첫 한 지점에 한정한 읽기 전용 실행 계획은 최근 후보 36개와 탈락한 옛 행 35개,
옛 fact 검사 35회와 fact read buffer 125개를 보였다.
해당 부분의 planning 901.777ms, execution 142.917ms는 60지점 전체나 HTTP 시간으로 환산하지 않는다.
현재·예보 수화도 각각 약 1초였으며 전체 cold 지연 원인은 아직 확정하지 않았다.
저장된 최종 특보 키 120개의 payload 크기 합은 56,040바이트였다.
큰 JSON이 원인이라고 단정할 근거가 없어 payload를 줄이지 않는다.

## 최소 변경

- 기존 필터와 적격 판정 뒤, 같은 전체 순서의 위치별 후보 제한을 LATERAL 안에 둔다.
  최근순·현재 우선 아님·metric별 아님 조건에서 최근 적격 행이 예산을 채우면
  더 오래된 행은 이길 수 없으므로 CASE로 그 행의 fact 유효기간 검사를 생략한다.
  부족하면 기존 옛 행 유효기간 검사를 그대로 한다. 별도 SQL 왕복을 추가하지 않는다.
- SQL LIMIT 경계의 문자열 동률은 기존 Python 비교와 맞도록 `COLLATE "C"`를 사용한다.
  NULL·알파벳 대소문자·한글·비ASCII 순서를 회귀로 확인한다.
- 기존 최근/오래된 순서, 제공처·종류·시간 필터, 명시 만료·해제 reducer, 512행 메모리 단위,
  전체 payload와 복합 `(value_id, known_at)` fact 수화를 유지한다.
  READ COMMITTED에서 선택 뒤 사라진 fact는 기존처럼 빠지며 다음 후보로 대체하지 않는다.

단순한 LATERAL LIMIT만으로는 Sort/Bitmap 계획이 먼저 옛 fact를 검사해 35회가 남는 RED였다.
CASE 보완 뒤 소유 PG18 합성 계획에서는 최근 후보가 충분한 경우 fact 검사 0회를 확인했다.
이는 이 낭비 제거의 증거이며 운영 25초 목표 달성의 증거는 아니다.
DB schema·인덱스·provider·writer·DTO·동시성·timeout·반환 제한 값은 바꾸지 않았다.

## 검증 원장

소유 PG18.6/max_connections 100, 2GiB·1CPU, named disk volume과 겹치지 않는 내부 대역을 사용했다.
운영 DB/API/provider·수집 job 호출은 0이다. QA 이미지와 lockfile은 고정하고 소스 전후 지문,
실제 종료 코드, Docker 독립 wait, 자식 종료와 각 시험 DB 연결 0을 확인한다.

- 최초 제품 RED: 옛 fact 검사 35회. 단순 LIMIT 초안도 같은 낭비가 남았다.
- 독립 Python 정렬과 SQL LIMIT의 대소문자 경계는 3 FAIL로 재현한 뒤 C 정렬로 통과했다.
- 기존 60지점 fixture의 raw 최근 40개는 projection에서 20개와 동률 1개로 합쳐진다.
  첫 집중 147 PASS/2 FAIL은 이 수를 41개로 잘못 센 시험 기대였다.
  정확 반환/reducer·필터·복합키·partition 검사는 유지하고 이 기대만 보완했다.
- 최종 관련 9개 시험 파일: WSL **150 PASS/64.51초**, Docker **150 PASS/70.28초**, 각각 actual0이다.
  Docker 독립 wait0·원 컨테이너 부재·두 실행 source7지문 불변·각 DB 연결0을 회수했다.
  직전 149개 버전은 WSL149/Docker149 actual0·wait0으로 보존한다.
  최종 추가 1개는 한글·비ASCII·NULL source 순서를 직접 비교한다.
- 환경 준비 실패, 초기 참조 fixture의 known_at 가정 오류, immutable 삭제 guard 시험 오류,
  SubPlan 번호 고정 recorder 오류도 원문 그대로 보존한다. 제품 성능 실패와 혼동하지 않는다.

증거 루트는 `/var/tmp/weather-alert-prefix-13c068dba2f2`이다.
`focused-r3-wsl`과 `focused-r3-docker`의 result/JUnit/로그가 최종 관련 시험 정본이다.
최초 cold 실패는 `/var/tmp/weather-live-next-a707-20261028-9c71a7bf96`,
R17 진단 receipt는 `/var/tmp/weather-live-next-a707-20261028-c0c224c03d/root-receipt.json`이다.
운영 한 지점 계획은 별도 private 원문을 보존하며 payload·SQL bind·인증값을 문서에 싣지 않는다.

남은 게이트는 정확 후보 CI·독립 두 소스 리뷰·통합 전체 검증 및 조율된 새 배포 뒤 최초 cold 수용이다.
전체 QA나 운영 효과를 관련 집중 시험으로 대신 통과 처리하지 않는다.

## 참고

- [PostgreSQL 18 EXPLAIN](https://www.postgresql.org/docs/18/sql-explain.html): ANALYZE는 실제 문장을 실행한다.
- [PostgreSQL 커서 비용 기준](https://www.postgresql.org/docs/18/runtime-config-query.html#GUC-CURSOR-TUPLE-FRACTION):
  일반 계획과 커서 계획을 따로 검산했으며 이번 저장 사례에서는 계획이 같았다.
