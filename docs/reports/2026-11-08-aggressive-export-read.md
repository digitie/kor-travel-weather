# Weather 검증 완료 결과의 사전 구성과 읽기 분리

## 문제와 선택

직전 실제 네트워크 HTTP는 current39.286초/조건부16.188초/만료 뒤21.535초, daily9.094~16.944초였다. native profile의 current freeze31.514초·전체34.286초는 응답마다 승인 약3,300범위와 manifest를 재구성하는 비용을 보였다. 기존 승인 상태 캐시만으로는 이 구성을 제거하지 못했다.

완성된 current·daily 페이지와 무결성 proof를 API 메모리에 재사용한다. 요청별 인증과 provider 선택은 앞에서 유지하고, 동일 키 동시 생성은 합친다. 매 hit에도 snapshot ID/manifest digest/TTL, 세대·plan·현재 head 존재와 실제 사용 파일의 동일fd hash·크기·행 수를 검사한다. 원천 수집·snapshot·as_of는 유지하며 generated_at만 실제 wire 응답 생성 시각을 쓴다. gzip·ETag·cursor·DTO 검증을 유지하고 x-export-read로 generated/reused를 구분한다.

최대 수명은 승인 조회 시작으로부터60초이며 hit가 늘리지 않는다. 64MiB/16항목·개별8MiB의 직렬화 예산이고 RSS 보장은 아니다. 요청이 있는 cache miss만 새 결과를 생성하고 같은 키의 동시 요청은 하나로 합친다. 주기적 전체 재생성 worker는 실제 R4에서 만료 뒤33.48초 대기와 새 snapshot 참조 저장 timeout을 보였으므로 폐기했다. 요청이 없는 API가 전체 승인 집합을 계속 재구성하지 않는다. 최초·만료 뒤 요청의 지연은 남아 있으며1초 달성을 의미하지 않는다.

## 잠금과 불변 파일

기존 current는 긴 metadata 선택 전체 동안 공용 파일 reader guard를 잡았다. API의 불변 읽기는 metadata/manifest 구성을 이 guard 밖으로 옮긴다. 새 snapshot을 발급할 때만 guard 안에서 모든 파일의 일반파일/크기 존재를 재확인하고 snapshot·세대 참조를 함께 commit한다. 세대 참조는512개씩 INSERT하며 전체 순서와 중복·FK를 지킨다. SQL문만 나누고 TX·guard는 나누지 않으며 뒤 묶음의 FK 실패도 앞 묶음과 snapshot을 함께 rollback한다. 따라서 GC가 새 pin을 보기 전에 파일을 지우는 틈을 만들지 않는다. 기존 유효 snapshot 참조는 GC root로 유지되며, 실제 페이지는 열린 동일fd의 hash/count를 매번 확인한다. 현재 head 유실은 즉시 원래 재구성 경로로 보내 stale로 판정하고 세대/plan/파일 유실은 정상200/304로 숨기지 않는다. SDK 기본 reader의 guard는 바꾸지 않는다.

원본 전체 MV refresh·TTL의 무근거 연장·추정 index 삭제는 선택하지 않았다. 새 외부 캐시 서비스 없이 API 내 작은 준비 결과를 쓰며, 향후 공유 read-model이 필요하면 실제 재구성 CPU와 key별 수명 자료로 판단한다. API 원천/서비스 인증·schema0038·SDK/의존성·SQL5초·실제 UI/API 포트/키/env/CS/Transport는 변경하지 않는다.

## 검증과 원 실패

초기 WSL PG18과 Docker PG18 집중검증 각각85개와 후속86개가 통과했다. 최종 요청 기반 후보는 별도 원문으로 정산한다. 겹친72개·이전85개와 합산하지 않는다. 새 cache의 인증·gzip/ETag·60초 경계·동일 키 동시성·용량·refresh 실패·실파일 변조·GC 배타 보유 중 이미 pin된 페이지·새 pin commit의 짧은 guard·기존 GC 보존·다중 source/daily fallback을 포함한다. 원5FAIL/후속1FAIL과 Docker의 무관한 예보 fixture1FAIL을 보존했다. 즉시 반영 기대는 승인60초 뒤 실제 판정으로 검사하며 cache를 지우지 않는다. dense forecast 시험은 거절되는60격자만 구성해 정상 갱신되는 무관61격자의 known_at/첫 scope 순서에 좌우되던 입력을 분리했고 원 값·시각 비교는 유지했다. 예보 제품 로직은 변경하지 않았다.

최초 테스트 보조는 정본 테스트 DB 미지정으로 연결 timeout 뒤 실제 시험 전 종료했다. 첫 native 보조는 inspect0 뒤 후보 프로세스 시작 전에 부모1로 종료해 보존했다. 큰 프로그램 인자 대신 별도R2는 stdin으로 전달했다. R2는 실제 API와 같은 DB/파일의 격리된 ASGI 후보 과정이며 네트워크 HTTP/cold/p95가 아니다: current는5~7초 일시 경합503, daily 첫40.797초200·재사용3.927초200/2.241초304·두 seed 뒤75초 관측1.941초200이었다. source clock·항목 SHA는 같고 background 결과의 as_of만 실제 새 읽기 시각으로 바뀌었다. 아직 목표1초 미달이며 기존 긴 guard를 줄인 R3와 실제 새 이미지 수용은 별도 기록한다.

