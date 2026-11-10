# 2026-11-10 — main3bcda7e1 배포·역할별 복호 캐시

사용자 요청으로 main `3bcda7e104508b71106f8905b3043548380f5832`(PR219·220 포함)의 API·수집기 이미지를 실제 반영했다. 기존 웹의 실행 소스는 ea7fda1이며 최신 main과 웹 트리가 같으므로 이미지·CID를 유지했다. 제품을 추가 수정하거나 SDK·웹을 재설치/재빌드하지 않았다.

## 실제 결과

| 역할 | image SHA256 | CID | 복호 캐시 파일 byte / 항목 |
| --- | --- | --- | --- |
| kor-travel-weather-api | `sha256:4c9741b6e7c6d203c4963c2bd03e75cdd6997a61e1be8dccf89171f1c0364812` | `97497fe51e05380cf9d4c972153ecb4a208e36998d6e11e73600bc21aaaf3686` | 67108864 / 8192 |
| kor-travel-weather-dagster-code-server | `sha256:40ad395f6045f841fe7a2d6c6b4079888f5189676eb736b5be4b845ab740edf9` | `ebe39222c8685b9f1101951f427283088818369aadd59a3754301a29d303c262` | 4194304 / 1024 |
| kor-travel-weather-web | `sha256:a70483301f0cc670cbaecdef10c4c3a95930bf5105d6acdcb45f428d30251542` | `449d03eb1e5aee3c742b4ae8f96a0910dc9363dd6a14651cd4294bbdbddca0ee` | 기존 웹 유지 |

API·수집기는 healthy, 실제 포트12201/12206·reader SQL5초·실효 VWorld 키·기존 자원·마운트·healthcheck를 확인했다. 원 Weather27 일정의 각각의 상태와 보호된 환경 파일, 다른 서비스는 전후 동일하다. Manager release `/srv/ktdm-releases/weather-adopt-20261110T121605Z-f07cc2f948a24b668351ae7d1a07849d`의 adopt/verify/show가 healthy다. DB0039 그대로, migration0이다.

## 캐시 설정

`KOR_TRAVEL_WEATHER_EXPORT_DECODE_CACHE_BYTES` / `KOR_TRAVEL_WEATHER_EXPORT_DECODE_CACHE_ENTRIES`를 API에는67,108,864/8192, Dagster code-server에는4,194,304/1024로 지정했다. native import한 모듈 상수와 실제 환경이 같은지 두 역할에서 확인했다. 역할별 정본 Compose override에 보존되며 비공개 환경 원문은 출력/커밋하지 않았다.

이는 프로세스별 파일 byte 기준 복호 캐시 상한이다. 전체 RSS의 강제 상한이나 수집·조회 전체 속도 보장이 아니다. 요청에서 반복 읽는 API는64MiB, hit가 드문 Dagster 및 상속되는 worker 설정은4MiB로 제한한다. PR220의 파일 identity 검증·최근2초 파일 재검증·10분 뒤 hash 재검증·승격 검산 scope는 원래대로다. 상태60초·SQL5초 계약도 바꾸지 않았다.

## 검증과 기존 수집 정산

정확한 main의 [CI42790607663](https://github.com/digitie/kor-travel-weather/actions/runs/42790607663)는 SUCCESS다. 두 이미지의141개 source 파일·설치 package 집합·실제 Cmd/EntryPoint를 검산했다. 빌드 첫Invocation `69ae5bd7aca3474cbbe4f4ea2c3bf645`와 배포 첫Invocation `b9083b0239d344e3bff1bc7fa35a6182`는 모두 실제0/done·MainPID0·cgroup부재이며 소유 자식22/82개가 모두 종료했다.

운영6HTTP는 health/version·공개 위치1·관리 위치1·최근run10·지역특보가 모두200이었다. 각 단일 wall은0.1882/0.0304/0.7655/0.4813/0.1000/0.5091초다. export 전체 순회·수집 처리량·p95·cold1초 달성을 새로 검증한 수치가 아니다. 웹 source·이미지가 같아 직전 PC1440/mobile375 실제 수용을 보존하고 재실행하지 않았다.

기존 인간의 강제 중지 승인 범위에서 Weather9건을 정식 취소 요청했고 실제 terminal은8CANCELED/1FAILURE였다. 다른 서비스 run은 취소하지 않았다. old worker0 뒤 CS를 교체하고 app canonical 회수 변경0·대상 `values_loaded`448,896/11,286 동일을 확인했다. 이는 대상 run 카운터이며 전체 고유 행 수가 아니다. 원 실패·이미 저장된 자료는 그대로다. 백업/복원은 SKIPPED_BY_USER, 원본 삭제·prune·자동rollback0이다.

정본: `C:/Users/digit/.codex/tmp/weather-cache-release-20261110/weather-final-acceptance.json`, SHA256 `c9f39bb2c53d2e8fcd3bbb9640b8997e141b303bf9b822f0a0b3d30bc6898921`.
Transport#179와 Transport Dagster CPU는 별도 진행 중이며 이 Weather 배포 성공으로 완료 처리하지 않는다.
