# Weather 현재 Dagster 배포 확인과 export 취소 정산

기준은 2026-10-27 12:08–12:16 UTC의 새 운영 관측이다. 이전95020 배포와173 정산을 현재로 복사하지 않았다. 이번 작업에서 새 image build·교체·migration·수집 강제 실행은 하지 않았다.

## 실제 버전과 중복 배포 생략

| 역할 | 실제 source | 상태 |
|---|---|---|
| API | `e39362a3803272e14bbd033359d0fe377b7441e0`(PR176 merge) | running/healthy, restart0 |
| Dagster code-server | `e39362a3803272e14bbd033359d0fe377b7441e0` | running/healthy, restart0 |
| web | `5098d2d80486df7e23753296e66b38ba8045660f`(PR177 merge) | running, healthcheck 없음, restart0 |
| Manager 설치 | `aeb2d34ce276e5e219761a62cd4b1c8f39cad72a` | 원 설치 보존 |

현재 main5098과 실제 API/collector e393의 차이는 문서2개와 로그인 UI 시험·package/lock·vendor dev.9뿐이다. Python core·Dagster·pyproject·uv.lock·Python Dockerfile·migration 입력은 같다. 따라서 #174의 CommonAA와 단기예보 정리 개선은 이미 운영 이미지에 포함됐으며 재빌드·교체하지 않았다. 웹도 최신5098이어서 교체할 것이 없다. 원본 checkout의 미추적 Prometheus .db4개는 손대지 않고 별도 worktree에서 문서만 작성한다.

## 실제 Common·schema·정본 bind

API와 code-server 각각에서 Common distribution의 실제 VCS commit이 `aa232ad5f255233184de80eceaab2e9b6ccb00c9`, Python source19개 hash가 검토한 정본과 완전히 같음을 확인했다. metrics/retention/repository/definitions/pyproject/uv.lock6개도 e393 Git blob SHA와 일치했다. 이것은 source 설치 증거이며 모든 Common 분기의 실제 실행이나 CPU/RSS 감소를 증명하지 않는다.

실제 API Cmd는 `0.0.0.0:12201`이었다. 그 정본 포트의 health/version GET은200이며 version은e393이다. 최초 역사 포트14101의 URLError는 API 장애 증거가 아니므로 성공 수용으로도 대체하지 않고 이력으로 분리한다. API의 실제 DB에서 READ ONLY transaction/3초 statement·0.5초 lock cap으로 schema `0034_marker_kind_indexes`를 확인했다.

Manager 실제 release와 DockerRoot의 findmnt는 `/dev/nvme0n1` ext4였다. 앱/이미지 재배치나 부팅 조건을 추가하지 않았다. 읽기 전후 Weather3+공용 plane3+PG의7개 CID/image/start/restart가 같았고 공용 daemon은 계속 running/healthy였다. 온도·주파수·다른 서비스 이미지·HAProxy·SameSite 설정은 변경하지 않았다.

## 자연 단기예보 정리 관측

정본 `short_forecast_retention_every_ten_minutes` schedule tag와 Weather location을 가진 run `3ea573eb-3fd3-4525-9753-05b3ee3b3678`은 SUCCESS(156.153초)였다. 다음 `cce472b6-1aea-41c6-aa60-361f5bf1a237`은 관측 시 STARTED였다. 강제 run은0이다. 이 표본만으로 모든 backlog가 비었거나 장기 실패가 전부 없어졌다고 주장하지 않는다. 삭제 행수·lag 감소·CPU/RSS는 이번 관측에 포함하지 않았다.

## 사용자 취소: export desired STOPPED

사용자가 Weather export 작업 취소를 명시해, 실제 snapshot에서 RUNNING이던 **`weather_export_publisher` sensor 하나만** 정본 Dagster stopSensor로 STOPPED로 바꿨다(12:11:53 UTC). 정확 sensor ID와 readback을 대조했고, 전체47 native 일정 중 나머지 ID/상태는 완전히 같았다. flags·기존 API·원천 데이터·다른 일정·공용 daemon은 바꾸지 않았다.

중지 직전 시작된 tick111545(12:11:36 UTC)는 강제 종료하지 않았다. 이후12:16 관측에서 같은 tick이 자연 SKIPPED로 끝났고 sensor는 STOPPED였다. 이 publisher는 sensor 본문에서 직접 평가하며 RunRequest로 별도 export job을 만드는 구조가 아니다. 임의 job/worker kill이나 오래된 메타데이터 종료 처리는 하지 않았다.

**이 sensor의 desired state는 앞으로도 STOPPED다.** 향후 배포의 일반 original-RUNNING 복원 규칙보다 사용자 취소가 우선하며, 이전 RUNNING snapshot을 이유로 다시 켜지 않는다. export 기능 코드/기존 API를 삭제하거나 Map consumer/profile/설정을 활성화하지 않는다. 원래 STOPPED인 native incarnation recovery도 그대로다.

## 공개 UI와 수용 범위

현재web5098/API e393에 결박한 기존21사례 공개 HTTPS UI 검증을 준비했다. 검토된 driver/check/시간/cleanup은 유지하고, 정확7runtime·immutable browser image·known password offline검증 뒤에만 HTTP를 허용한다. **현재 새 실제 UI21은 아직 실행 전이며 수용 대기다.** 기존95020 UI21이나 Map의 UI 결과를 현재Weather 성공으로 승계하지 않는다. 두 source 리뷰와 n150 browser 슬롯 뒤 한 번 수행하고 원 실패/성공·자기 session/container 정리를 별도로 정산한다.

이21사례는 정상 로그인·메뉴·날씨 지도/목록·로그아웃 회귀다. SSR 직후 script 강제 정지·저장 계정 자동완성·모든 수집/성능 회귀를 대신하지 않는다.

## 원장 결박

| 원장 | SHA256 |
|---|---|
| weather-dagster-resume-current-readonly-actual-w-r1.json | c4ae182c3666e6fbea7b2cc7ced9f82fa16ca75743a24abc736f4bf7ffd4266f |
| weather-export-cancel-exact-sensor-actual-w-r1.json | 7b9e0ccc2c7da8d2c0cc622b81910a210e94621f0e2a900963ee7eb672cd2f51 |
| weather-dagster-resume-metadata-readonly-actual-w-r1.json | 18c2f6e58b7fb4f2cb6a1d5c153f5b3a3e981b74b6253c5830180a9825306b20 |
| weather-current-public21-source-preparation-w-r1.json | 23290320ba578c3cc7df56c8fe676d1a9a8e31c82451c6916d82e4c96a344517 |

원장은 운영 감사 디렉터리의 비밀 없는 선택 필드/metadata/hash 증빙이다. 비밀번호·쿠키·원 env·provider payload를 문서에 옮기지 않는다.
