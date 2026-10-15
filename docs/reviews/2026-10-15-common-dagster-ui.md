# 공통 Dagster UI 최신화

운영 live UI 검사에서 weather의 실행 검색 입력이 없어 검색·빈 결과·초기화 검증이 실패했다.
Geo에서 사용하는 공통 UI dev.6를 동일하게 설치해 검색, 페이지 이동, 실행 상세와 제한된 행 렌더링을 공유한다.
소비 저장소에 공통 컴포넌트의 복사본을 만들지 않는다.

- Common 소스: `091d4fe1dda8eacf43b453ad39b4f2e42e9e80fc` (UI 내용은 main `efcd671207d7f9e2fb01251bab6854a8d543279a`와 같다).
- vendor: `kor-travel-ui-0.1.0-dev.6.tgz`
- SHA-256: `e4945d01d9eb89ed505a95b551899fd0ecf41be66c9ee6b76246701350447e6d`
- API·인증 계약 변경 없음. 실행 이력 조회 한도와 공통 UI 페이지 크기를 유지해 전체 행을 DOM에 쌓지 않는다.
- Geo 자원·배치·동시성 튜닝값을 변경하지 않는다.

공개 Dagster URL은 runtime 환경값만으로 Next.js 번들을 바꿀 수 없어 Docker 빌드 인자로도 전달한다.
Compose의 build/runtime 값이 같고 build 전에 적용되는지 회귀 검사한다.

## 검증 상태

- 변경 전 live UI: 전체 79 PASS / 5 FAIL. 이 앱의 실행 검색 부재가 실제 실패 중 하나다.
- 로컬 관리자 UI: type-check·lint PASS, 단위 테스트 65 PASS, production build PASS. 51개 실행 중 첫 페이지 50행만 렌더링하는 vendor 계약도 통과했다.
- 기존 Python 소스 전체 458 PASS·Ruff PASS. 공통 UI 후보의 두 독립 리뷰와 소스 CI PASS, ARM64 관리자 UI 컨테이너 검사 PASS, 운영 두 UI 교체와 로그인 HTTP 200을 확인했다. 아래 인증 보강 후보의 최종 CI·ARM64 배포·재리뷰·live 재검증은 아직 NOT_RUN이다.
- 운영 조회의 명시적 화면 이동으로 취소된 요청과 안정 화면의 실제 오류를 구분한 뒤 모든 필수 사례를 재검증한다.
- 실패 결과를 통과로 바꾸거나 검색 검사를 제외하지 않는다. 최종 검증 뒤 이 문서를 갱신한다.


## live 검사에서 발견한 인증 저장소 경계 보강

실제 R4 live UI는 Geo·Weather·Transport 합계 85 PASS / 2 FAIL이었다.
Weather의 일반 API 401과 Transport 지도 조회의 미분류 요청 취소를 실패로 보존한다.
Dagster 검색·현재 plane 링크·503/네트워크 장애 후 복구·다섯 화면 폭은 통과했지만
이 결과를 전체 PASS로 바꾸거나 실패 항목을 제외하지 않는다.

Weather의 저장소 조회는 실제 폐기와 timeout/비정상 응답을 같은 boolean으로 합쳐
유효한 cookie도 401로 처리할 수 있었다. 로컬 장애 재현으로 이 경로를 확인했다.
반면 후속 운영 읽기 표본은 순차 6건·동시 8건 모두 200이고, 직접 backend 조회도
정상이었으므로 실제 R4의 간헐 401이 timeout 때문이었다고 확정하지 않는다.

저장소 판정을 active/revoked/unavailable로 구분한다. unavailable은 503·no-store와
재시도 안내를 반환하고 cookie를 유지하며 접근은 차단한다. 실제 invalid/revoked만
401 또는 로그인 이동을 사용한다. 읽기 제한은 기존 쓰기와 같은 5초로 유한하게 두고
자동 재시도·권한 허용 캐시는 추가하지 않는다. 손상된 200 응답은 허용하지 않고,
로그아웃도 엄격한 revoked=true 응답을 받은 뒤 cookie와 로컬 세션을 폐기한다.
정상 DB PK 조회·Python API·DTO·Geo 튜닝은 변경하지 않는다.

현재 frontend 96 PASS·type-check·lint·production build PASS다.
추가 31건은 응답 손상·지연·상한·같은 cookie 복구·실제 폐기·로그아웃 ACK와
교차 origin 차단을 확인한다. 첫 검사 중 2건은 테스트용 공개 origin 설정 누락으로
403을 받아 실패했고 fixture를 완성한 뒤 전체 96건을 통과했다.
최종 후보의 두 독립 수정 후 리뷰·운영 배포·live 재검증을 이어서 기록한다.
