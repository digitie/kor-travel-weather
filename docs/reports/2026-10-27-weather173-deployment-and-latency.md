# Weather173 배포와 실제 지도 수용 정산 — 지연 후속 진행 중

## 결론과 범위

[PR #173](https://github.com/digitie/kor-travel-weather/pull/173)의 제품은 main
`a9f54ff3eb53752844a28a209aa8b281ff44ca6b`로 머지됐고2026-10-27 UTC06:35에
API·code-server·웹 세 역할과0034 migration을 운영에 반영했다. 기존 일정21 RUNNING/2 STOPPED,
공용 daemon과 타 서비스 보존, 자연 게시 갱신·새 정기 수집 재개를 확인했다.

**마커 지연 수용은 실패 상태다.** 실제60지점 세 동시 요청의 두 번째 표본은19.506/
29.884/28.324초로25초 상한을 두 요청이 넘었다. 지도 면적·상세·실제 온도 표시는 통과했지만
빠른 지도 응답이나 전체 전국 지점 완료를 수용하지 않는다. 현재 추가 반복 UI/직접 provider
호출 없이 저장 요청과 SQL 생성 경로에서 병목을 좁히는 단계다.

모든 시각은 별도 표기가 없으면2026-10-27 UTC다. 실제 Android/iOS 기기와 Firefox는 이번
배포 수용에서 실행하지 않았다.375px 결과는 Chromium의 반응형 브라우저 검사다.

## 소스와 배포 게이트

- 최종 제품 head `4f9878133c34e8151f94bfe3631fe9d87eab50ea`, main merge는 위a9f54다.
- 정확 head CI run40423939440의 Python1319와 frontend259/type/lint/build가 성공했다.
  migration 충돌 보호31개·WSL 집중 회귀와 독립 두 소스 리뷰를 배포 전에 확인했다.
  이전 소스의 전체 실행·Docker 기록을 정확 최종 head 전체 통과로 확대하지 않는다.
- 옛5ba2 전체 WSL1299 실행은 자연 exit0/3892.118초로 회수했고 원본을 중단·덮어쓰지 않았다.
- 완성 ARM64 세 이미지는01:46:57~02:16:50에 빌드됐다. r2/r3에서 다시 빌드하지 않았다.

| 역할 | 실제 적용 image SHA256 |
| --- | --- |
| API | `06480b4154d1ad852232e1b9d205d4a65d00761fff19089a1c344c1e60c95040` |
| Dagster code-server | `989ec9bdfebbd36cc6c4115387b1d70dd3fda45c8aecd90107a110db67deb0e1` |
| 웹 | `b045e7f738e39ac91450e86c9e6e1fd9c4cd13114d951bd9a45641cd8fe2df96` |

06:42:52 실제 API/CS는 healthy, 웹은 healthcheck가 없는 Running이었다. API의version은
배포 merge a9f54와 일치했다. 공용 daemon CID
`123eb231e3ede89780537d29386932e7d44fd8680d205a095a719a1e6dfc2c4c`와 image·시작 시각은
유지됐다. 실제 실행은 Manager G→Weather R·NVMe·승인된 세 역할 범위에서만 진행했다.

## 실패 원장과 r3 완료

앞선 r1/r2는 Manager `targets validate --json`의30초 timeout으로 pause/DDL/운영 교체 전에
실패했다. 원본을 보존하고 이 명령 하나만60초로 보완한 r11 배포 bundle을 새로 만들었다.
다른 명령의 timeout·호스트 검증·응답 상한은 바꾸지 않았다.48개 무접속 회귀와 두 독립
운영 소스 리뷰가 통과한 다음 Manager venv Python `-I -B`로 실행했다. 최적화로assert를
비활성화하지 않았다.

검토 manifest SHA256은 `203efe6993c558d4255a643a113f4ea6f0af2fed04912f86b77ad4a5e7d49ee4`,
stage-input은 `5f10258779b76eaecbe11a5437cf03fe4a017745dde0a05fa7f49749d316fbbc`다.
최종 r3 소유 root는 `/srv/ktdm-releases/weather173-a9f54ff3eb53-20261027-r3`다.

| 단계 | 관측/원장 |
| --- | --- |
| stage |04:16:28,624개 소스·변이 없음 검증 |
| controller 시작 |04:17:23.486704 |
|23개 Weather 일정 중지 첫 확인 |04:18:56 |
| 마지막 정기 OpenMeteo run |`9010c743-13de-4e0a-98c6-401bd7410122` 자연 SUCCESS |
| drain 완료 |06:31:27.407909 |
| 소유 migrator 실행 |06:31:45.176368403~06:31:55.393446473, exit0 |
| 교체·복원 완료 |06:35:08.793288, `deployed-health-verified`·`instigators_restored=true` |
| 동일 unit 종료 |06:35:09, MainPID0·exit0 |
| 실제 복원·G 확인 |06:43:08,21 RUNNING/2 STOPPED·active run/worker 없음·G holder 없음 |

unit은 `kt-weather173-rollout-a9f54ff3-20261027-r3.service`, invocation은
`c522621aa5ba446993c15ae3690a4d9c`다. 남은 `active/exited` 상태는 RemainAfterExit의 결과이며
실행 중인 parent를 뜻하지 않는다. 무관 서비스 signal·수동 collector/job·외부 provider 직접
호출은 없었다. UI의 정상 VWorld 타일 GET은 실제 화면 확인 범위다.

## 기존 인덱스와 롤백 제약

r3가 아직 drain 중이고 schema0033인05:15~05:24에 네0034 인덱스가 이미 valid/ready였다.
최초 생성자·시각은 확인하지 못했다. 이번 migration이 네 개를 새로 만들었다거나1GiB를
새로 늘렸다고 귀속하지 않는다.

| 구분 | 배포 전/후 동일 OID |
| --- | ---: |
| current |745723 |
| forecast |745759 |
| provider_current |745784 |
| provider_forecast |745826 |

실제 tableOID610176과0034 원문 SHA256
`409b93583cb90d4dad53f51f7df8b769cd0cbe6a120f301a8e99dcf6dacb47d0`를 대조했다.
`target_table`·`checked_index`·`expected_predicates`·`specifications`를 SELECT-only로 재사용해
배포 전과06:43:28 후의 정렬/INCLUDE/predicate/opclass/collation/valid/ready가 모두 일치했다.
READ ONLY·statement3초·lock500ms를 유지했으며 upgrade/downgrade/DDL은 진단에서 호출하지 않았다.

기존 자동 rollback은0033 downgrade 뒤 인덱스0개를 전제하므로 배포 전부터 있던 네 인덱스를
보존하지 못할 수 있다. 이번 forward는 성공했고 rollback은 실행하지 않았다. 다음 배포 도구
재사용 전 원래 인덱스 baseline 보존을 별도 P1으로 해결해야 한다.

## 일정 중지 구간과 자연 재개

최초 pause 확인04:18:56부터 마지막23개 STOPPED 확인06:25:19까지 최소2시간6분23초의
중지 구간이 관측됐다.06:35:08의 완료에는 복원이 끝났으므로 이 시각까지 전부 중지 상태였다고
확대 계산하지 않는다. 원래 publication_clock04:18:03.976101에서 새06:41:21.549822까지는
2시간23분17.573721초이며, 이는 게시 시각 사이 간격이다.

06:50:25에는 clock06:50:21.769286 재갱신과 새 정기 OpenMeteo run
`1a3d2712-cd2b-4a5e-9239-8cc9b89eee2a`의 STARTED·current1197행·heartbeat06:50:23을 확인했다.
게시 재개와 새 원천 수집 재개는 확인했고, 그 run의 전체 예보 완료·유지보수 구간 자동 backfill은
확인하지 않았다. 빠른 실황/특보의 중지 공백은 새 최신값으로 소급 해소됐다고 쓰지 않는다.

다음 배포에서는 장기 예보 run 자연 종료를 pause/G 획득 전에 기다리고, 짧은 최종 재확인 뒤
pause/drain·교체한다. 현재 r3에 외부 일정 변경이나 취소 신호를 추가하지 않았다.
도구의 빈 drain 반환·safe_to_restore 변경·정방향 변이 직전 취소 safepoint도 독립 P1이다.

## 실제 UI 통과 범위

06:46:31 끝난 공개 로그인→목록→상세→닫기→로그아웃/보호화면 검사는 모두 통과했다.

| viewport | 지도 시작 | 지도 높이 | 가로 넘침 | 상세 |
| --- | ---: | ---: | --- | --- |
|1440×1000 |152px |824px |없음 |지도 위 absolute 오버레이·Escape 닫기 |
|375×812 |193px |615px |없음 |지도 위 absolute 오버레이·Escape 닫기 |

실제 PNG를 직접 확인했다.375px 지도는 화면 하단808px까지 도달한다. 다만 좁은 화면의 상세
값에 `deg_c`가 그대로 표시되는 기존 표현과 미조회 초기 `정보 없음` 마커는 남아 있다.
가평의 실제 응답 OpenMeteo TEMP14.4·collected_at04:53:57.975302Z와 마커14°·상세14.4·
수집13:53:57KST가 일치했다. 재조회 안내는 정상·빈 검색에서 불필요하게 남지 않았다.
관측·예보/전체 제공처의 의미를 소비자에게 더 쉽게 보이는 기능은 별도 후속이다.

## 실제 마커 응답 지연 실패

첫 수용기는 초기 첫 요청 세 개가 응답 없이 남아70초 timeout으로 실패했다. 취소를
수집하지 않은 원본이므로 이 원장에서 취소 원인을 단정하지 않는다. 뒤에 실제 완료한 첫
60×3은200/DTO 일치·12.769/19.115/20.889초였고 모두 같은 시간대에 겹쳤다.
이 표본은 빠른 latency 성공의 증거로 쓰지 않는다.

후속 수용기는 실제 `requestfailed`의 정확 `net::ERR_ABORTED`만 원장에 보존해 제외했다.
다른 network 실패·JSON 오류·잘못된 DTO를 정상 표본에서 빼지 않는다. 무접속14회귀는
초기 취소 후 정상·CONNECTION_RESET 제외 금지·JSON/DTO 실패를 포함해 통과했다.

두 번째 실제 표본은06:48:08.291~06:48:38.177의 세60지점 요청이 모두200·JSON/요청 지점
대응·최소DTO 검증을 통과했다. 본문19.506/29.884/28.324초로25초 상한은 두 개가 실패했다.
동시 겹침은 실제였고 Transport build는 당시 아직 시작하지 않았으므로 이 지연을 그 빌드에
귀인하지 않는다. CPU/RSS·동일 workload 비교나 전체 수집 병목은 측정하지 않았다.

명시 취소는 초기06:48:08.318의3개와 정상 검색 조작 뒤06:48:38.509의3개다. 저장된
ordered location ID scope는각1회로 동일 scope 반복0이다. 초기 목록 재계산이 실제 취소를
유발한 이유는 별도 코드/순서 분석 전에는 확정하지 않는다. 검증의 첫3은 취소를 제외한
첫 유효 요청이며 전국1421개 완료를 뜻하지 않는다.

## 증거 원본과 다음 작업

키·cookie·raw argv·raw 운영 로그는 문서에 포함하지 않는다. 필터된 주요 원본은 다음과 같다.

- 완료/역할: `/var/tmp/weather173-rollout-observe-20261027/20261027T064253Z-11018c.json`
- 실제 복원/clock: `/var/tmp/weather-followup-20261027/20261027T064309Z-resume-606b6f.json`
- 기존 정의/OID: `/var/tmp/weather-followup-20261027/20261027T052433Z-index-definition-c572e1.json`
- 배포 후 정의/OID: `/var/tmp/weather-followup-20261027/20261027T064329Z-index-definition-e06f00.json`
- 새 정기 수집: `/var/tmp/weather-followup-20261027/20261027T065027Z-resume-1bf529.json`
- 첫 marker FAIL: `/var/tmp/weather173-live-accept-20261027-e6c530f6a6`
- PC/375 PASS: `/var/tmp/weather173-live-accept-20261027-bc423775b1`
- 후속 latency FAIL: `/var/tmp/weather173-live-accept-20261027-15988d7ed0`
- 취소/실제 값 정산: `/var/tmp/weather173-ui-evidence-summary-20261027.json`

다음 한 작업은 저장된 실제60IDs/시각과 query 생성 코드·runtime predicate/index/조인/정렬을
대조해 병목 한 개를 재현 가능하게 식별하는 것이다. 추가 UI 반복·provider/job 호출·운영 DB
쓰기 없이 수행하며, 필요한 경우 한 batch의 실행 없는 EXPLAIN만 READ ONLY/3초/500ms로
검토한다. 원인과 최소 수정 제안이 확인되기 전 제품 패치/새 runtime 변경은 하지 않는다.
