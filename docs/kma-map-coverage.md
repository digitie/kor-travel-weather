# KMA 지도 수집 범위 개선

## 원인과 변경

2026-10-22 운영 DB의 활성 위치는 1,450곳이다. 이전 KMA 수집은 명시 대상 21격자와 고정 추가 표본 150격자, 총 171격자를 반복 방문했고 최근 24시간 일반 KMA 값이 갱신된 위치는 258곳이었다. 특보만 있는 위치도 제공처 존재 조건에 포함되어 KMA 필터 수는 1,450곳으로 보였다.

DB에 nx/ny가 저장된 389곳의 격자는 268개지만, 미계산 좌표를 `python-kma-api`의 public `to_grid`로 계산하면 전체 1,076개 격자다. KMA 격자·발표 시각은 직접 재구현하지 않는다.

격자 중심의 기존 공간 분산 순서를 유지하고 실행 시각의 슬롯에 따라 추가 수집 구간을 순환한다. 명시 대상 21격자는 항상 우선한다. 전체 300격자/회 상한 안에서 나머지 279격자를 방문하며, 서로 연속한 성공 슬롯 4개가 전체 방문 기준이다. 동일 격자의 여러 위치에 값을 전파하고 비활성 위치를 제외한다.

실황·초단기예보는 시간 슬롯, 단기예보는 3시간 슬롯을 사용한다. 정상 주기에서 전체 방문은 각각 약 4시간·12시간이다. 이는 전체 위치의 2시간 이내 최신성 보장이 아니다. 실패·skip·위치 목록 변경은 완료 시간을 늘린다. Dagster 재시도는 원본 실행 생성 시각의 슬롯을 유지한다. 운영 검증용 `kortravelweather/kma_rotation_slot`은 대상 선택만 고정하며 provider 발표 시각을 바꾸지 않는다.

지도는 선택한 제공처에 일반값이 있는 위치를 기본 조회한다. `특보만 있는 위치 포함`으로 특보 전용 위치도 볼 수 있다. API의 기존 기본 동작은 유지하며 `weather_only=true`에만 일반값 조건을 적용한다. provider와 일반값 조건은 같은 EXISTS에서 결합하고 페이지 제한 전에 실행한다. 미래 예보는 과거 수집 시각만으로 제거하지 않는다.

또한 KMA 실황 `T1H`·`RN1`을 기온·강수 요약과 마커에서 인식하도록 보완했다. 값이 없을 때 0도로 보이던 변환도 막고 실제 0도와 음수는 유지한다.

## 자원·요청 예산

- fill 기본값과 Compose fallback을 300으로 일치시킨다. 전체 격자 상한 300, 응답 행 100만, 값 800만, 대상 1만 상한은 유지한다.
- publish는 기존 5,000값·25 source 배치와 location chunk 경계를 유지한다. 동시 worker 수나 메모리 상한을 높이지 않는다.
- 정상 hourly 24회 기준 최대 7,200 logical request/operation/day, 단기 8회 기준 2,400회다. HTTP 페이지·재시도·수동 실행·다른 소비자의 호출은 별도이므로 키별 허용량을 보장하는 수치가 아니다.
- 추가 예산 0인 경우 미방문 격자가 있으면 순환 완료 횟수를 0으로 기록하지 않고 순환 비활성으로 구분한다.

## 검증과 배포

