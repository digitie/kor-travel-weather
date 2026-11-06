# Weather PR205 최신 코드 이미지 배포

## 반영 범위

최신 main `76f280699b8f5fed39e5ca12412363a0bb6d80f5`(PR205)을 기존 배포 브랜치와 합친
제품 `ba66543d53ac86ad69d17dc124a857f308a912c2`다. API reader guard의 기본 대기를 2초에서
5초로 늘리고 0.5~10초 설정 범위를 적용한다. 배타 guard의 보유 시간을 holder별로 계측하며,
파일 descriptor를 모두 닫은 뒤 계측하고 계측 오류를 본 처리로 전파하지 않는다.

기존 상세 날씨 UI·export 개선·DB0037을 보존한다. 웹 tree·uv.lock·pyproject·Alembic tree는
기존 제품9acc409와 정확히 같다. 웹 이미지를 다시 만들거나 DB migration을 실행하지 않는다.
API·수집기는 현행 이미지와 같은 의존성에 최신 소스를 단일 COPY 레이어로 반영했다.

## 검증과 현재 상태

격리 PostgreSQL18의 frozen export·reader lifetime 시험67개와 소스 ruff를 통과했다.
3초 배타 보유 뒤 reader200, 설정 범위, 보유자별 계측·로그와 기존 읽기 계약을 포함한다.
두 ARM64 이미지 빌드와 native 기본5초·DB0037 계약도 통과했다. 빌드 최초 Invocation
`ac8fba1b19184f00913fb681422e28e1`은 journal done·MainPID0·cgroup 부재다.

API4e59e13·CID07c4e7a4를 실제 포트12201로 교체했고 native Settings의 reader guard5초와
VWorld 키 일치를 값 출력 없이 확인했다. 실제 health/version/지역특보/status/500행 위치는200,
같은 위치의 조건부 요청은304/0바이트다. 재시도0이며 전국·cold 성능 배율의 근거로 쓰지 않는다.
기존 웹508a438·CIDb1d95fc7은 유지한다. Firefox157 PC1440·모바일375의 현재·내일·시간별
그래프·지도와 overflow0·logout200·소유 브라우저 자식 종료·잔여0을 확인했다.

수집기7cabfec은 준비됐으며 기존 수집의 자연 종료를 기다리는 단일 배포 Root가 실행 중이다.
원27일정 복원·수집기 교체·최종 Manager·부모 실제 종료는 아직 완료로 기록하지 않는다.
진행 Root는 `weather-latest-deploy-20261106-3dcbbaf3c7.service`, 첫 Invocation은
`6815146ca14b45b39f189173f7794fd1`이다. 정상G→R/FD9, NVMe, 다른 서비스·설정을 보존하고
백업·복원은 사용자 지시로 생략한다. 새 리뷰·전체 시험 반복·외부 재수집 게이트를 추가하지 않는다.

정본 원문은 Git 밖 `C:/Users/digit/.codex/tmp/weather-latest-release-20261106/checkpoint.json`과
각 build/deployment/live 결과에 보존한다. 이미지 준비와 실제 수집기 반영을 구분한다.
