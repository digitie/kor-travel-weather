# Weather 위치 목록 BFF 전송 실패 복구

## 문제와 변경

운영 UI에서 공개 위치 목록 GET이 약9.76초 뒤 HTTP500으로 끝났고,
같은 시각 BFF 로그는 `TypeError: fetch failed`와 `UND_ERR_SOCKET`이었다.
상대측 socket이 닫힌 전송 실패 유형을 확인했지만, 연결 종료의 더 깊은
원인까지 확정한 것은 아니다. 기존 BFF는 fetch 예외를 처리하지 않아
Next500으로 전파했다.

정확한 `GET /v1/weather/locations`만 응답 헤더 전의
`UND_ERR_SOCKET`/`ECONNRESET`/`EPIPE`가 TypeError의 cause로 확인될 때
최대2회 시도한다. 쿼리·헤더·no-store 의미를 그대로 유지한다.
HTTP4xx/5xx는 원래 body/status로 전달하며 재시도하지 않는다.
POST/PATCH/PUT/DELETE/HEAD와 다른 GET에는 새 재시도 정책을 적용하지 않는다.

위치 GET의 헤더 수신 총예산은20초이며 재시도마다 초기화하지 않는다.
이는 관측된 약9.76초 실패에 대해 최대 두 시도를 약10초씩 수용하는
초기 복구 예산이다. 정상 응답의 p95/p99 분포를 측정해 최적화한 값이나
연결 종료의 원인을 해결했다는 의미는 아니다. 기존 frontend 요청에는
별도의 전체 timeout이 없으므로 위치 목록에만 새 유한 경계를 둔다.
브라우저 취소는 이 예산보다 우선하며 timer callback이 밀려도
monotonic 시각을 다시 확인한다.

헤더를 받은 뒤에는 timer를 해제한다. 기존 long GET/export·관리 write와
응답 stream 전체에20초 제한을 새로 적용하지 않는다. body는 버퍼로
누적하지 않고 backpressure에 따라 전달하며 완료·실패·소비자/요청 취소에서
upstream reader를 정리한다. 일부 body가 전달된 뒤 실패하면 재전송하지 않는다.

복구되지 않는 헤더 전 전송 실패는 일반503, 위치 헤더 예산 소진은504,
호출자 취소는499로 끝낸다. 오류 body는 no-store이며 빈 위치 목록으로
대체하지 않는다. 원래 error/cause·URL·주소·키·cookie를 기록하지 않고
고정 allowlist category와 attempt만 남긴다. 인증 middleware와 durable
revocation/rate-limit의 fail-closed 계약은 변경하지 않았다.

## 검증

- 원본a7 소스에서 socket 재시도·최종503·취소3개 회귀를 실행해3FAIL을
  재현했다. 나머지 선택 제외 테스트를 실패 건수에 합산하지 않았다.
- 수정 후 위치 BFF 회귀24개를 포함한 frontend 전체156개 PASS.
- type-check, lint, production build PASS.
- 재시도 총예산이20초에서 초기화되지 않음, event-loop timer 지연,
  취소와 첫 socket 실패의 경쟁, 대기 중/헤더 뒤 취소, HTTP 오류 보존,
  모든 write/HEAD 및 다른 GET의 재시도 제외를 확인했다.
- 헤더 뒤 stream 오류는 재시도하지 않고 reader를 해제하며,
  다른 GET/write 및 헤더 이후 느린 body에 새20초 제한을 부과하지 않음을 확인했다.

이 증거는 로컬 소스·mock fetch 검증이다. 이 변경의 운영 배포와
live UI E2E, 두 독립 코드 리뷰 및 PR merge는 별도 후속 단계다.
기존 운영a7의 E2E 결과를 이 변경의 live 검증으로 사용하지 않는다.
Python/API DTO 변경이 없어 OpenAPI export 변경은 없다.
