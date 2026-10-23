# Map service API 첫 단계: 계약·인증 경계

제품 HEAD: `735d90d66aa6271171ce9d22f592be1d17cbb6d0`. W-MAP-01의 계약·프록시 경계이며 W1/W2/W4 실제 데이터 공개 완료가 아니다. 운영 배포 전이다.

- 기존 `require_admin`/`x-admin-token`/OpenAPI `AdminToken`을 재사용한다. `/v1/service/exports/{locations,current,status}`의 DTO·TS·OpenAPI를 추가하고 인증 후 `EXPORT_NOT_READY` 503을 반환한다. 완결된 성공 head가 없으므로 DB/provider 읽기·빈200·304는 없다.
- 웹 catchall은 service 경로와 재해석 가능한 percent/dot/separator/control 경로를 토큰 주입·URL 생성 전에404로 차단한다. 기존 canonical weather/admin과 query 전달은 유지한다.
- 전용 PostgreSQL 전체738 PASS(916.21초), frontend243 PASS·type-check·production build·Ruff PASS. 신규 API11개·프록시53개를 포함한다. 프록시 재현18 FAIL/35 PASS 뒤 수정해53 PASS였다. 제품 CI Python/frontend SUCCESS, James/Popper 독립 제품 리뷰의 열린P0/P1/P2 없음.
- 격리 PostgreSQL·실제 uvicorn API·production Next build·실제 Chromium에서 API3범주(각401/인증503)·UI11개 PASS. 실제 로그인·위치 화면·390/1440px·지원6method의 service/인코딩/정규화별36요청404·기존public/admin BFF를 확인했다. 투명 HTTP 기록 프록시의 service upstream0회·JS오류0이다. API/브라우저 route mock은 없고 위치 스크린샷도 시각 확인했다.
- 이 검증은 로컬 production build이며 운영 HTTPS 수용/배포가 아니다. 첫 브라우저 실행은 옛 이미지 entrypoint가 검사 전 migration을 실행해 실패했다. 두 번째의 쓰기 검사는 Origin 누락으로 middleware403이었고, 같은 origin을 넣어 다시 통과했다. 실패 로그는 보존했다.
- 초기 Python 실행도 명시 import path와 전용DB 분리 전 옛 비파티션 개발 DB에서 실패했다. 그 DB를 수정하지 않고 별도UUID 테스트DB로 전체를 다시 통과했다.

다음은 W-MAP-02의 canonical accepted receipt·scope 완결/terminal 성공 승격·독립 성공 head다. 이후 W1/W2/W4 실제 값과 W3 snapshot·ETag/gzip/cursor/checkpoint를 연결한다. W5는 승인된 실제 KMA실황을 포함해 별도 사전 집계로 구현한다. 이 첫 단계만으로 전체 API·CPU/RAM 개선을 완료 처리하지 않는다. 실제 데이터 공개 단계의 운영 배포 전에 Map 담당에게 응답 예시·PR을 전달한다.
