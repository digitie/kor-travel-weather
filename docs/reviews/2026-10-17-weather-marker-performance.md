# 지도 marker 처리 비용 개선 검증

대상 제품 source는 통합 58230797b1b3c932537b28fc8ca5139e8075c49e다.
PR105는 frontend worker queue와 marker memo, PR106은 repository의 winner hydration을 소유한다.
이 문서는 검증 당시의 상태이며 운영 UI 통과나 머지·배포 완료를 선기재하지 않는다.

## 처리 방식

- 기존 60개·최대 3개 동시 요청을 유지하면서 완료한 worker가 다음 chunk를 시작한다.
  느린 요청 하나 때문에 다른 두 슬롯이 쉬던 wave barrier를 제거한다.
  R24의 전체 692개 위치와 12개 요청은 줄이지 않았다.
- 지도 모델은 위치·선택·summary가 바뀔 때만 다시 만들어 무관한 상세 상태 렌더에서 GeoJSON 재구성을 줄인다.
- 백엔드는 기존 top4/observed 우선/fallback 및 Python 비교를 유지한다.
  후보 다섯 필드로 승자를 고른 뒤 정확한 (value_id, known_at) 쌍만 전체 ORM/raw로 읽는다.
  경보 raw 처리, DTO, lineage 및 generic read 계약은 보존한다.

## 독립 리뷰

제품별 작성자를 제외한 두 리뷰를 받았다.
frontend는 D와 C, backend는 A와 C가 전체 변경과 관련 호출 경로를 읽고 독립 모형을 실행했다.
열린 P0/P1/P2는 0이다. 작성자 검증을 독립 리뷰 수로 합산하지 않았다.
원문은 evidence의 2026-10-17-weather-marker-{queue-d,winner-a,combined-c}.md에 그대로 보존했다.

후보가 있으면 SQL 왕복 한 번이 추가된다. READ COMMITTED의 두 조회 사이에 정확한 승자가
retention으로 삭제되면 해당 값은 누락되며 다른 partition이나 차순위로 대체하지 않는다.
UI 성공별 publication은 692개 기준 최대 12회로 기존 wave당 4회보다 잦을 수 있다.
실제 CPU/렌더 비용 및 UI 완주는 별도의 운영 gate다.

## 실제 Odroid 읽기 전용 비교

같은 60개 위치·고정 기준 시각을 기존→후보→기존→후보 순서로 실행했다.
DB 쓰기·DDL·컨테이너 변경은 없었고 모든 기존 컨테이너 fingerprint/PID/ticks가 유지됐다.
cProfile을 켠 단일 Python 진단 프로세스의 수치이며 HTTP 전체 지연이나 host CPU 점유율 수치가 아니다.

| 구현 | CPU 시간(ms) | wall 시간(ms) |
| --- | --- | --- |
| 기존 1 | 2760.790 | 3264.804 |
| 후보 1 | 2139.270 | 2564.744 |
| 기존 2 | 2534.018 | 3021.899 |
| 후보 2 | 2107.663 | 2545.566 |

네 번 모두 170 facts·142144 bytes의 최종 DTO가 같았으며,
전체 WeatherValue와 payload의 digest도 같았다.
평균 CPU는 약2647→2123ms, wall은 약3143→2555ms다.
후보의 winner 전체 조회는 110행이며 두 번의 SQL 시간은 32.55/29.95ms였다.
기존 424개 candidate 전체 ORM을 새 구현에서는 scalar로 읽는다.
경보 조회의 1380행은 그대로 유지된다.

동일 승자 쿼리를 실제 PostgreSQL에서 force_custom_plan과 force_generic_plan으로
각각 준비·실행했다. 두 방식 모두 정확110행·동일 digest였고,
EXPLAIN ANALYZE 실행시간은 7.189/7.128ms였다.
실행 시 필요 없는 partition은 Actual Loops=0이고 나머지는 정확한 PK index로 읽었다.
이 숫자를 cold-cache 또는 모든 크기의 요청 성능 보장으로 확대하지 않는다.

## 통합 검사와 아직 남은 gate

- 정확한 통합 HEAD의 실제 전용 PostgreSQL 전체 pytest: **628 PASS, 49 warnings**.
  원래 실패했던 이전 통합 실행 620 PASS/1 FAIL은 지우지 않았고,
  migration head 기대값을0020에 맞춘 후 최종 전체를 다시 검증했다.
- Ruff 전체, frontend type-check/lint 및 frontend 전체 **202 PASS**.
- PR106 정확 통합 HEAD의 GitHub Python/frontend CI **둘 다 SUCCESS**.
- API/CS native ARM64 build EXIT0, UI local ARM64 archive build EXIT0.
  이는 image strict probe·실제 서비스 교체·live UI 성공을 대신하지 않는다.
- 이전 R24 live UI **35 PASS/1 FAIL**(25초 지도 settle)은 실패 그대로 유지한다.
  새 frontend/backend 동시 배포 후 **같은 25초 기준**으로 다시 확인해야 한다.
- 건강 점검 주기90초를 포함하는 실제 유휴 CPU 비교는 별도로 최소90초 주기로 측정한다.
  공유 webserver가 켜진 표본을 기존 webserver OFF baseline과 직접 비교하지 않는다.

보존한 JSON과 각 독립 원문은 비밀·DSN·인증자료를 포함하지 않는 명시적 안전 투영이다.
운영 인덱스·기존 행 삭제 및 최종 머지/배포 결과는 후속 실제 증거로 추가한다.


## 실제 배포와 live UI R25

API·Dagster code server·UI의 제품 소스58230797b1b3c932537b28fc8ca5139e8075c49e를 실제 arm64 호스트에 배포했다. API와 code server는 native health healthy/restart0/OOM0이며 90초 점검주기를 유지한다. UI는 원래 native healthcheck가 없어 /login 전체 HTML을 3초 전체예산으로 확인했다. API /version의 git_commit도582다. 기존 컨테이너·이미지·실패 증거는 보관했다. Geo5개 컨테이너의 Config/HostConfig/Mount·PID/startticks·재시작 횟수는 유지했다.

R25는 2026-10-16T16:07:34.657Z~16:08:26.523Z에 기존과 같은36개 케이스를 실행하여36PASS/0FAIL이었다. 제한25초와 batch60/max3/전체692위치는 유지했다. 최종 poll에서 canvas 표시, loading0, pending read0, body task0, document task0이었다. 12개 HTTP200/본문12개/본문오류0이며 692개 요청 identity가 모두 서로 다르고 배치 크기는60×11+32다. 첫 미완 요청의 navigation abort1개와 완료 요청 cleanup signal12개를 구별한다. 마지막 marker body는 첫 정상 요청으로부터22.055초에 수신했다. 이는 지도 전체 대기시간과 같은 지표가 아니다.

로그인·7개 메뉴·Dagster native history/선택 실패 상세·기본 상세 fanout 방지·503/abort 뒤 복구·320/375/414/768/1440px·logout·runtime 오류 검증이 모두 포함된다. 실제375/1440 지도 PNG를 root가 직접 열어 지도와 선택 상세 표시를 확인했다. 이 검증은 domain write를 막은 읽기 중심 UI 검증이며 수집 job 실행 성공이나 whole-host CPU 개선을 증명하지 않는다. 이전 R24의35PASS/1FAIL은 그대로 유지한다.

[36개 결과](evidence/2026-10-17-weather-live-ui-r25.json), [마커 시간·개수](evidence/2026-10-17-weather-live-ui-r25-marker-timing.json)를 공개 안전 투영으로 보관한다. 이 R25는 기존 단기예보 초기 삭제를 실행하기 전 결과다.
