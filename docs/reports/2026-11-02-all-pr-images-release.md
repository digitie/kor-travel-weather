# 2026-11-02 전체 PR 통합 새 이미지 반영

사용자의 “모든 PR 반영해서 이미지부터 새로 올려” 지시에 따라 열린 제품 PR을 통합하고 새 ARM64 이미지를 반영했다. 동일 코드라는 사유로 기존 이미지를 재사용하지 않았다. 고정 공식 nginx 이미지인 Transport gateway는 최신 구성으로 재생성했다.

- 통합 PR: #197
- 반영 원 PR: #193, #196
- 실제 이미지 제품 소스: `ccaed85630efba4df3220e2797b152d05e100ac4`
- 배포 최초 Invocation: `515ea7cb66274a7f8126ca6118133517` · 실제 정상 종료, Main PID0, cgroup 부재, 소유 자식 종료
- Manager current metadata: `/srv/ktdm-releases/weather-adopt-20261102T151214Z-365c5d2c1d0649358cfcf9788071648d` · adopt/verify/show healthy
- 원 일정: 26개 원래 상태 복원
- 백업·복원: 사용자 지시로 생략. 앱 백업 POST/목록 GET을 실행하지 않았다.
- 추가 독립 리뷰: 사용자 지시로 생략.
- 비공개 지도 키: BuildKit secret/기존 런타임 설정 유지. Git·문서·로그에 값을 기록하지 않았다.

|역할|실제 CID|이미지|역할별 제품 소스|실제 명령 포트|
|---|---|---|---|---|
|kor-travel-weather-web-latest|`be140d8083838adbb440e53e30bca9eb484d23bd457bb7522b5331014002eec5`|`sha256:6ab4d9f1cc4e184baffd8d91710c437fb2131e10d5916309a0903728d0192834`|`950f3ed4a5956b0ff1308311cb821baba37b36c0`|`npm run start -- --hostname 0.0.0.0 --port 12205`|
|kor-travel-weather-api-latest|`d38e8a45afff10c690ed27cd3820c6fbde465074fa09e484c38b4de6500fe809`|`sha256:92ad165322c336605b88600af1bd81cdfff78b13b3bc1b10d000930b4ed742e4`|`ccaed85630efba4df3220e2797b152d05e100ac4`|`uvicorn kortravelweather_api.app:app --host 0.0.0.0 --port 12201`|
|kor-travel-weather-dagster-code-server-latest|`98f0a93fecb9ee58b181f8aaa5a1dc159dfbbda8660236a95606db266c373e44`|`sha256:561da38bee8d233e0a287dced5dddf5159ee694bb68679e535234c50395d984d`|`ccaed85630efba4df3220e2797b152d05e100ac4`|`dagster code-server start -h 127.0.0.1 -p 12206 -m kortravelweather_dagster.definitions --inject-env-vars-from-instance`|

## 검증

- PR193은 제품 기능 PR로 반영했다. PR196의 같은 값 descriptor namespace LRU 기억·변경된 model_copy의 namespace 구분·OFFSET 0 LATERAL의 필요한 JSON 4필드 투영·digest 우선 재사용도 함께 반영했다. 전체 metadata를 JSONB로 일괄 변환하지 않는다.
- 웹·API·수집 서버의 첫 새 Docker 빌드는 모두 실제0으로 종료했다. PR196은 웹/런타임 의존성 변경이 없어 완료된 웹 이미지를 유지하고 API/수집 서버만 새 소스로 추가 빌드했다. 각 역할의 실제 소스 revision은 배포 원장에 따로 기록했다. 최초 추가 빌드의 잠금 경합과 두 번째 빌드의 raw 이미지 ID 참조 실패는 원문을 보존했고, 새 고유 R3에서 고정 로컬 태그의 이미지 동일성을 확인해 실제 빌드를 완료했다.
- 운영 DB는0035_daily_precip_coverage다. 운영 API·수집 서버의 PR196 순수 계약을 실제 이미지 안에서 확인했다.
- Firefox PC1440/mobile375에서 현재·내일 날씨, 작은 세부 정보, 상세 시간별 기온 그래프, 비적용 특보 설명 부재, 정상 VWorld 이미지 타일을 실제 확인했다. 미세먼지·체감·자외선·일출/일몰 등 실제 미제공 항목을 임의 값으로 채우지 않았다.
- PR196 통합 ccaed856의 CI41447895075에서 Python·frontend 모두 성공했다.
- 각 화면 수평 넘침 없음, 로그아웃과 소유 브라우저 프로세스 종료를 확인했다.
- 실제 화면/원문은 Git 밖 작업 원장에 보관했다. 다른 서비스·공용 daemon·비공개 환경 파일을 보존했다.