정본은 weather-aggressive-read-20261108/plan.json과 각 QA/native 원문이다. 현재 단계는 구현·집중검증 완료이며 새 이미지 배포/실제 네트워크 수용 전이다. 백업/복원은 사용자 지시로 생략했고 원 실패/UNKNOWN·원본·영구 통계·환경/키·다른 서비스를 보존한다.

## 실제 후보에서 폐기한 방법

격리 native R3는 새 pin admission guard 대기 timeout과 오래된 선택 lease 문제를 구분했다. 첫 페이지 miss는 새 승인 시점에서 시작하도록 고쳤다. R4는 current 첫46.199초503(참조 INSERT의5초 statement timeout), 이후 생성45.644초200, 재사용4.355초304, 두 seed 뒤75초 관측33.483초200이었다. daily는 첫17.166초/재사용2.746초200·1.842초304/만료 뒤23.410초200이다. R4 결과는 성능 수용이 아니며 모두 이전 API와 같은 DB를 쓰는 격리 과정이다. 운영 네트워크/cold/p95/코드 단독 개선율로 해석하지 않는다.

주기적 전체 재생성은 새 snapshot 전체 저장을 반복하며 CPU·DB 비용을 옮길 뿐이어서 제거했다. 공유 read-model·MV·비정규화는 커서 고정·공개 세대와 GC pin의 원자성까지 바꿔야 한다. 이번에는 그 계약을 우회하지 않고 요청별 반복 제거와 작은 FK 문장·짧은 파일 guard를 실제 반영하며 남은 최초 구성 병목을 수치로 공개한다.

## 완료 페이지의 삭제 무결성 조회 추가 개선

API d24908dc/이미지2a07628b를 실제 반영하고 Manager/원47일정·다른서비스·첫 부모 종료를 확인했다. 그러나 실제 HTTP는 current 첫35.524초/재사용14.139초/조건부11.749초503/만료 뒤46.665초, daily53.288·47.498·57.021·42.137초였다. 완료 페이지 재사용만으로 목표를 충족하지 못했고1분 수명 경계를 넘은 실패도 보존했다. 측정 중 별도 MCP 이미지 작업과 수집이 진행됐으므로 코드 단독 비율/p95/cold로 과장하지 않는다.

후속은 세대/불변plan/head의 존재3,305개를 매번 읽는 대신 같은 RR의 작은 삭제 변경 번호를 확인한다. 0039는 해당3표의 DELETE/TRUNCATE statement trigger와 파생 counter3행만 추가한다. 정상 발행 INSERT/UPDATE에는 trigger나 공용 counter 잠금이 없으며 원본 전체 MV refresh나 신규 FK를 추가하지 않는다. 삭제와 counter 갱신은 같은 TX이며 rollback도 함께다. DELETE0행도 보수적으로 무효화할 수 있다. 기존 삭제/정리의 허용 여부는 바꾸지 않고 prune도 켜지 않는다.

전체 승인/파일을 검산한 실제 읽기 시점의 변경 번호를 기억한다. hit는 snapshot ID/digest/TTL과3행 counter를 읽고 사용 파일의 동일FD hash/count는 계속 검산한다. plan/세대 삭제 변화는503·폐기, head 삭제 변화는 기존 재구성/stale 경로다. PG counter 유실·미설치를 벡터 생략 성공으로 처리하지 않으며 SQLite만 기존 벡터 조회로 fallback한다. 최초 생성 중 삭제도 확인한다. 승인 조회 이전의 limiter/worker 대기는1분 수명을 소비하지 않으며 이미 읽은 상태/캐시 hit의 수명은 연장하지 않는다.

최초·만료 뒤 전체 구성 비용은 여전히 별도다. 새0039/API 이미지는 집중 회귀와 공식 migration·실제 HTTP 뒤에만 수용 결과로 기록한다.


최종0039 후보는 WSL PG18·Docker PG18 각각126개 PASS다. 이전112·90·독립32개와 겹치므로 합산하지 않는다. 실제metadata설치/0039 up·down 원행보존·1초locktimeout 원자rollback·일반발행중counter잠금비영향·삭제/rollback·DROP/재생성/DISABLE/다른trigger·작업대기이전lease·원cursor/파일/GC/다중source를 함께 검사했다. 전용PG는정확CID/label로정산했다. Ruff F/I·diff 검사와키부재검산을수행하며 feature/Draft를유지한다.


0039/API44e2558b는 제한DDL·native3행·원47/환경/다른서비스·Manager와최초부모done으로반영됐다. 마지막생성중업무날짜/수명경계변화의예외가plain Exception이어서500이될수있는경로를보완했다. 같은경계를주입한격리PG18에서원44e2558b cache소스HTTP500/보정소스HTTP503을대조했다(자연운영500관측이아님). 내부예외를기존GuardTimeout계층으로바꾼1줄제품보정이며cache hit의재구성동작은유지한다. WSL/Docker초점19개씩PASS,SQL/DDL·SDK/웹변경0. 최초외부재현helper의conftest미적용/개발env누락은시험수집전실패로보존하고R2명시환경에서1PASS했다. 최종보정이미지는0039를재실행하지않고API만앞으로교체한다.
