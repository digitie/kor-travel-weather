# 비동기 호출과 TPS 제어

AirKoreaClient는 기존 비동기 구현을 사용하며 별도 동기 클라이언트와 aio 생성 facade를
제거했다. 기존 서비스 메서드·typed 모델·endpoint·파라미터와 페이지 종료 조건은 유지한다.
네트워크 조회와 run_debug_method는 await, 페이지 순회는 async for를 사용한다.
HTTP session은 async get만 허용하며 동기 함수를 실행하기 전에 거절한다.
CLI와 Streamlit은 외곽에서 asyncio.run을 한 번 호출해 생성·조회·종료를 같은 루프에서 한다.

AsyncTokenBucket은 동일한 공통 구현이다. max_rps는 유한한 양수, capacity는 유한한
1 이상의 수를 받으며 bool은 거절한다. 기본값은 5 TPS, 용량은 max(1, max_rps)다.
초기 burst 이후 monotonic 시간에 따라 충전한다. FIFO 대기 중 취소된 호출은 토큰을
소비하지 않는다. 같은 버킷을 다른 이벤트 루프에 재사용하면 RuntimeError를 낸다.

모든 서비스가 클라이언트 내부의 한 버킷을 공유한다. rate_limiter 인자로 같은 인스턴스를
주입하면 여러 클라이언트도 한 quota를 공유하며 이 인자가 max_rps보다 우선한다.
재시도와 redirect마다 토큰을 추가로 획득한다. HTTPX next_request로 redirect 메서드,
쿠키, 인증 헤더 제거를 유지하며 마지막 응답은 기존 요청 API와 같은 형태로 반환한다.
사용자가 주입한 DigestAuth/transport 내부 재송신은 버킷 제어 범위 밖이다.

좌표 변환, 카탈로그, 파싱, fixture 저장처럼 네트워크 없는 유틸리티는 일반 함수다.
외부에서 주입한 HTTPX session의 종료는 호출자가 책임진다.
