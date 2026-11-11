# 공통 UI 전환 뒤 Weather 웹 단독 배포 계획

상태: **NOT_RUN — 후보 배포 금지**. 코드·필수 검사·동일 immutable manifest의 독립 2인 리뷰·CI·개별 PR 머지 뒤 부모의 go를 받아 순차 실행한다. 이 문서는 배포 실행 결과가 아니다.

## 정본과 적용 역할

- 제품 소스: 머지된 `digitie/kor-travel-weather` commit. 로컬 후보 branch는 배포 입력이 아니다.
- Dockerfile: `deploy/Dockerfile.web`, context는 Weather 저장소 root. 프런트만 context로 주면 vendor 산출물 COPY가 실패한다.
- 운영 오케스트레이션: Manager의 `weather` target / `kor-travel-weather-web` 서비스 / `weather-web` 역할. Weather 자체 compose·`deploy/deploy.sh`는 local-dev/e2e와 역사 기록이며 이번 운영 배포에 쓰지 않는다.
- 확인한 Manager registry 소스는 target host **odroid**, 웹 container `kor-travel-weather-web-latest`, host-network process port `12205`, API consumer port `12201`을 명시한다. 기존 n150 런북의 이전 계획 문구보다 현재 registry가 우선한다. read-only 원격 preflight에서 Docker context `default`, compose 작업 경로 `/opt/kor-travel-odroid`, 서비스 `kor-travel-weather-web`, host network와 실제 process port12205를 확인했다. container는 running이지만 Docker healthcheck는 없으므로 HTTP·Manager로 검산한다. go 직전에 다시 확인한다.
- Manager compose의 기본 image는 `kor-travel-weather-web:latest-main`이며 `KOR_TRAVEL_WEATHER_WEB_IMAGE`로 선택한다. 배포 후보는 `kor-travel-weather-web:<merged-sha>-common-ui` 같은 immutable 태그와 digest로 식별하고, 현재 역할의 이미지 참조를 통해 반영한다. 실제 실행의 Image 참조는 mutable 기본 태그가 아니라 `sha256:a70483301f0cc670cbaecdef10c4c3a95930bf5105d6acdcb45f428d30251542`이며 image revision label은 `ea7fda11da34775858d62bc034a5eb40778e3890`이었다. 이 기존 digest를 rollback 입력으로 보존한다. 최종 새 태그·digest는 부모가 확정해 기록한다.

## go 이후 순서

1. Manager의 실제 target host/context와 배포 checkout의 merged SHA, clean 상태, web의 현재 image ID·Cmd·포트·health를 확인한다. 이전 image digest를 rollback 입력으로 남긴다. 포트 14105로 되돌리지 않는다.
2. `common-artifacts.json`의 후보 상태·버전·integrity와 최종 freeze manifest를 대조한다. 같은 tgz bytes와 lock integrity를 검사한 commit만 사용한다. 최종 통합 전 변경된 후보를 사용하지 않는다.
3. 기존 보호 환경 파일·세션 secret·UI password/hash·서버 admin token·trusted proxy/origin을 그대로 보존한다. 값·환경 dump·SSH 인증값은 stdout·문서·commit에 남기지 않는다. 필요하면 값 대신 일치 여부만 확인한다.
4. 공개 지도 식별자 `NEXT_PUBLIC_VWORLD_API_KEY`와 공개 Dagster URL은 기존 배포 설정을 빌드 입력으로 사용한다. Next의 공개 값은 빌드 시 고정되므로 runtime 환경만 보고 일치한다고 판단하지 않는다. 로그에도 원문 값을 출력하지 않는다.
5. 기존 Manager의 승인된 build/redeploy 경로에서 **web 역할만** 빌드·교체한다. `--no-deps`에 해당하는 역할 경계를 사용하고 API·code-server·공유 Dagster·DB·RustFS·migration·schedule의 lifecycle은 실행하지 않는다. 전체 target up/down·volume 조작·데이터 삭제는 없다.
6. 새 image ID·소스 SHA·실행 Cmd `12205`·컨테이너/Manager health를 확인하고, 최초 배포 Invocation과 소유 자식이 모두 terminal인지 확인한다. HTTP 200만으로 소스 일치를 주장하지 않는다.
7. 실제 HTTPS `/login`에서 기존 계정으로 로그인하고 `/weather`의 지도·종류/출처 필터·목록·선택 상세·시간별 표/그래프·안내 닫기/초점 복원을 PC와 모바일에서 확인한다. 위치 목록·실행 목록/원본 상세·Dagster 주소 표는 읽기만 확인한다. 데이터 생성/활성화 변경/주소 갱신 launch는 운영 smoke에서 하지 않는다.
8. 기존 API `/health`·`/version`, 로그인 없는 관리 접근 차단, proxy 정상 응답을 확인한다. API image/commit은 이번 웹 배포 전후 동일해야 한다. DB·RustFS container/volume·설정/일정의 보존도 일치 여부로만 기록한다.
9. 실패하면 이전 **web 이미지**로 같은 역할만 되돌리고 같은 smoke를 수행한다. DB 복원이나 migration rollback은 없다. 결과·image/digest·최초 Invocation terminal·실제 smoke·미실행 항목을 한국어 기록에 남긴다.

## 정본 참고

- `deploy/Dockerfile.web`
- `deploy/README.md`, `deploy/n150.md`, `docs/runbooks/docker-app.md` (역사/로컬 경계 확인)
- 부모 소유 Manager `config/docker-targets.yml`의 weather target과 web container, `docker-compose.yml`의 web build/command/env 계약 (읽기만 확인)

최종 push·독립 리뷰·PR·merge·운영 배포 승인 판단은 부모가 담당한다.

## read-only baseline 결과

2026-11-11 사전 조회에서 현재 웹 `/login`, 기존 API `/health`·`/version`은 각각HTTP200이었다.
환경값·인증값은 조회/출력하지 않았다. 이 결과는 **기존 이미지의 배포 전 baseline**이며,
새 이미지 배포·실제 계정 로그인·대표 화면 수용을 완료했다는 뜻이 아니다. container/image/HTTP 조회만
수행했고 build·restart·compose up/down·DB/RustFS lifecycle은 실행하지 않았다.
