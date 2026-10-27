# odroid weather "현재 release" 표지 제안 — **Codex 합의 필요**

> **상태: 제안(미적용).** odroid 배포를 맡는 Codex와 합의하기 전에는 odroid의 어떤 파일·
> 컨테이너·compose도 바꾸지 않는다. 이 문서 작성 중에도 odroid에는 아무것도 바꾸지 않았다.
> 합의되면 Codex 쪽 배포 절차와 이 저장소 `docs/runbooks/`에 같은 문구를 옮기고, 첫 적용은
> Codex가 한다.

## 문제

odroid에는 **지금 운영 중인 weather release가 무엇인지 적힌 곳이 없다.**

- `/opt/kor-travel-odroid/compose.yml`의 weather 이미지 기본값은 여전히 weather128/130
  이미지를 가리킨다.
- 각 release의 이미지 목록은 `/srv/ktdm-releases/weather<PR>-<sha>-<date>[-rN]/images.yml`에
  있지만, 그 파일을 참조하는 것은 **실행 중인 컨테이너의 compose label뿐**이다(compose가
  기동 때 받은 `-f` 목록을 label로 남긴다).
- 그래서 가장 최신 `images.yml`을 빠뜨린 채 `docker compose up -d`(재생성)를 하면 오류 없이
  **옛 이미지로 조용히 되돌아간다.** 어느 release가 최신인지는 디렉터리 이름의 PR 번호·날짜와
  컨테이너 label을 사람이 대조해야 알 수 있다.

2026-10-27 실제로 이 때문에 Codex의 weather173 배포와 우리 쪽 weather176 배포가 서로의
release를 모른 채 끼어들었다(어느 쪽이 "현재"인지 기록이 없어 각자 자기 `images.yml`로
재생성했다).

## 제안

**release 디렉터리를 가리키는 표지 하나를 두고, 모든 배포가 그것만 갱신하며, 문서화된 재생성
명령은 항상 그 표지를 통해 `images.yml`을 읽는다.**

- 표지: `/srv/ktdm-releases/weather-current` → 운영 중인 release 디렉터리(symlink, 상대 경로).
- 갱신은 원자적으로 한다: 새 symlink를 임시 이름으로 만든 뒤 `mv -T`로 바꿔 끼운다
  (rename(2)라 읽는 쪽은 옛 대상이나 새 대상 둘 중 하나만 본다).
- 옛 release 디렉터리는 지우지 않는다. 되돌리기는 표지를 옛 디렉터리로 다시 돌리고 같은 재생성
  명령을 실행하는 것이다.
- 표지 갱신은 **새 이미지로 재생성해 health를 확인한 뒤**에 한다. 재생성이 실패하면 표지는
  그대로라서 다음 사람의 재생성이 마지막 정상 release로 돌아간다.
- `compose.yml`의 기본값은 그대로 둔다(이 제안 범위 밖). 대신 재생성 명령이 표지의
  `images.yml`을 항상 겹쳐 읽으므로 기본값에 떨어지는 경로가 없어진다.

### 보조 점검(선택)

배포 직전·직후에 "실행 중인 컨테이너가 쓰는 `images.yml`"과 표지가 가리키는 파일이 같은지
비교해 다르면 멈춘다. 다르면 누군가 표지 없이 배포했거나 표지가 갱신되지 않았다는 뜻이다.

## runbook 문구(합의 시 그대로 옮길 초안)

> ### weather 재생성·배포 (odroid)
>
> 운영 중인 release는 `/srv/ktdm-releases/weather-current`가 가리키는 디렉터리 하나다.
> 다른 곳(compose.yml 기본값, 디렉터리 이름의 번호)을 현재 release로 읽지 않는다.
>
> **재생성(이미지 변경 없음)** — 항상 표지의 `images.yml`을 겹쳐 쓴다.
>
> ```bash
> CUR=/srv/ktdm-releases/weather-current
> test -f "$CUR/images.yml" || { echo "weather-current 없음 — 재생성 금지"; exit 1; }
> readlink -f "$CUR"   # 기록용: 어느 release로 재생성하는지
> docker compose -f /opt/kor-travel-odroid/compose.yml -f "$CUR/images.yml" \
>   up -d <weather 서비스들>
> ```
>
> **새 release 배포**
>
> 1. 새 디렉터리 `/srv/ktdm-releases/weather<PR>-<sha12>-<YYYYMMDD>[-rN]/`에 `images.yml`을 둔다.
> 2. 시작 전 점검: 실행 중인 weather 컨테이너의 compose label이 가리키는 `images.yml`이
>    `readlink -f /srv/ktdm-releases/weather-current`의 `images.yml`과 같은지 본다.
>    다르면 멈추고 마지막 배포자와 확인한다(다른 배포가 진행 중이거나 표지가 누락됐다).
> 3. 새 `images.yml`로 재생성하고 health를 확인한다.
>
>    ```bash
>    NEW=/srv/ktdm-releases/weather<PR>-<sha12>-<YYYYMMDD>[-rN]
>    docker compose -f /opt/kor-travel-odroid/compose.yml -f "$NEW/images.yml" \
>      up -d <weather 서비스들>
>    ```
>
> 4. health 확인 뒤에만 표지를 원자적으로 바꾼다.
>
>    ```bash
>    cd /srv/ktdm-releases
>    ln -sfn "$(basename "$NEW")" .weather-current.tmp
>    mv -T .weather-current.tmp weather-current
>    readlink weather-current   # 새 디렉터리 이름이 나와야 한다
>    ```
>
> 5. 배포 기록(보고서·journal)에 바꾸기 전·후 표지 대상을 남긴다.
>
> **되돌리기** — 표지를 옛 release로 같은 방식(4단계)으로 돌린 뒤 "재생성" 명령을 실행한다.
> health가 회복된 것을 확인한다. 옛 release 디렉터리는 지우지 않는다.

## 도입 순서(합의 뒤)

1. Codex가 현재 실행 중인 weather 컨테이너 label에서 실제 `images.yml`을 확인하고, 그
   디렉터리로 `weather-current`를 처음 만든다(이미지 교체 없음).
2. Codex 배포 절차와 이 저장소 runbook(`docs/runbooks/docker-app.md` 또는 odroid 절)에 위
   문구를 넣는다. 양쪽 문구가 같아야 한다.
3. 다음 weather 배포부터 표지 갱신을 필수로 한다.

## 합의가 필요한 점

- 표지 이름·위치(`/srv/ktdm-releases/weather-current`)와 symlink 방식이 Codex 배포 도구와
  맞는지. 도구가 디렉터리를 직접 가리켜야 한다면 symlink 대신 `weather-current.txt`(대상
  디렉터리 이름 한 줄, `mv`로 원자 교체)도 같은 효과다.
- 2단계 시작 전 점검을 "멈춤"으로 둘지 "경고"로 둘지.
- 다른 서비스(geo·transport)도 같은 문제를 가지는지와, 같은 규칙으로 넓힐지. 이 문서는
  weather만 제안한다.
