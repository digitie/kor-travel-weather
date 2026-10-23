# Map export 실행 설정과 공유 artifact volume

## 문제와 변경

Settings에만 export flag와 경로가 있어 `.env`에 설정해도 Compose의 API·code-server로
전달되지 않았다. 파일도 각 컨테이너의 writable layer에 두면 collector가 저장한
승인 파일을 API가 읽지 못하고 컨테이너 교체 때 잃는다.

두 서비스에 같은 `KOR_TRAVEL_WEATHER_SERVICE_EXPORTS_ENABLED`(기본 false)와
`KOR_TRAVEL_WEATHER_SERVICE_EXPORT_ARTIFACT_ROOT`를 전달하고 같은 named volume
`weather-export-artifacts`를 `/var/lib/kor-travel-weather`에 연결한다. 두 서비스의
artifact 경로는 그 아래 `/var/lib/kor-travel-weather/exports`로 고정한다. Compose가
만드는 volume 루트의 기본 mode에 의존하지 않고 Store가 하위 exports를 0700으로
만들게 한다. 커스텀 override를 쓰면 두 환경변수·mount를 함께 수정해야 한다.

reader도 `.export.lock`을 생성·획득하므로 API를 read-only mount하면 안 된다.
현재 Python image의 API·code-server는 같은 UID(root)를 사용한다. artifact의
디렉터리 0700, lock 0600, 파일 0400을 완화하지 않는다. 향후 UID를 분리하면 공통
그룹·소유권과 실제 교차 컨테이너 읽기를 먼저 검증한다.

## NVMe와 운영 적용

Odroid에서는 Docker data-root와 이 named volume의 실제 mountpoint가 NVMe에 있는지
배포 전에 `docker volume inspect`와 `findmnt -T`로 확인한다. 기존 Manager 배포 승인과
NVMe 정책을 그대로 따른다. eMMC 경로로 fallback하거나 호스트 부팅 조건을 추가하지
않는다. volume은 컨테이너 교체 후에도 유지하며 배포·rollback에서 삭제하지 않는다.
API·collector·publisher는 같은 volume을 사용해야 한다.

이 PR은 실행 설정의 누락만 보강하며 flag를 켜지 않는다. 기본 제공처·중기/외부 승인,
자동 publisher 및 실패 복구, snapshot pin을 보존하는 GC, 최대 wall-clock/RSS,
실제 Map consumer의 최초 전체 수신·304/410/stale 수용이 끝나기 전 운영 활성화하지 않는다.
공유 daemon은 계속 RUNNING이고, 배포 시 Weather instigator만 기존 절차로 관리한다.

## 검증

Compose config에서 두 서비스 flag·경로·mount 일치와 기본 OFF를 확인했다.
기존 배포 계약 21개 PASS. 실제 독립 컨테이너 두 개에서 원본 ExportArtifactStore로
같은 volume에 작성한 뒤 첫 컨테이너를 제거하고 두 번째가 guard를 잡고 hash/byte/count
검증 읽기에 성공했다. 디렉터리 0700·lock 0600·파일 0400을 확인했고 테스트 volume만
정리했다. 원천 호출은 없으며 이 로컬 검증을 Odroid 운영 활성화로 세지 않는다.
정확 최종 CI와 독립 리뷰 2명은 별도 수용 gate다.
