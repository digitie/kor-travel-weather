# 2026-10-29 최신 Weather 배포 기록

상태: **DEPLOYED / 15_FUNCTIONAL_PASS**. 2026-10-29 23:33:59 KST에 최신 이미지 배포와 Manager 운영 표식 등록을 마쳤다.
23:37:41 KST readback에서 세 역할의 실행 소스·이미지와 DB 보정을 확인했다. 후속 공개 UI 기능 검사 15개는 통과했다.
첫 실행의 실제 Weather origin 504는 미해결 성능 문제로 남겨 두며, 전체 UI 성능 수용 완료로 표시하지 않는다.

## 대상과 범위

배포 소스는 최신 원격 main을 확보한 커밋 `ad4ee5350a50804e34b84679be29d7a743f9e443`이다.
깨끗한 체크아웃에서 만든 Git archive를 Odroid NVMe에 전송했다. API·관리 웹·Dagster 코드서버의
이미지를 새로 빌드했고, migrate 역할은 새 API 이미지를 사용했다. 제품 코드는 이 운영 문서 PR에서 변경하지 않는다.

원격 소스 위치:

```text
/srv/ktdm-releases/weather-latest-ad4ee5350a50-20261029-8e893720ee074c598ae547e260e41d82/source
```

빌드·배포 자료의 로컬 보관 위치는
`F:\dev\audit-reports\2026-10-24-operations`이다. 원본 저장소의 미커밋 파일은 배포 소스에 포함하지 않았다.

## 변경 전 확인

2026-10-29 22:01 KST 확인한 Weather 실행 소스와 이미지는 다음과 같다.

| 역할 | 실행 소스 | 실행 이미지 ID |
| --- | --- | --- |
| API | `21d9ea9090360ffed1ef68e42a1b7d866d3b53de` | `sha256:d307600054b8f9d5c38f9787075e829a53de2ce4dcd6d2ec03925003ce5eb80a` |
| 관리 웹 | `a7075a6bcc5fb2f086086e872245247f67ffb6bf` | `sha256:6d2ba2c5cd628c68fa4c4fdd5248b85305428b95e27e44722b0f156005d18c62` |
| Dagster 코드서버 | `e39362a3803272e14bbd033359d0fe377b7441e0` | `sha256:e6e3a048d27129c68c12cd5110de0921a45c3683fa3885e11a29b4037af8ca97` |

22:16 KST의 읽기 전용 확인에서 DB schema는 `0034_marker_kind_indexes`였다.
Weather에는 실행 중인 작업과 대기 작업이 있었으며, export publisher는 이미 중지 상태였다.
운영 중인 API·코드서버의 service export 설정은 유지한다.

## 실제 적용 순서와 보호 범위

호스트 공통 Manager 잠금으로 운영 변경을 직렬화했다. Weather 외 16개 컨테이너의 ID·이미지·시작 시각이
배포 전후 동일했다. 공용 PostgreSQL과 Dagster daemon·webserver는 교체하지 않았다.

1. 23:08:44~23:08:52 KST에 기존 Weather schedule·sensor 상태를 기록하고 Weather만 일시 중지했다.
2. 23:09:12~23:09:32 KST에 `weather_daily_summaries`와 `alembic_version`을 244,160바이트로 백업하고, 운영 PostgreSQL과 같은 PG18의 `pg_restore --list`로 두 표를 확인했다.
3. 23:09:49~23:10:29 KST에 `0035_daily_precip_coverage` 데이터 보정을 적용했다. 구간의 일부 RN1 값으로 확정했던 강수 합계·무강수 판정을 null로 바로잡는다.
4. 기존 코드서버를 유지한 채 API·관리 웹을 교체했다. 23:13:53 KST에 새 API 버전·두 이미지·건강 상태·로그인 화면을 확인했다.
5. 대기·실행 작업에 대한 후속 처리를 마친 뒤 23:23:17 KST에 논리적 실행·대기 작업과 실제 worker가 모두 없음을 확인했다. 코드서버 상태를 백업하고 이미지를 교체했다.
6. 코드서버는 23:24:24 KST에 새 이미지로 시작했다. 교체 후 확인 요청의 timeout은 별도 실패로 보존하고, 아래 재검증 절차로 복구했다.
7. 23:32:58 KST에 원래 실행 중이던 Weather schedule·sensor를 복구했다. 23:37 readback은 21개 RUNNING, 2개 STOPPED였다. `weather_export_publisher`와 `weather_native_incarnation_recovery`는 STOPPED를 유지했다.
8. 23:33:58 KST에 Manager 공식 CLI의 adopt·verify·compose-args 대조를 완료했다. 마지막 컨트롤러 기록은 23:33:59 KST의 `deployed-and-adopted`다.

0035는 이력 표의 schema를 변경하지 않는 작은 요약 표의 데이터 보정이다. 일반 수집 작업이 저장하는
fact·current 표와 분리되어 있으며, 요약을 쓰는 export publisher를 중지한 상태에서 적용했다.
각 날짜별 갱신은 autocommit하고, statement timeout은 최대 60초, lock timeout은 최대 5초로 제한한다.
실패 시 잘못된 부분 합계를 되살리는 자동 downgrade는 하지 않는다.

