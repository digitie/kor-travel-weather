# 2026-10-29 최신 Weather 배포 기록

상태: **IN_PROGRESS**. 빌드·배포 진행 중의 초안이다. 완료 여부와 검증 결과는 실제 운영 확인 후 갱신한다.

## 대상과 범위

배포 소스는 최신 원격 main을 확보한 커밋 `ad4ee5350a50804e34b84679be29d7a743f9e443`이다.
깨끗한 체크아웃에서 만든 Git archive를 Odroid NVMe에 전송했다. API·관리 웹·Dagster 코드서버의
이미지를 새로 빌드하며, migrate 역할은 새 API 이미지를 사용한다. 제품 코드는 이 운영 문서 PR에서 변경하지 않는다.

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

## 적용 순서와 보호 범위

호스트 공통 Manager 잠금으로 운영 변경을 직렬화한다. 다른 서비스, 공용 PostgreSQL,
Dagster daemon·webserver는 이번 배포의 교체 대상이 아니다. 배포 전후 컨테이너 ID·이미지·시작 시각을 비교한다.

1. 기존 Weather schedule·sensor의 실행 상태를 기록하고 Weather만 일시 중지한다.
2. `weather_daily_summaries`와 `alembic_version`을 백업하고, 운영 PostgreSQL과 같은 버전의 `pg_restore --list`로 확인한다.
3. `0035_daily_precip_coverage` 데이터 보정을 적용한다. 구간의 일부 RN1 값으로 확정했던 강수 합계·무강수 판정을 null로 바로잡는다.
4. API·관리 웹을 교체하고 이미지 ID·API 버전·로그인 화면을 확인한다. 이 단계에서 기존 코드서버의 수집 작업은 계속 완료할 수 있다.
5. 실행·대기 작업과 실제 worker가 모두 종료된 뒤 코드서버의 상태를 백업하고 이미지를 교체한다.
6. Weather repository를 다시 로드하고 세 역할의 건강 상태·소스·이미지를 확인한다.
7. 원래 실행 중이던 Weather schedule·sensor만 복구한다. 취소된 export publisher와 기존에 중지된 항목은 중지를 유지한다.

0035는 이력 표의 schema를 변경하지 않는 작은 요약 표의 데이터 보정이다. 일반 수집 작업이 저장하는
fact·current 표와 분리되어 있으며, 요약을 쓰는 export publisher를 중지한 상태에서 적용한다.
각 날짜별 갱신은 autocommit하고, statement timeout은 최대 60초, lock timeout은 최대 5초로 제한한다.
실패하면 진단을 남기고 잘못된 부분 합계를 되살리는 자동 downgrade는 하지 않는다.

## 빌드와 리뷰

빌드는 2026-10-29 **22:18:21 KST**에 시작했다. 소스·이미지·빌드 캐시·임시 파일은 NVMe를 사용한다.
운영 실행 이미지가 바뀌었다는 표식은 빌드 시작이나 systemd 시작 성공만으로 기록하지 않는다.

배포 도구는 독립적인 두 검토를 거쳐 수정했다. API·웹과 코드서버 배포를 분리하여 진행 중인 수집을
강제 종료하지 않으며, 코드서버 교체 전에는 논리적 실행 상태와 실제 worker를 함께 검사한다.
진단에는 기존 Manager의 자격 증명 제거 함수를 사용하고, 민감한 값은 공개 보고에 넣지 않는다.

## 실제 결과

아래 항목은 아직 이 초안에 완료 결과가 전달되지 않았다. 완료 후 실제 관측값으로 채운다.

| 확인 항목 | 결과 |
| --- | --- |
| 새 API·웹·코드서버 빌드 및 이미지 ID | 미확인 |
| 표 백업 및 복원 목록 확인 | 미확인 |
| schema 0035 실제 적용·DB readback | 미확인 |
| API 버전·이미지·건강 상태 | 미확인 |
| 관리 웹 공개 접속 및 live E2E UI | 미확인 |
| 코드서버 배수·교체·repository 재로드 | 미확인 |
| 원래 schedule·sensor 복구, publisher 중지 | 미확인 |
| 다른 서비스·공용 DB·Dagster plane 보존 | 미확인 |
| Manager 운영 표식 및 이미지 ID 대조 | 미확인 |

완료 결과를 보강하기 전에는 이 문서를 배포 완료의 증거로 사용하지 않는다.
