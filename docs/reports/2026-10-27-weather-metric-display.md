# 2026-10-27 — 상세 기온·풍속의 사용자 표시

## 범위

기준은 Weather main `1d24d856fb9dd72eb52d89027c5172df91de95b0`이다.
기존 실제375px 상세 캡처의 `14.4 deg_c`, `5.5556 m/s`를 읽기 쉬운 표시로 바꾼다.
원본 `/var/tmp/weather173-live-accept-20261027-bc423775b1/375-detail.png`와
[당시 배포·지연 기록](2026-10-27-weather173-deployment-and-latency.md)은 수정하지 않았다.
이 후보는 새 운영 수용이나 마커 지연 개선을 뜻하지 않는다.

Common UI와 Weather에서 기상 단위 formatter를 확인했으나 공용 구현은 없었고,
기존 `weather-map.tsx`의 `valueFor`가 원시 숫자·단위를 이어 붙이고 있었다.
같은 파일 안의 작은 표시 helper를 기존 카드·최근 측정값·예보 미리보기에만 적용한다.
새 범용 라이브러리·export·의존성은 만들지 않는다.

- 온도 단위 `deg_c`·`°C`·`℃`는 화면에서 `℃`로 통일한다.
- 숫자 온도와 `m/s` 풍속만 최대 소수 한 자리로 표시한다. 예: `14.4 ℃`, `5.6 m/s`.
  정수와0에 불필요한 `.0`을 붙이지 않는다. 작은 비영 값이 반올림으로0이 되면 원값을 보존한다.
- 강수량과 다른 단위는 원래 정밀도를 유지한다. `0.04 mm`를0으로 만들지 않는다.
  `knots`, `K`, 알 수 없는 단위·null 단위도 임의 변환하지 않는다.
- `value_text`는 숫자로 강제 변환하지 않아 `unknown`, `<0.1`, `−2`, 문자열0을 보존한다.
  숫자0과 null을 구분하고 결측 표시는 기존 `—`를 유지한다.

DTO·backend machine unit·원래 값·관측/수집 시각·신선도·마커 온도/날씨 분류는 바꾸지 않는다.
최근 측정값의 작은 단위 태그를 포함해 마크업 구조·스타일·레이아웃·색상은 유지한다.

## 검증

기존 실제 카드 helper와 최근 측정/예보의 실제 JSX callback을 실행하는 회귀로
원본8실패/2통과를 재현한 뒤10개를 통과했다. 온도 단위 세 가지·음수·0·null·기호·미지원 단위·
작은 강수량과 풍속, 카드와 두 행의 표시 일치를 확인했다.

| 검증 | 결과 |
| --- | --- |
| WSL 프런트엔드 전체 | 266개 통과 |
| WSL 타입·린트·Next 검증 빌드 | 통과, 기존 `admin-shell.tsx:54` 경고1개 유지 |
| 로컬 WSL+Docker Node22.23.1 | 266개·타입·린트·Next 검증 빌드 통과, 같은 기존 경고1개 |
| backend 전체 재실행 | NOT_RUN, frontend 전용 수정이며 자동 CI 결과와 분리 |
| 새 브라우저·운영/API·제공자·DB·수집·운영 이미지 빌드/배포 | NOT_RUN/변경0 |

WSL 격리 경로는 `/var/tmp/codex-weather-metric-display-20261027`이다.
`metrics-red-final.log`, `frontend-tests.log`, `frontend-type.log`, `frontend-lint.log`,
`frontend-build.log`, `docker-final.log`를 보존한다. Docker는 로컬 Unix/x64 소켓의
네트워크 없는 소유 테스트 컨테이너만 사용하고 종료 후 회수한다.
실제 JSX를 테스트용 자식 배열로 실행한 검증은 브라우저 렌더링이나 실제 API 수용의 대체가 아니다.

정확 후보의 CI·두 독립 리뷰·머지·운영 적용은 다음 게이트다.
병행 Weather backend PR180·Transport 배포/철도/항공편 준비본은 변경하지 않는다.
