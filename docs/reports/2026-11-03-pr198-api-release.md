# 2026-11-03 PR198 API 새 이미지 운영 반영

PR198의 export 읽기 경합 분류와 승인 inventory 조회 축소를 기존 PR193 상세 UI·PR196 통합 소스 위에 반영했다. 변경되는 API만 새 이미지로 교체했다. API의 공식 PYTHONPATH와 실제 포트를 보존했다.

- 제품 소스: `643bb34e094f474486ef872a7e6fdbe5c9dfbcaa` · PR198 head `3ccea9d506bbe8778be5109f1b57c8f3732492d5` 포함
- 실제 API 이미지: `sha256:b4da586588b487f7d4d3aa7aba2bcc4b45bf92a50b352bbc43dd8bb71648dd13`
- 실제 API CID: `ad158be9d05ab3ea40903824f856db3ba9aae31312c3b1ebc0ea8bf25aa68a86` · 포트12201 · running/healthy
- Manager current: `/srv/ktdm-releases/weather-adopt-20261102T214523Z-a9d9df8f43bd4788a515fa6d9d247f20` · adopt/verify/show healthy
- 배포 최초 Invocation: `6d47c5aa06a74343baa0979268d12173` · 실제0/정상 종료, Main PID0, cgroup 부재, 소유 자식 종료
- 웹·코드서버는 기존 실제 CID와 이미지를 유지했다. Transport·다른 서비스·공용 daemon과 비공개 환경 파일도 보존했다.
- 수집 일정 조작·DB migration·백업·복원·수동 재수집·prune를 실행하지 않았다. 백업·복원은 사용자 지시로 생략했다.
- VWorld 실효 값 일치와 Git 이력·문서에 실제 키 값이 없음을 확인했다. 값을 출력하지 않았다.

## 반영한 동작과 검증

- SQLSTATE의 일시 경합·연결 끊김·pool 대기·artifact guard 대기·상태 재투영 대기는503 `EXPORT_TEMPORARILY_UNAVAILABLE`와 `Retry-After: 5`다. 실제 export 부재나 DB 인증·디스크 결함은 기존 `EXPORT_NOT_READY`이며 재시도 헤더를 붙이지 않는다.
- 승인 inventory의 위치별 attempt·plan 반복 join을 줄이고 plan을 attempt당 한 번 읽는다. 원본·이력을 삭제하지 않는다.
- 원 PR의 CI41493243921은 성공했다. 새 통합 이미지의 실제 운영 컨테이너에서 순수 응답14사례를 확인했다. 라우터 모듈만 로드해 앱 자동 생성의 DB 연결과 분리했으며 실제 HTTP 프로세스의 인증·DB 상태는 아래 응답으로 따로 확인했다.
- 실제 인증된 읽기 요청은 `/health`200, export `/status`200·15항목이었다. 요청2회·재시도0이며 원문 토큰·URL·payload는 기록하지 않았다. 모든 export 자원의 전체 전국 데이터 공급·장시간 성능까지 수용한 것은 아니다.
- 웹 source·의존성·migration tree가 바뀌지 않아 기존 웹을 재빌드하거나 이미 완료한 상세 UI 전수 검사를 반복하지 않았다.

## 원 실패와 범위

실제 Docker 이미지 빌드는0이었다. 첫 build 부모는 `python -I`가 API의 공식 PYTHONPATH를 제거한 격리 검증에서1로 종료했다. 별도 격리 검증도 운영 토큰/DB가 없는 상태에서 package의 앱 자동 생성을 실행해 실패했다. 세 원 unit의 실패와 실제 종료를 보존했고 재시작·재신호하지 않았다. 이를 빌드·검증 전체 성공으로 바꾸지 않는다. 이미 성공한 원 PR CI와 실제 이미지 빌드에 근거해 API를 교체한 뒤 운영 환경과 분리된14응답·실제 인증 HTTP를 검증했다. 제품 소스를 수정하거나 같은 이미지를 반복 빌드하지 않았다.

실제 원문은 Git 밖 `weather-pr198-release-20261103` 원장에 보관했다. 이번 작업으로 기존 미제공 지표·공식 특보 구역 자료·지역 라벨 겹침·Map 쪽 장시간 소비 등의 별도 과제를 완료 처리하지 않는다.
