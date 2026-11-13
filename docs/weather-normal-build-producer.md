# Weather 정상 이미지 producer 계약

scripts/build-images.py는 reviewed source의 API·code server·frontend 이미지를 기존
Dockerfile로 정상 빌드한다. retag·SSH·Compose·배포·DB·migration 기능은 없다.
source/receipt가 맞아도 운영 적용 승인은 아니다.

## 입력과 역할

Python 3.12 이상, Git, native Docker daemon이 필요하다. 설치·기본 builder/context 변경은
하지 않는다. architecture arm64/amd64는 실제 daemon과 같아야 하며 QEMU fallback은 없다.
output docker만 지원한다.

| CLI/receipt role | Dockerfile / target | 고정 buildarg |
| --- | --- | --- |
| api | deploy/Dockerfile.python / 마지막 stage | INSTALL_DAGSTER=0, GIT_COMMIT=revision |
| code-server | 같은 Python Dockerfile / 마지막 stage | INSTALL_DAGSTER=1, GIT_COMMIT=revision |
| frontend | deploy/Dockerfile.web / runner | 아래 공개 입력 두 개 |

frontend 공개 입력은 NEXT_PUBLIC_VWORLD_API_KEY, NEXT_PUBLIC_DAGSTER_URL이다.
현재 승인된 compiled-input hash 기준을 정식 등록 도구로 확인하고, 두 값을 JSON stdin으로
전달한다. 값은 argv/receipt에 넣지 않으며 build log에서 치환한다. 환경 전체나 DB·인증
비밀을 입력하지 않는다. URL userinfo/query/fragment와 개행/NUL은 거부한다.

~~~sh
# 승인된 도구가 만든 JSON을 stdin으로 연결한다. 실제 값은 명령에 적지 않는다.
python3 scripts/build-images.py --repo /path/to/canonical-clean-repo \
  --revision FULL_REVIEWED_MERGED_SHA --expected-tree FULL_TREE_SHA \
  --role api --role code-server --role frontend --architecture arm64 \
  --output docker --output-dir /path/outside-repo/new-private-build \
  --public-inputs -
~~~

SHA는 실제 40자리 값을 사용한다. repo root/HEAD/tree/clean/origin을 검증하며 alternates·
grafts·외부 Git context를 거부한다. 원격 main을 자동 추적하거나 checkout/fetch하지 않는다.
실제 merge와 승인 근거는 공식 release admission이 별도로 확인한다. 빌드 중 main 전진을
이유로 frozen HEAD/tree를 임의 재결박하지 않는다.

## 정상 빌드와 검증

Git archive를 core.autocrlf=false로 생성하고 파일 blob/mode와 gitlink를 기록한다.
symlink/special archive member는 거부한다. outer output은 private하게 유지하며 archive의
directory traversal mode를 복원해 restrictive umask가 non-root 정적파일을 가리지 않게 한다.
checkout의 임의 파일이 아닌 이 context를 빌드한다. 현재 Dockerfile COPY와 dockerignore의
지원 문법만 해석하고 source/COPY 목록·SHA256·context metadata를 각 역할 전후 검산한다.

실제 docker build --iidfile 후 loaded image ID/platform/OCI revision·source.git·source-tree·
role·COPY label을 검증한다. API와 CS는 별도 빌드이며 같은 image로 간주하지 않는다.
Python 기본 KOR_TRAVEL_WEATHER_GIT_COMMIT을 실제 config에서 확인한다. frontend는 기존
USER node를 요구한다. 소스에 없는 frontend release ENV를 만들지 않는다.

정확한 own image의 network-none 일회성 container에서 기존 기본 USER로 import/파일 probe를
수행한다. entrypoint를 지정하는 probe이므로 기본 CMD·HTTP·gRPC 수용과 구분한다.
Python native imports·checked-hash stdlib cache·copied raw Python source를 확인한다.
frontend는 Node/npm/UID, worker/config 가독성, image cache 쓰기, vendor SRI와 공통 6패키지
전체 설치 파일 SHA256, compiled route target과 두 공개 입력의 번들 존재/hash를 확인한다.
runner에 raw UI source가 있다고 주장하지 않는다.

현재 C11 inventory는 6패키지 263파일이며 ledger/tgz에서 동적으로 산출한다.
원 JS/CSS나 키 문자열을 보고서에 저장하지 않는다. 실제 화면·로그인·지도·API·schema0040
수용 및 운영 approved baseline 비교는 공식 SDK/QA의 별도 gate다.

취소·읽기 timeout 시 EOF 뒤 wait와 leader 종료 여부에 관계없이 own client group의
live 멤버만 TERM → 단계별 최대15초 → KILL로 정리하고 종료를 확인한다. PID/session
불일치는 신호 전에 거부하며, leader와 이미 입양된 같은 group의 자식만 reap한다.
foreign group과 Docker daemon 작업은 이 client 정리의 대상이 아니다.
runtime probe 실패는 stdout/stderr를 공개 입력 치환 후 private 진단에 보존하고 exact
image+nonce label로 검증한 own CID만 정리한다. 실패 재시도는 새 output을 사용한다.
global prune/다른 container/daemon 변경은 없다.

## receipt와 SDK 연계

build-receipt.json: version 1, kind weather-normal-build, mode NORMAL_BUILD_ONLY,
output docker, normalDockerBuild=true, actualApplyAllowed=false, deployReady=false.
source는 canonical .git URL, release_sha/tree는 frozen 실제 입력이다.
images/artifacts key는 선택한 api/code-server/frontend와 같다. 부분 빌드는 fullstack 완료가 아니다.

artifact에는 imageID, loaded=true, normalDockerBuild=true, architecture, COPY.sha256,
publicInputHashes, runtime audit와 redacted log hash를 담는다. OCI source/revision 외 label은
io.kor-travel-weather.source-tree/build-role/copy-sha256이고 공개 입력은
io.kor-travel-weather.public-input-<lowercase-name> hash다.
현재 승인 baseline의 compiled public hash 비교는 정식 SDK admission의 책임이다.

Aquinas의 SDK Weather core adapter가 같은 계약을 소비한다. 기존 normal/prebuilt receipt가
source of truth이며 별도 운영 provenance를 만들어 채택하지 않는다. 현재 DB revision,
검증된 backup/restore, 사전 quiescence/drain, 선택 migration one-shot, API/CS/UI transaction,
nonselected 보존은 이 도구 밖이다. scheduler resume은 별도 공식 operator gate이며 생략을
PASS로 처리하지 않는다. 운영 검토·CI·실제 merge 전 제품 이미지 빌드/적용을 하지 않는다.

## 검증과 미실행 구분

~~~sh
python3 -B -m unittest discover -s tests -p test_normal_image_build.py -v
ruff check scripts/build-images.py scripts/weather-build-contract.py tests/test_normal_image_build.py
~~~

실제 Git/archive/umask/vendor 검증과 mocked Docker orchestration을 구분한다.
순수 테스트 mock image/runtime은 실제 이미지·ABI·HTTP·배포 PASS 증거가 아니다.
실제 normal image/runtime/browser 수용은 승인 슬롯에서 최종 merged source로 수행한다.
Transport193 archive/COPY/EOF 취소 패턴을 GPL-3.0-or-later 조건으로 재사용했다.
C11·라이브러리·lock·Dockerfile·앱·CI 순서와 다른 프로젝트는 변경하지 않는다.
