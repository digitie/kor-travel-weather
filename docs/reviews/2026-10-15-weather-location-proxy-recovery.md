# Weather 위치 목록 BFF 전송 실패 복구

## 문제와 변경

운영 UI에서 공개 위치 목록 GET이 약9.76초 뒤 HTTP500으로 끝났고,
같은 시각 BFF 로그는 `TypeError: fetch failed`와 `UND_ERR_SOCKET`이었다.
상대측 socket이 닫힌 전송 실패 유형을 확인했지만, 연결 종료의 더 깊은
원인까지 확정한 것은 아니다. 기존 BFF는 fetch 예외를 처리하지 않아
Next500으로 전파했다.

정확한 `GET /v1/weather/locations`와
`GET /v1/admin/provider-credentials`만 응답 헤더 전의
`UND_ERR_SOCKET`/`ECONNRESET`/`EPIPE`가 TypeError의 cause로 확인될 때
최대2회 시도한다. 쿼리·헤더·no-store 의미를 그대로 유지한다.
HTTP4xx/5xx는 원래 body/status로 전달하며 재시도하지 않는다.
POST/PATCH/PUT/DELETE/HEAD와 다른 GET에는 새 재시도 정책을 적용하지 않는다.

허용한 두 GET의 헤더 수신 총예산은20초이며 재시도마다 초기화하지 않는다.
이는 관측된 약9.76초 실패에 대해 최대 두 시도를 약10초씩 수용하는
초기 복구 예산이다. 정상 응답의 p95/p99 분포를 측정해 최적화한 값이나
연결 종료의 원인을 해결했다는 의미는 아니다. 기존 frontend 요청에는
별도의 전체 timeout이 없으므로 허용한 두 읽기에 새 유한 경계를 둔다.
브라우저 취소는 이 예산보다 우선하며 timer callback이 밀려도
monotonic 시각을 다시 확인한다.

헤더를 받은 뒤에는 timer를 해제한다. 기존 long GET/export·관리 write와
응답 stream 전체에20초 제한을 새로 적용하지 않는다. body는 버퍼로
누적하지 않고 backpressure에 따라 전달하며 완료·실패·소비자/요청 취소에서
upstream reader를 정리한다. 일부 body가 전달된 뒤 실패하면 재전송하지 않는다.

복구되지 않는 헤더 전 전송 실패는 일반503, 읽기 헤더 예산 소진은504,
호출자 취소는499로 끝낸다. 오류 body는 no-store이며 빈 위치 목록으로
대체하지 않는다. 원래 error/cause·URL·주소·키·cookie를 기록하지 않고
고정 allowlist category와 attempt만 남긴다. 인증 middleware와 durable
revocation/rate-limit의 fail-closed 계약은 변경하지 않았다.

## 검증

- 원본a7 소스에서 socket 재시도·최종503·취소3개 회귀를 실행해3FAIL을
  재현했다. 나머지 선택 제외 테스트를 실패 건수에 합산하지 않았다.
- 초기 수정 후 위치 BFF 회귀24개를 포함한 frontend 전체156개 PASS.
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


## 추가 취소 회귀 보강

독립 리뷰에서 body queue가 찬 뒤 소비가 멈추면 caller 취소만으로
reader 잠금 해제가 끝나지 않는 경로를 발견했다. 추가 소비 없이 취소하는
회귀를 기존 구현에 실행해1FAIL을 직접 확인했고, 취소 자체가 reader와
listener를 종결하도록 수정했다. 진행 중 pull 및 downstream/caller의
동시 취소 회귀도 추가했다. upstream cancel은 한 번이며 자동 재시도는 없다.

최종 위치 BFF 회귀27개와 frontend 전체159개, type-check·lint·production
build가 통과했다. 최초 리뷰 원문과 실패 결과는 보존하며 이 수정의 독립
재검토 및 운영 검증은 별도로 수행한다.

## 공급자 키 상태 읽기 보강

후속 실제 Weather UI R10은 31 PASS / 1 FAIL이었다. 공급자 키 상태 GET의
비주입503과 페이지 이동 중 두 요청의 ERR_ABORTED가 마지막 검증에서 검출됐다.
해당503의 body/전송 category는 저장되지 않아 원인을 socket 종료로 확정할 수 없다.
독립적인 후속 같은 GET 세 번은 모두200(1.200/0.978/0.550초)이었지만, 이를 기존
실패의 수정 또는 전체 live PASS로 집계하지 않는다. 실패 원문은 보존한다.

공급자 키 상태는 키 원문을 반환하지 않는 읽기 전용 API다. 이 정확한 경로에도
기존 위치 GET과 같은 제한된 **헤더 전 전송 장애** 복구를 적용한다. 이미 받은
HTTP503/401/403/429는 재시도하지 않는다. 관리 write·다른 GET/export·body 오류는
계속 제외한다. 이 변경은 미확정503 원인의 해결을 주장하지 않는다.

이전 소스에서 공급자 상태 socket 복구 회귀는1 FAIL/기존27 PASS였다. 수정 후
관련33개를 포함한 frontend 전체173개가 통과했다. 공급자 상태의 실제 HTTP 오류
보존과 두 시도 합계20초 예산도 확인했다. 이 후속 delta의 독립 리뷰·배포와
UI 재검증은 별도 gate이며, 페이지 이동 취소의 설명 가능성도 추가로 확인한다.

## 빌드 worker 수 제한

동일 로컬 ARM64 빌드에서 BuildKit의 2CPU/2GiB 제한만으로 Next 정적 빌드의
worker 수가 줄지 않았다. 실제 소유 builder의 Docker process 목록에는
`jest-worker/processChild.js` 15개가 있었고, Docker stats는 약1.99GiB/2GiB였다.
컴파일 후 page data 수집 단계에 머물러 소유 로컬 build client를 정상 취소했다.
이 결과를 성공이나 OOM으로 집계하지 않는다. 운영 서비스는 변경하지 않았다.

설치된 Next의 `getNumberOfWorkers`는 명시한 `experimental.cpus`를 우선한다.
이를2로 지정해 정적 빌드 worker를 제한한다. API/Dagster의 작업 동시성이나
운영 HTTP worker 설정을 바꾸는 항목이 아니다. 기존 실패 로그와 source c2e6은
보존하며, 실제 새 ARM64 빌드의 worker/메모리·완료 여부는 후속 실측으로 확인한다.
