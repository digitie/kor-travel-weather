# 승인 inventory 예보와 전체 source REST 연결

최신 quota 밖의 승인 source도 frozen vector에 유지한다. provider별 실제 pre-HTTP roster와
마지막 승인 위치 합집합을 읽으며 artifact에서 선택 위치만 출력한다. 요청 때 원천 이력이나
전체 weather_current_values를 다시 스캔하지 않는다.

각 페이지는 동일 RR에서 최신 source intent/승인/좌표/정책과 신선도를 재확인한다.
원천 실패 뒤 이전 커서도 stale이며 정상 ETag 304로 실패를 숨기지 않는다.
known_at은 원천 시각이고 next_since는 공개 watermark다. 모든 페이지 수신 뒤 next_since를
다음 요청의 since로 적용한다. 410이면 첫 페이지부터 다시 받고 304는 같은 표현을 재사용한다.

기본 current는 설정된 KMA 실황·AirKorea·산림청 산악/청정넷·KREX·HTTP3종의 실제
승인 source를 모두 포함한다(7 provider, 8 dataset). KHOA는 예보만 있어 current에서
제외하며 external forecast에 포함한다. 실제 설정에서 비활성화한 제공처만 제외한다.
미지원 활성 provider나 필수 dataset의 최초 미승인은503이다. 청정넷 별도 catalog
권한이 없는 최초 상태는 정상0건으로 꾸미지 않고 전체 source_status를 stale로 표시한다.

기존 KMA SDK가 선택한 실제 격자 그룹을 HTTP 전에 명시하고 native 시작 intent와
단일 dataset을 함께 저장한다. 임의 위치×모든 provider의 곱을 요구하지 않는다.
출력은 불변 source vector 순서이며 전역 location_id 정렬을 약속하지 않는다. 같은
snapshot의 cursor는 중복/누락 없이 그 고정 순서를 따른다.

SDK의 새 station이 사전 canonical roster 밖에서 발견되면 기존 native 이력/catalog는
저장하고 이번 export 승인은 무효화한다. 다음 수집이 새 anchor를 사전에 선언한 뒤
정상 승인하면 복구한다. 운영자가 명시 비활성화한 anchor는 새 발견으로 오인하지 않는다.
정상 빈 응답은 export opt-in에서 한 번 재확인한다. KMA/산악의 SDK 발표/시각 재조회는
기존 public API에 맡긴다. 네트워크 예외의 SDK 재시도를 추가로 중첩하지 않는다.

중기 두 part grouped reader는 별도 PR이다. 공통 STYLE_DATASETS/_prepare_manifest/
_page_items hook으로 같은 since·서명 cursor·TTL·ETag·source 건강 검증을 재사용한다.
W5 eligibility·전체 provider 일간 요약·운영 비용/GC gate는 아직 후속이다.

검증은 실제 PostgreSQL에서 SDK41+7 provider 기본 current/KHOA REST 2건을 함께
43PASS, 빈 응답/새 station 발견과 다음 수집 복구·registry 집중10PASS다. 고정 최종
CI/두 적대 리뷰/실제 TCP/UI gate는 별도로 수용한다. 운영 flag는 OFF를 유지한다.
# 실제 원천의 실패와 namespace

신뢰 상태의 단위는 `source_scope`(provider/dataset/entity의 실제 HTTP 원천)다.
정규화 정책이나 선택 위치가 달라 namespace가 달라도 같은 실제 원천의 현재
실패는 이전 승인 값을 stale로 표시한다. 불변 파일·승인 위치 집합은 유지한다.
현재 owner의 active attempt만 확인하고 claim epoch가 일치해야 하며, 과거에
교체된 attempt나 다른 source_scope의 실패는 해당 승인 집합을 오염시키지 않는다.
native 성공 뒤 Dagster가 확정 FAILURE/CANCELED여서 publisher가 attempt를
failed로 기록한 경우에도 같은 정책으로 건강한 이전 ETag304를 차단한다.
