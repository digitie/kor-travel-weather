# 관리자 로그인 rate-limit 장애 관측

실제 UI R17과 R18에서 잘못된 계정 요청이 간헐적으로503을 반환했다. R18의
bounded 응답은 rate-limit 서비스 unavailable 문구였지만 check와 failure가 같은
외부 문구여서 어느 내부 요청이 실패했는지 알 수 없었다. 현재 API 로그만으로
timeout·transport·HTTP오류·잘못된 ACK를 확정하지 않는다.

lib/session.ts의 기존 호출이 unavailable로 판정될 때 JSON 한 줄을 server stderr에
남긴다. event, action(check/failure/success), reason(configuration/timeout/transport/
non-200/invalid-reply), upstream_status, elapsed_ms만 기록한다. 계정·password·token·
bucket hash·URL·응답 body·exception message/cause는 기록하지 않는다. 로그 자체가
실패해도 원래 unavailable 결과를 유지한다.

외부 응답·엄격 ACK·action별5초 abort·fetch1회·retry0·fail-closed 정책은 그대로다.
관측 로그 추가를 인증 가용성 개선이나 실제503 원인 해결로 표현하지 않는다.
로그가 없는 정상 ACK도 보존한다.

신규15회귀는3개action의non-200/transport/malformed200/5초abort와비밀값배제,
configuration/관측실패/정상quiet를 검증한다. 기존51개 focused(신규15+기존36)는
통과했고, immutable이전소스의같은신규회귀는13FAIL/2PASS로관측누락을재현했다.
브라우저R18은전체34개중32PASS/2FAIL이었으며 이 source의실배포/E2E결과가아니다.
운영 적용뒤 새로운503이발생하면 이 분류와 API 시각을 대조해 원인을 확인해야 한다.

## 배포 UI 기준선 통합

API·수집기용 브랜치에서 파생하면서 현재 UI969cf51370e0893908a32b5736c0d1946138f6ec의
일부 파일이 빠져 있음을 이미지 빌드 전에 확인했다. 실제 배포된 해당 기준선의 위치·공급자 상태
읽기 전송 복구와 stream 취소 회수, 실패 상세 실행 식별자, 빌드 worker2 및 관련 회귀를
파일 바이트 그대로 통합했다. 로그인 진단은 그 기준선 위에 추가한다. 이전155개 검증을
통합된 전체 UI의 새 검증으로 간주하지 않으며 통합 뒤 프런트엔드 확인을 다시 수행한다.
