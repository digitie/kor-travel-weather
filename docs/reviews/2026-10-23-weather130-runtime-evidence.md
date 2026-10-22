# Weather PR #130 수집 병행·안정성 배포 증거

## 대상과 판정

제품 PR [#130](https://github.com/digitie/kor-travel-weather/pull/130)의 source HEAD는
`40bcce6cba282d56ededa59504fd6fd49aa1a65e`, merge commit은
`9b03a4722b9abf873a64569994a83ec5a45d9661`이다(2026-10-22 22:46:32Z 머지).
KMA 격자/중기 요청을 최대 2개 선행하고 DB 게시자는 하나로 유지한 코드가 Odroid code-server에
배포됐다. 실제 nowcast 300격자 실행은 성공했다. **전체 job CPU/RAM 절감률은 아직 입증하지
않았다.** 소표본 재수집과 실운영 한 번의 관측을 통제된 전후 비교로 취급하지 않는다.

이 문서 PR은 Markdown과 안전한 요약 JSON만 추가한다. 코드·schema·운영 설정 변경은 없다.
사용자 지시로 문서 PR의 CI를 실행하거나 기다리지 않는다. 아래 CI는 이미 완료된 제품 PR의 기록이다.

## 검사와 독립 리뷰

| 검사 | 실제 결과와 범위 |
|---|---|
| 로컬 전용 PostgreSQL 전체 검사 | 722 passed, 57 warnings, 1,146.55초. 이후 취소 1개·정확 경계 4개 추가 전 결과 |
| 후속 집중 검사 | 45 passed, 실 PostgreSQL 검사 2개 deselected. 실 PostgreSQL은 앞선 전체 검사에 포함 |
| 제품 최종 CI Python | **727 passed**, 57 warnings, 265.68초 |
| 제품 최종 CI frontend | type-check PASS, **223 passed / 18 files** |
| 제품 최종 CI Ruff | PASS |
| 제품 코드 적대 리뷰 | James·Popper가 최종 source HEAD를 각각 재검토, 미해결 P0/P1/P2 없음 |

이미 완료된 [제품 CI 실행](https://github.com/digitie/kor-travel-weather/actions/runs/39863934182)의
실제 로그에서 최종 수치를 확인했다. James 초기 P2였던 raw source JSON의 정확히 8MiB 경계
재현은 수정하고 경계 검사를 추가했다. 두 독립 리뷰 최종 판정은 coordinator의 확인을 근거로
기록하며 원본 보고서를 새로 작성한 것처럼 제시하지 않는다.
[검사·리뷰 요약](evidence/2026-10-23-weather130-validation.json).

## 가짜 지연 실험과 실제 6격자 수집을 구분한다

[가짜 지연 실험](evidence/2026-10-23-weather130-simulated-benchmark.json)은 HTTP 40ms,
게시 25ms, 30격자/60fact를 고정한 로컬 제어 실험이다. 요청 1개는 1.649/1.6215초, 2개는
1.0133/1.0141초였다. 최대 동시 요청이 각각 1/2임을 확인했다. HTTP 대기와 게시를 겹칠 수
있다는 제한된 증거이며 Odroid 운영값이나 전체 프로세스 RAM 절감률이 아니다. JSON의
`python_peak_mib`는 Python 메모리 추적값이며 RSS/cgroup 전체 메모리가 아니다.

[실제 KMA→운영 DB 첫 검사](evidence/2026-10-23-weather130-live-initial.json)는 동일한 6격자에서
요청 1개 실행이 16개 값을 게시하고, 뒤의 요청 2개 실행은 중복 억제로 0개를 게시했다. 각각
2.572/1.1초였지만 **서로 작업량이 달라 직접 속도 비교를 하지 않는다.** 두 실행 모두 성공했다.

[워밍 후 1/2/1/2 반복](evidence/2026-10-23-weather130-live-warm.json)은 6격자 모두 신규 게시
0개인 멱등 재수집이다. 순서대로 2.599/1.49/2.083/1.934초, 프로세스 최대 RSS는
126.28/127.28/128.16/128.91MiB였다. 실제 provider 통신을 포함하지만 작은 범위·0건 게시·
짧은 반복이라 전체 job 메모리·CPU 개선률로 일반화하지 않는다. 두 요청에서 메모리가 줄었다고
판정할 근거도 아니다.

## 공개 HTTPS UI와 실패 기록

초기 n150 브라우저는 13개 case를 통과한 뒤 marker 요청 504 여섯 건과 응답 대기 timeout으로
실패했다. 이를 성공 보고에서 지우지 않는다. **최초 504 원인은 미확정**이다.
[초기 UI 요약](evidence/2026-10-23-weather130-pre-ui.json).

coordinator는 별도 탐색에서 `ERR_NETWORK_CHANGED`와 source catalog 실패도 관측했다.
내부 API 검사에서는 marker 60곳 약 1.5초, catalog offset 1,000 목록+count 약 0.6초였지만,
이 관측만으로 외부 504 원인을 확정할 수 없다. 이 부가 관측은 coordinator의 조사 기록이며
첨부된 초기 UI JSON 자체가 해당 네트워크 오류/내부 지연을 모두 증명하는 것은 아니다.

이후 **Docker 격리 network namespace의 브라우저**에서 실제
[Weather 공개 HTTPS](https://weather.digitie.mywire.org)를 접속해 22개 case를 통과했다.
예상 밖 JS/HTTP 오류는 0, VWorld 응답은 48회 성공/0회 실패다. 로그인·로그아웃, 키보드
멀티셀렉트, 전체 해제/복원, 단일·복수 provider, 검색, 빠른 필터/범위 변경, KMA 실제 marker와
온도/강수 카드, 목록→지도 상세, 경보 포함, 모바일, 의도한 provider 목록 503 복구를 포함한다.
화면전환/필터 변경으로 취소된 `ERR_ABORTED` 요청과 의도한 503은 예상 밖 오류와 구분했다.
[격리 UI 요약](evidence/2026-10-23-weather130-isolated-ui.json).

이 22개 검사는 **code-server 운영 교체 전** 결과다. 배포 후 최종 UI 재검증은 coordinator가
이어 수행할 예정이며, 후속 결과가 오기 전에는 미검증으로 남긴다. 격리 브라우저 성공은 초기
네트워크 실패를 분리해 관찰한 결과이지 최초 504가 영구 해결됐다는 증거는 아니다.

## NVMe 배포와 원래 실행 상태 복원

배포 release 경로는 Odroid NVMe의 `/srv/ktdm-releases/weather130-40bcce6c`다.
code-server만 source HEAD로 교체했고 API/Web은 이번 변경으로 교체하지 않았다.
rollout PASS, repository load 성공, runtime fetch concurrency 2를 확인했다. 다른 서비스의
컨테이너 9개는 보존했다. Weather instigator를 기록·잠시 중지하고 활성 worker를 비운 후
교체했으며, 공용 daemon을 중단하지 않았다. 원래 instigator 상태 복원 결과는 RESTORED다.
18개 schedule RUNNING, sensor 3개 RUNNING/원래 STOPPED 1개로 복원됐다.

coordinator 확인에 따라 이번 작업의 stage 컨테이너와 staging 환경을 정리했다. 원본 env·compose,
API token·개인 로그는 첨부하지 않는다.
[안전한 배포·실행 요약](evidence/2026-10-23-weather130-rollout-run.json).

## 배포 후 실제 Dagster 실행

- Dagster run: `0aeaa342-4f67-5f29-89d2-58d3a231b19e` — SUCCESS.
- SyncRun: `run_d020f39f5f75425fa1bff18f5c0c73b9` — success, `has_error=false`.
- SyncRun 시작/종료: 2026-10-22 22:51:38.498128Z / 22:54:10.278685Z.
  한국 시각으로 2026-10-23 07:51:38.498128~07:54:10.278685, **151.781초**다.
- 300격자/요청 300회, 위치 446곳, 게시 3,554개.

code-server cgroup 표본은 위 SyncRun보다 넓은 **218.810초/22표본**이다. CPU 누적 사용량의
차이는 131.142669 core-seconds로, 표본 구간 평균 **59.934% of one core**다. 호스트 전체 CPU%
또는 4코어 정규화 수치가 아니다. cgroup `memory.peak` 최대는 **411.559MiB**, 마지막
`memory.current`는 **271.699MiB**다. 이 값에는 worker 외 metadata/file cache 등이 포함되어
**프로세스 RSS가 아니다**. peak는 cgroup이 기록한 누적 peak이며 이번 SyncRun만의 allocation으로
해석하지 않는다.

이전의 다른 시각 job과는 provider 데이터량·게시 수·동시 부하가 다르다. 따라서 151.781초나
59.934%/411.559MiB를 이전 job 수치와 단순 비교해 개선률로 표현하지 않는다. 동일 artifact와
부하·cold/warm 조건을 맞춘 더 넓은 전후 실험이 전체 CPU/RAM 절감 판정의 후속이다.
