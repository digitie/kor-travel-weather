# 작업 재개 가이드 (Resume)

이 문서는 AI 에이전트가 세션을 중단했다가 복귀하거나 새로운 세션을 시작할 때, 이전 작업 맥락을 즉시 파악하고 이어서 개발할 수 있도록 돕는 징검다리 역할을 한다.

---

## 마지막 작업 상태 (2026-05-31)

- **상태**: `maplibre-vworld-js` 프로젝트의 AI 에이전트 협업 스타일 및 MCP 설정 이식을 95% 완료함.
- **현재 브랜치**: `chore/adopt-vworld-agent-style`
- **완료된 내용**:
  - `.gemini/mcp.json`, `.claude/settings.local.json`, `.codex/config.toml` 등의 로컬 MCP 및 명령어 승인 설정 생성.
  - 루트 경로에 에이전트별 `antigravity.json`, `claude.json`, `codex.json` 설정 생성.
  - 프로젝트 루트에 세션 요약 `CLAUDE.md` 신설.
  - 에이전트 지침서인 `AGENTS.md`와 `SKILL.md`를 상세하게 보완 및 갱신 완료.
  - 에이전트 추적용 연속성 가이드 4종(`docs/decisions.md`, `docs/journal.md`, `docs/tasks.md`, `docs/resume.md`) 신설 완료.

---

## 이어서 진행할 작업 (Next Steps)

1. **품질 게이트 검증 및 테스트**
   - 로컬 환경에서 아래의 빠른 검증 명령 5종 세트를 실행하여, 정적 분석 오류가 없고 모든 단위 테스트가 통과하는지 재차 확인합니다.
   ```bash
   python -m compileall src/airkorea tests
   python -m pytest
   python -m pytest --cov=airkorea --cov-fail-under=90
   python -m ruff check .
   python -m mypy src/airkorea
   ```

2. **Git 형상 관리 및 커밋**
   - 생성 및 보완된 모든 에이전트 설정 파일과 문서들을 스테이징하고 커밋합니다.
   - 커밋 메시지 권장: `chore: adopt maplibre-vworld-js agent style and mcp settings`

3. **PR 생성, 머지 및 리모트 푸시**
   - GitHub CLI를 통해 PR을 생성합니다.
   ```bash
   gh pr create --title "chore: adopt maplibre-vworld-js agent style and mcp settings" --body "Adopt rich agent development style and unified MCP settings from maplibre-vworld-js project."
   ```
   - 생성된 PR을 머지하고 로컬 및 리모트 브랜치를 깔끔하게 정돈합니다.
   ```bash
   gh pr merge --merge --delete-branch
   ```
   - 최종적으로 리모트에 무사히 push/merge 되었는지 확인합니다.