## 기존 작업의 종료와 재시도

API·웹 교체 중 기존 코드서버는 유지했다. 23:22 KST의 `SAFE_TERMINATE` 처리 대상 네 작업 중
Weatherstack·KMA 특보·KMA 초단기예보는 CANCELED, Open-Meteo는 SUCCESS로 끝났다.
이 배포를 모두 자연 완료로만 진행했다고 해석하면 안 된다.
취소 전 미게시 export attempt의 정리 완료는 확인하지 않았으며, publisher를 계속 중지하고 해당 기록을
보존했다. 강제 SQL 정산이나 publisher 활성화는 하지 않았다.

세 CANCELED 작업은 23:34 KST에 같은 job·run config·Weather location으로 `ALL_STEPS` 재실행했다.
재실행마다 원래 run을 가리키는 parent 연결도 확인했다. 23:37:41 KST 관측 결과는 다음과 같다.

| 작업 | 원래 결과 | 재실행 결과 | 확인 범위 |
| --- | --- | --- | --- |
| `weatherstack_weather_job` | CANCELED | SUCCESS | config·job·location·parent 연결 일치 |
| `kma_weather_alerts_job` | CANCELED | STARTED | config·job·location·parent 연결 일치, 완료 미확인 |
| `kma_ultra_short_forecast_job` | CANCELED | STARTED | config·job·location·parent 연결 일치, 완료 미확인 |
| `open_meteo_weather_job` | SUCCESS | 재실행 불필요 | 원래 성공 유지 |

## 코드서버 확인 timeout과 복구

23:25:51 KST에 코드서버 교체 후 확인 단계가 `TimeoutError('timed out')`로 실패했다.
코드서버 교체 결과를 성공으로 덮어쓰지 않고 원래 실패 기록을 보존했다.
23:26:02 KST에 Weather workspace가 LOADED였고, 후속 읽기 전용 확인에서 실제 코드서버의 server ID와
workspace 연결을 대조했다. 23:27 공개 전 단계의 로컬 확인에서는 API `/version`이 200·0.192초,
관리 웹 `/login`이 200·0.888초, GraphQL repository 조회가 200·0.198초였다.

복구는 이미지나 DB 보정을 다시 실행하지 않았다. 별도의 현재 검증 결과에 최신 커밋과 세 이미지 ID가
모두 일치함을 확인한 뒤, 실제 건강 상태와 schema 0035를 다시 검사하고 기존 실행 상태를 복구했다.
23:32:26~23:33:17 KST의 verify → restore → verify가 성공했으며, 이어서 Manager 운영 표식을 등록했다.

## 빌드·실행 이미지·운영 표식

빌드는 2026-10-29 **22:18:21~23:08:22 KST**에 완료했다. 세 이미지는 `linux/arm64`이며 OCI revision은
모두 `ad4ee5350a50804e34b84679be29d7a743f9e443`이다. 빌드 중 기존 2GB 메모리 제한을 유지하고 CPU를
임시 1코어로 제한했으며, 종료 후 원래 builder CPU 설정을 복구했다. 소스·이미지·빌드 캐시·임시 파일은 NVMe를 사용했다.
빌드 단계에서는 운영 컨테이너를 교체하지 않았다.

| 역할 | 최종 이미지 ID | 운영 확인 |
| --- | --- | --- |
| API | `sha256:9e68b480b4e4b81f6a3c83c8edb178760a6278fc4bbe086936f21269ca003a3b` | 실행 중·healthy·`/version` 최신 커밋 |
| 관리 웹 | `sha256:0335d575a106cc25eeb38215934d732bfcb88df42bcc0bc0fdc8c514165152a2` | 실행 중·`/login` 200, Docker HEALTHCHECK는 없음 |
| Dagster 코드서버 | `sha256:dba7a4ebe7702c42a3b3734d5599e0f36766631b1898b1c28af9301306253ef7` | 실행 중·healthy·Weather repository 로드 |
| migrate | API 이미지와 동일 | schema 0035 readback 확인 |

Manager 운영 표식은 다음 NVMe 릴리스에 등록했다.

```text
/srv/ktdm-releases/weather-adopt-20261029T143337Z-9b4a3d87866e4446b16c345531697b1b
```

공식 verify는 네 역할의 expected/actual 이미지 ID 일치와 `healthy`를 반환했다.
공식 compose-args의 `kor-travel-odroid`/`main` 해석 결과에서도 네 이미지 ID가 일치했다.
등록 단계의 컨테이너 변경 명령은 0개였고, Manager `.env`와 실행 컨테이너 정보는 유지됐다.

## 리뷰·CI와 공개 UI 검사

