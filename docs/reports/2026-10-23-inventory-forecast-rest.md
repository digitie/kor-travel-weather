# 승인 inventory 예보와 전체 source REST 연결

최신 quota 밖의 승인 source도 frozen vector에 유지한다. provider별 실제 pre-HTTP roster와
마지막 승인 위치 합집합을 읽으며 artifact에서 선택 위치만 출력한다. 요청 때 원천 이력이나
전체 weather_current_values를 다시 스캔하지 않는다.

각 페이지는 동일 RR에서 최신 source intent/승인/좌표/정책과 신선도를 재확인한다.
원천 실패 뒤 이전 커서도 stale이며 정상 ETag 304로 실패를 숨기지 않는다.
known_at은 원천 시각이고 next_since는 공개 watermark다. 모든 페이지 수신 뒤 next_since를
다음 요청의 since로 적용한다. 410이면 첫 페이지부터 다시 받고 304는 같은 표현을 재사용한다.

현재 구현은 내부 예보 reader의 SDK inventory 선택과 KHOA quota 회전 회귀다.
전체 source REST/중기 두 part 조합·W5 eligibility·운영 비용/GC gate는 아직 후속이다.