구현 PR [#128](https://github.com/digitie/kor-travel-weather/pull/128)은 두 적대적 리뷰어의 지적을 반영하고 머지했다. 추가 예산 0의 순환 로그, provider 목록 장애 안내 유지 문제를 수정했으며 최종 소스 `3a0ae398`의 미해결 P0/P1/P2는 없다.

Python 전체 718개, 프런트엔드 223개·type-check·lint, Ruff·OpenAPI export와 최종 커밋의 CI가 통과했다. 운영 DB를 사용하는 별도 API·웹에서 live UI 22개가 통과했고 JavaScript/API 오류 0, VWorld 48건 성공이었다. 실제 KMA marker 응답, T1H·RN1 카드, 단일/복수 제공처, 특보 전용 위치 포함, 빠른 필터 변경, 목록 선택, 모바일, provider 목록의 의도적 503, 로그인·로그아웃을 확인했다.

초기 시험의 `route.fetch` 및 raw HTTP→`route.fulfill` 중계에서는 취소/지도 렌더링 정체가 있었다. loopback HTTPS streaming proxy와 Chromium host resolver를 사용해 공개 origin과 브라우저 취소 동작을 유지한 경로에서 전 케이스를 확인했다. 초기 실패 보고는 최종 통과 근거로 사용하지 않는다. 공개 HTTPS 운영 검증은 배포 후 별도로 수행한다.

Odroid NVMe에서 ARM64 이미지를 만들고 API·웹·code-server를 `3a0ae398`로 배포했다. 실행 중인 Weather worker가 종료된 뒤 교체했고 공유 Dagster daemon·PostgreSQL·Geo·Transport 등 비교 대상 9개 컨테이너의 ID와 실행 상태를 보존했다. DB migration은 없다. 운영 fill·전체 ceiling 모두 300이다.

첫 단발 gRPC 검사 실패 시 기존 3개 이미지·Compose로 자동 롤백했다. 이후 운영과 같은 tmpfs·환경을 갖춘 별도 code-server에서 socket 준비 40.75초, gRPC 검사 완료 55.21초와 실제 `__repository__` 로딩을 확인했다. 재배포는 최대 180초 readiness 대기와 저장소 로딩 검증을 통과했다.

## 실제 수집·운영 UI 결과

2026-10-22 KST에 nowcast 검증용 슬롯 0~3을 순서대로 실행했고 모두 성공했다. 합쳐서 1,076개 격자 원천·1,450곳의 최근 일반값을 확인했다. 최근 6시간·24시간 내 일반 KMA 값이 있는 위치는 기존 258곳에서 1,450곳으로 늘었다. 이는 T1H 대표값을 우선하고 T1H가 없으면 PTY를 확인한 결과다. 수집 run→source→현재값→fact의 연결을 사용하며 read-only transaction과 SQL 12초 제한을 적용했다.

유효 기온 T1H는 1,445곳이다. 협재해수욕장·연평도·삼척해수욕장·격렬비열도·춘천 가리산 5곳은 이번 원본 응답에 T1H=-999, RN1=-998.9, REH=-998 등이 왔고, 정규화 후 PTY=0만 남았다. 이 5곳을 기온 수집 성공으로 세지 않는다. 원본을 보존하며 다음 순환에서 원천 응답이 회복되면 다시 갱신한다.

| 슬롯 | 격자·논리 요청 | 처리 위치 | 수집 시간(초) | CPU 한 코어 기준 평균(%) | cgroup current 샘플 최대(MiB) |
| --- | --- | --- | --- | --- | --- |
| 0 | 300 | 422 | 128.17 | 46.65 | 380.84 |
| 1 | 300 | 406 | 114.60 | 57.80 | 369.02 |
| 2 | 300 | 446 | 114.45 | 56.47 | 386.46 |
| 3 | 300 | 454 | 127.61 | 59.47 | 386.74 |

4회 합계 1,200 logical request와 11,565개 값 적재를 기록했다. 위치 처리 수에는 명시 대상의 반복 방문이 포함된다. 검증은 명시 슬롯 태그로 연속 실행했으며 자연 시간의 4회 hourly tick을 관찰한 결과가 아니다. 단기예보의 12시간 전체 순회는 정책·시뮬레이션 기준이고 이번 실측은 nowcast에 한정한다.

CPU는 수집 구간 내 약 10초 간격 샘플의 한 코어 환산값이며 호스트 전체 사용률이 아니다. 메모리는 code-server·worker·page cache를 포함한 cgroup current 샘플 최댓값이다. cgroup 수명 전체 peak는 최대 402.50MiB였고 기동 과정도 포함한다. 동일 작업의 변경 전후 대조 실험이 아니므로 CPU·메모리 저감률을 주장하지 않는다.

스케줄 18개 RUNNING, 센서 3개 RUNNING·1개 STOPPED로 원래 상태를 복원하고 전체 일치를 확인했다. daemon은 유지했다. 공개 HTTPS의 live UI 22개도 모두 통과했다. KMA 일반값 필터 1,450곳, 실제 marker 응답, T1H 5.5 deg_c·RN1 0 mm 카드, 빠른 전환·모바일·장애 안내·로그아웃을 확인했고 JavaScript/API 오류 0, VWorld 48건 성공이었다.

## 근거 파일

- [기준·전체 격자 시뮬레이션](reviews/evidence/2026-10-22-kma-coverage-baseline.json)
- [두 리뷰어·단위 검사·CI](reviews/evidence/2026-10-22-kma-coverage-validation.json)
- [staging UI](reviews/evidence/2026-10-22-kma-coverage-stage-ui.json), [운영 배포](reviews/evidence/2026-10-22-kma-coverage-rollout.json)
- [수집·자원·freshness 실측](reviews/evidence/2026-10-22-kma-coverage-post.json), [5곳의 원천 결측값](reviews/evidence/2026-10-22-kma-coverage-temperature-quality.json)
- [스케줄·센서 복원](reviews/evidence/2026-10-22-kma-coverage-instigators.json), [공개 운영 UI](reviews/evidence/2026-10-22-kma-coverage-production-ui.json)