배포 도구와 timeout 복구 변경은 각각 독립적인 두 검토를 거쳐 수정했다.
배포 도구의 구문 검사, pause/drain 보호 조건 5개, 복구 결과 선택 조건 4개를 확인했다.
운영 오류는 원래 진단을 남기되 기존 Manager 자격 증명 제거 함수를 사용하여 공개 보고에 민감한 값을 넣지 않았다.

제품 코드의 기존 검증 근거는 `7504f5a4d884e94a71acb9ce0f6f238fcf15cde3`에 대한 CI run
[40623613188](https://github.com/digitie/kor-travel-weather/actions/runs/40623613188) SUCCESS다. 이 제품 코드와 배포한 `ad4ee5350a50804e34b84679be29d7a743f9e443`의 차이는
Markdown 문서 5개뿐이다. 기존 검증은 Python 1,577개, 프런트엔드 276개, type-check·lint·build를 포함한다.
이번 운영 작업에서 이 전체 제품 테스트를 새로 실행했다고 주장하지 않는다.

첫 공개 live UI 실행은 로그인에는 성공했으나, 지도 화면 검증 중 실제 Weather origin의 HTTP 504와 저배율에서
개별 마커를 기다린 검사 timeout으로 **FAIL**했다. 이 실패는 보존했다. 저배율에서는 클러스터를 확인하고
실측 응답에서 선택한 위치의 숫자 기온·상세를 확인하도록 테스트 전제만 바로잡았다. 제품 코드는 변경하지 않았다.
504는 검증 흐름 변경으로 해결된 것으로 처리하지 않으며, cold 성능 후속 항목 `W-UI-CURRENT-01`로 남긴다.

두 번째 Firefox 실행은 실제 종료 코드 0으로 **15개 기능 검사 모두 PASS**였다.

- 실제 로그인, 기본 지도의 클러스터·개별 위치 표시, 선택 위치의 숫자 기온과 상세
- 제공처 멀티셀렉트 선택·복원, PC 1440px·모바일 375px의 지도와 가로 넘침 없음
- HTTPS Dagster 링크, VWorld 실제 타일 응답 64개
- UI 로그아웃·보호 화면 이동·세션 정리·남은 쿠키 0개
- JS 오류·금지된 쓰기·HTTP 500 이상 응답·사용자 중단 요청 없음

이 실행에서 확인한 markers 응답은 **1개·60개 실측 위치**이며 응답 시간은 **20.719초**였다.
선택 위치의 latest는 **4.831초**, forecast는 **4.998초**였다. 화면 전환에 따른 요청 취소 **5개**는 별도로
기록했다. 이 수치는 cold 25초, 180개 위치 또는 전국 전체 로딩 수용을 증명하지 않는다.
검사 전후 실행 컨테이너 정보가 일치했고, 이 실행이 만든 브라우저 프로세스는 종료 후 0개였다.

두 번째 실행의 WSL 브라우저 원본 자료는 다음 경로에 보존했다.

```text
/var/tmp/weather-latest-ui-ad4ee535-20261029-9282b46700
```

로컬 감사 자료는 `weather-latest-ui-accepted-20261029-r2.json`,
`weather-latest-ui-accepted-summary-20261029-r2.json`, `weather-latest-ui-scope-facts-20261029-r2.json`이다.

## 최종 readback과 남은 확인

실측 자료는 `weather-latest-final-readback-20261029-r1.json`에 보관했다. 관측 시각은
2026-10-29 **23:37:41 KST**다.

| 확인 항목 | 결과 |
| --- | --- |
| 세 역할 소스와 실행 이미지 | 최신 커밋·최종 이미지 ID 일치 |
| DB readback | `0035_daily_precip_coverage`, 요약 1,086행, 부분 강수 판정 오류 0행 |
| 표 백업 | 244,160바이트, PG18 복원 목록으로 두 대상 표 확인 |
| Weather 실행 상태 | 21 RUNNING·2 STOPPED, publisher/native recovery 중지 유지 |
| service export 설정 | API·코드서버 모두 기존 `true` 유지, publisher 활성화와 구분 |
| 다른 서비스·공용 DB·Dagster plane | 16개 컨테이너 ID·이미지·시작 시각 동일 |
| Manager 운영 표식과 이미지 ID | adopt·verify·공식 Compose 해석 일치 |
| 공개 UI 기능 검사 | 첫 FAIL·실제 504 보존, 두 번째 Firefox 실행 15 PASS·실제 종료 코드 0 |
| UI 관측 범위·정리 | markers 1응답·60곳, 타일 응답 64개, 화면 전환 취소 5개, 브라우저 잔여 0·컨테이너 정보 동일 |
| 취소된 작업의 후속 재실행 | Weatherstack SUCCESS, 특보·초단기예보 STARTED로 완료 미확인 |

배포 완료는 export publisher 운영 활성화, 별도 보존 정책의 완료, 전국 cold 조회 25초 SLA 달성을 뜻하지 않는다.
첫 공개 UI 실행의 504 원인과 성능 수용은 `W-UI-CURRENT-01`로 남긴다. 23:37 관측에서 실행 중이던 두 후속 작업의
최종 상태는 추가 관측 후 갱신한다.
