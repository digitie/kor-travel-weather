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
