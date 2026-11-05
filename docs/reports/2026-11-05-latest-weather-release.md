# 최신 Weather 통합 이미지 배포

## 반영 범위

2026-11-05 사용자의 최신 코드 이미지 배포 요청에 따라, 요청 시점의 `main`
`240f3427770f4932d0d2edd144cb93888328afcf`를 기존 배포 기능이 있는 브랜치와 합쳤다.
제품 소스는 `9acc40906d421ab58712838605649af10496cc2b`다.

- PR200: 오래된 위치별 승인 만료와 export meta의 실제 유효 범위 반영.
- PR201: 행정구역 근거가 있는 공식 특보 발표 이벤트 API·수집·화면 표시.
- PR202: export 선택 계획 재사용, 배열 매개변수와 소유자 조회 비용 개선.
- PR203: 승격의 배타 guard를 최종 CAS로 축소하고 파일 재검사 유지.
- 기존 PR193 상세 날씨 UI와 PR196·198·199 성능·응답·snapshot projection 변경 유지.

`0036_snapshot_kind_projection`와 `0036_regional_alert_events`는 독립 이력으로 보존한다.
추가한 `0037_release_merge`는 두 부모를 연결하는 무변경 병합 revision이다.
기존 운영 DB에서 특보 이벤트 테이블만 추가하며 downgrade·stamp·행 삭제는 하지 않는다.
이는 두 기능의 병합이며 서로 다른 revision의 이름을 덮어쓰는 방식이 아니다.

## 검증과 원 실패

WSL의 격리 PostgreSQL18에서 최신 head migration·특보 저장/정규화·export head/inventory
85개와 실제 특보 HTTP 계약16개를 통과했다. 시험 DB와 운영 DB는 분리했다.
병합 중 재발한 0035 전용 시험의 revision 기대값은 실제 upgrade target인 0035로 맞췄다.
이 시험 자료 변경으로 제품 이미지를 다시 만들지 않는다.

첫 API 이미지는 실제 Docker 빌드0이다. 후속 격리 검사에 앱의 PYTHONPATH가 빠져 최초
빌드 부모는1로 종료했다. 다음 격리 검사는 API package의 자동 앱 생성이 개발 DB에
접속해 실패했다. 원 부모1·종료·cgroup 부재를 보존하고 이미지 생성 성공과 구분한다.
검사는 DB 요청 없는 core import·마이그레이션 head·리소스 존재 확인으로 정정했다.
실제 앱 기동과 인증·DB는 교체 뒤 실제 HTTP로 확인한다.
동일 API 이미지의 재빌드는 하지 않았다.

비공개 VWorld 입력은 BuildKit secret으로 전달하고 실제 현재 입력과 일치 여부만 확인한다.
키 값은 문서·Git·출력에 기록하지 않는다. 사용자 지시에 따라 백업·복원은 생략한다.

## 실제 배포 상태

세 ARM64 이미지 빌드는 모두 실제 Docker0이며 마지막 빌드 Root의 첫 Invocation
`7b807a25c9dd4a01b65853f12231934f`는 journal `done`, MainPID0·cgroup 부재다.
API 이미지는 `99bdacb984e5`, 수집기 이미지는 `1731ff3c7376`, 웹 이미지는 `508a4383eea8`이다.

웹 CID `b1d95fc7e3b2`(12205)와 API CID `094b8338dc6b`(12201)를 실제 교체했다.
같은 DB의 새 revision은 `0037_release_merge`이며 native Settings의 VWorld 키 일치도 확인했다.
기존 테이블을 변경하지 않는 특보 테이블 추가와 API 반영을 먼저 끝냈다.
운영 수집기는 기존 작업의 자연 종료를 기다리는 중이다. 새 수집기·일정 복원·최종 Manager
정산·배포 부모 실제 종료는 아직 완료로 기록하지 않는다.

실제 API health·version·지역 특보 이벤트·export status·500행 위치 응답은 모두200이다.
같은 관측의 조건부 위치 요청은 `EXPORT_TEMPORARILY_UNAVAILABLE`503/Retry-After5였다.
이 원문을 보존하고 조건부304 성공이나 전국 성능 개선으로 바꾸지 않는다.
이때 수집기는 아직 기존 코드이므로 publisher guard 개선의 최종 수용과 구분한다.

Firefox157 PC1440·모바일375에서 서울 상세의 현재 날씨·내일 최저3/최고9도·시간별 그래프
24시간·지도 배경을 실제 확인했다. 두 화면 overflow0, 비어 있는 특보 설명0,
logout200·소유 브라우저 자식 종료·잔여0이다. 미세먼지 등 원천에서 없는 항목은 미제공으로
남으며 이번 배포를 데이터 충족으로 과장하지 않는다. 화면 원문은 `live-ui/results.json`,
`overview-ready-1440.png`, `overview-ready-375.png`, `hourly-1440.png`, `hourly-375.png`다.

0035 전용 migration 시험 기대값 수정도 별도1개 시험과 ruff를 통과했다.
Python 시험은 서로 다른85+16+1개다. 웹 Next 컴파일·타입 검사·정적 페이지15개 생성도 통과했다.

실행 중인 단일 배포 Root는 `weather-latest-deploy-20261105-4b58099995.service`, 최초 Invocation
`2219cd940ec54c85b7b9db1e9c5ffb4a`다. 새 독립 검토·백업·복원·추가 운영 Root를 만들지 않는다.
정상 Manager G→R/FD9를 유지하며 Weather 원26일정만 일시 정지했다. 기존 수집이 끝나면
수집기를 교체하고 원26상태 및 새 지역 특보 일정의 기본 RUNNING을 명시 정산한다.
다른 서비스의 일정·환경·컨테이너를 변경하지 않는다.

정본은 Git 밖 `C:/Users/digit/.codex/tmp/weather-latest-release-20261105/checkpoint.json`과
각 build/deployment 결과다. 원 실패와 진행 중인 부모를 성공으로 승격하지 않는다.
