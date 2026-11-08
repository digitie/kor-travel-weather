# 남은 조회 지연과 대표 카드 겹침

## 2026-11-08 — 남은 조회 지연·대표 카드 겹침 수정

실제 프로파일에서 current 렌더44.85초·freeze37.81초와 캐시 무결성 JOIN의SQL57014를 관측했다. 같은 RR의 PK두조회로 같은 존재 검증을 유지하고, 비압축 plan전체8MiB가 증명된 때만1024행 fetch를 허용하며 그 외128행을 유지한다. snapshot 참조는 순서·FK·TX를 유지한단일UNNEST INSERT로 저장한다. 후보 렌더30.49초 관측 뒤 느린ref크기예비조회는제거했다. WSL/DockerPG18최종54개씩통과,SQL5초·상태60초·원본/파일검증은보존한다. UI도 원좌표/표시값/특보·키보드를보존한대표pill충돌분리와스크롤목록을수정하고WSL/Docker32개씩·타입/린트·28대표Firefoxfixture를검산했다. 아직 새 API/UI 이미지 배포 전이다.

실제 새 이미지·Manager/일정·HTTP·n150 PC/모바일 결과는 배포 뒤 별도로 정산한다. 기존 실패와 속도 목표 미달은 유지한다. 원장 weather-transport-remains-20261108/plan.json, UI 원문 fuel-region-overlap-20261108/manifest.json. 새 리뷰/SDK설치/백업복원 게이트는 추가하지 않는다.
