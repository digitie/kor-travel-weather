"""승인된 예보를 위치별 배열로 읽는다. 날짜 이력/current 표를 스캔하지 않는다."""

import json
from collections import defaultdict
from copy import copy
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, text

from .export_capacity import (
    MANIFEST_STATE_RESERVE,
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_SCOPES,
    ExportCapacityExceeded,
    MetadataBudget,
    bounded_manifest_digest,
    read_generation_refs,
)
from .export_database_artifacts import MAX_ARTIFACT_READ_BYTES
from .export_heads import ScopeDescriptor, _digest
from .export_inventory_selection import catalog_source_projection
from .export_read_window import ArtifactReadWindow, forecast_candidates
from .export_snapshots import (
    MAX_PAGE_ARTIFACTS,
    MAX_PAGE_BYTES,
    MAX_PAGE_SCOPES,
    MAX_SNAPSHOT_LOCATIONS,
    PAGE_META_RESERVE,
    ExportCursorInvalid,
    ExportSnapshotExpired,
    ExportSnapshotUnavailable,
    FrozenCurrentExports,
    ensure_snapshot_files,
    snapshot_expires_at,
)
from .read_performance import measured_stage, read_stage

MAX_PAGE_INPUT_BYTES = MAX_ARTIFACT_READ_BYTES


def _freshness(heads, now):
    """snapshot meta의 수집·revision 시각. 신선도 창 안 승인만 센다(없으면 전체)."""
    from .export_inventory_selection import counts_for_freshness

    heads = list(heads)
    fresh = [h for h in heads if counts_for_freshness(h.source_collected_at, now)] or heads
    return {
        "source_collected_at": min(h.source_collected_at for h in fresh),
        "data_revision_at": min(h.data_revision_at for h in fresh),
    }

class FrozenForecastExports(FrozenCurrentExports):
    STYLE_DATASETS = {
        "ultra_short": "kma_ultra_short_forecast",
        "short": "kma_short_forecast",
        "external": "kma_short_forecast",
    }

    def __init__(self, *args, style, since=None, **kwargs):
        super().__init__(*args, **kwargs)
        if style not in self.STYLE_DATASETS:
            raise ExportSnapshotUnavailable("해당 예보 style의 전체 원천이 준비되지 않았습니다.")
        if since is not None:
            since = since.astimezone(UTC)
        self.style, self.since = style, since
        self.dataset = self.STYLE_DATASETS[style]
        # 이번 요청 페이지에서 미래 예보가 없어 뺀 위치가 있으면 stale로 알린다.
        self._expired_omitted = False
        self.selection = {
            "kind": "forecasts",
            "style": style,
            "since": since.isoformat() if since else None,
        }
        if self.source_inventory:
            self.selection.update(
                source_inventory=1, provider_signature=_digest([
                    self.current_providers, self.expected_sources, self.optional_sources,
                ]),
            )
        if style == "external":
            from .export_external import ADAPTERS

            if not self.source_inventory and (
                not self.current_providers or not set(self.current_providers).issubset(ADAPTERS)
            ):
                raise ExportSnapshotUnavailable("요청 외부 provider 전체가 준비되지 않았습니다.")
            self.selection["providers"] = list(self.current_providers)
            if not self.source_inventory:
                self.selection["provider_signature"] = _digest(self.current_providers)
        self.resource = f"forecasts:{style}:{_digest(self.selection)}"

    def _catalog_source(self, catalog):
        # KMA 단기/초단기 anchor는 좌표와 grid만 쓴다. SDK/mid JSON은 그대로 둔다.
        expected = {("python-kma-api", self.dataset)}
        if (
            self.source_inventory and self.style in {"short", "ultra_short"}
            and self.current_providers == ("python-kma-api",)
            and set(self.expected_sources) == expected
            and set(self.optional_sources).issubset(expected)
        ):
            return catalog, []
        return catalog_source_projection(catalog)

    @measured_stage("freeze")
    def _freeze_forecasts(self):
        now = datetime.now(UTC)
        anchor = now.replace(minute=0, second=0, microsecond=0)
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as c,
            c.begin(),
        ):
            c.execute(text("SET LOCAL statement_timeout = '5s'"))
            clock = self.heads.metadata.tables["weather_export_publication_clock"]
            watermark = c.scalar(select(clock.c.published_at).where(clock.c.clock_id == 1))
            if watermark is None:
                raise ExportSnapshotUnavailable("공개 watermark가 없습니다.")
            if self.since is not None and self.since > watermark:
                raise ExportCursorInvalid("아직 발급되지 않은 publication watermark입니다.")
            catalog = self.heads.metadata.tables["weather_locations"]
            source_from, source_columns = (
                self._catalog_source(catalog) if self.source_inventory else (catalog, [])
            )
            roster = c.execute(
                select(
                    catalog.c.location_id,
                    catalog.c.nx,
                    catalog.c.ny,
                    catalog.c.latitude,
                    catalog.c.longitude,
                    *source_columns,
                )
                .select_from(source_from)
                .where(catalog.c.enabled.is_(True))
                .order_by(catalog.c.location_id)
                .limit(MAX_SNAPSHOT_LOCATIONS + 1)
            ).all()
            if not roster or len(roster) > MAX_SNAPSHOT_LOCATIONS:
                raise ExportSnapshotUnavailable("활성 위치 전체가 준비되지 않았습니다.")
            groups = defaultdict(list)
            for row in roster:
                key, nx, ny = row.location_id, row.nx, row.ny
                if self.source_inventory:
                    continue
                if self.style == "external":
                    continue
                if nx is None or ny is None:
                    raise ExportSnapshotUnavailable("위치 grid가 준비되지 않았습니다.")
                groups[nx, ny].append(key)
            if any(len(keys) > 50 for keys in groups.values()):
                raise ExportSnapshotUnavailable(
                    "50개 위치를 넘는 예보 grid는 아직 준비되지 않았습니다."
                )
            descriptors = [
                ScopeDescriptor(
                    dataset_key=self.dataset,
                    forecast_style=self.style,
                    entity_type="weather_response",
                    entity_id=f"grid:{nx}:{ny}",
                    nx=nx,
                    ny=ny,
                    location_ids=tuple(sorted(keys)),
                    normalizer_revision="kma-v1",
                    winner_policy_revision="kma-base-v1",
                    coverage_contract="kma-grid-forecast-v1",
                )
                for (nx, ny), keys in sorted(groups.items())
            ]
            selected, unconfirmed = None, False
            if self.source_inventory:
                descriptors, selected_heads, selected, unconfirmed = self._select_current_scopes(
                    c, [row._mapping for row in roster],
                )
            elif self.style == "external":
                from .export_artifacts import ExportArtifactError
                from .export_external_cohorts import selected_external_descriptors

                try:
                    descriptors = selected_external_descriptors(
                        c,
                        self.heads.metadata,
                        [r._mapping for r in roster],
                        self.current_providers,
                        forecast=True,
                    )
                except (ExportArtifactError, KeyError, ValueError, TypeError) as exc:
                    raise ExportSnapshotUnavailable(
                        "외부 예보 선택 집합이 준비되지 않았습니다."
                    ) from exc
            if len(descriptors) > MAX_SOURCE_SCOPES:
                raise ExportSnapshotUnavailable("예보 source vector 상한을 넘었습니다.")
            heads = {} if self.source_inventory else {
                r.namespace: r
                for r in c.execute(
                    select(
                        self.heads.heads.c.namespace,
                        self.heads.heads.c.generation_id,
                        self.heads.heads.c.source_collected_at,
                        self.heads.heads.c.data_revision_at,
                        self.heads.generations.c.created_at,
                    )
                    .join(
                        self.heads.generations,
                        self.heads.generations.c.generation_id == self.heads.heads.c.generation_id,
                    )
                    .where(self.heads.heads.c.namespace.in_([d.namespace for d in descriptors]))
                )
            }
            if self.source_inventory:
                heads = {namespace: SimpleNamespace(
                    **head, created_at=head["published_at"],
                ) for namespace, head in selected_heads.items()}
            if len(heads) != len(descriptors):
                raise ExportSnapshotUnavailable("모든 위치의 승인된 예보가 필요합니다.")
            refs = read_generation_refs(
                c, self.heads.refs, [r.generation_id for r in heads.values()],
            )
            scopes = []
            budget = MetadataBudget()
            for d in descriptors:
                head = heads[d.namespace]
                chunks = refs[head.generation_id]
                if (
                    not chunks
                    or len(chunks) > 64
                    or sum(r["byte_count"] for r in chunks) > 128 * 1024 * 1024
                    or any(not r["location_ids"] for r in chunks)
                    or set(k for r in chunks for k in r["location_ids"]) != set(d.location_ids)
                ):
                    raise ExportSnapshotUnavailable(
                        "예보 위치별 chunk index가 준비되지 않았습니다."
                    )
                scopes.append(
                    {
                        "namespace": d.namespace,
                        "source_scope": d.source_scope,
                        "generation_id": head.generation_id,
                        "nx": getattr(d, "nx", None),
                        "ny": getattr(d, "ny", None),
                        "provider": d.provider,
                        "forecast_style": d.forecast_style,
                        "location_ids": list(selected[d.namespace] if selected else d.location_ids),
                        "approved_location_ids": list(d.location_ids),
                        "dataset_key": d.dataset_key,
                        "source_part": getattr(d, "source_part", "main"),
                        "refs": chunks,
                        "published_at": head.created_at.isoformat(),
                        "source_collected_at": head.source_collected_at.isoformat(),
                        "data_revision_at": head.data_revision_at.isoformat(),
                    }
                )
                budget.add_bounded_record(scopes[-1], MAX_MANIFEST_BYTES)
            manifest = {
                "selection": self.selection,
                "as_of": anchor.isoformat(),
                "scopes": scopes,
                "next_since": watermark.isoformat(),
                "unconfirmed_sources": unconfirmed,
            }
            manifest = self._prepare_manifest(manifest)
            digest = bounded_manifest_digest(
                manifest, MAX_MANIFEST_BYTES - MANIFEST_STATE_RESERVE,
            )
            # 재사용 조건은 그대로다(같은 digest의 유효 handle이 그 selection의 가장 최근 발급).
            # digest를 먼저 index로 찾고 그보다 새 행만 selection 조건으로 해석한다. 예전처럼
            # selection 조건으로 표 전체를 먼저 훑으면 행마다 큰 manifest json을 조건 수만큼 해석해
            # 5초 상한을 넘겼다 (2026-11-02 odroid: 한 조건으로 65행 약 3.7초).
            previous = (
                c.execute(
                    select(self.snapshots)
                    .where(
                        self.snapshots.c.manifest_digest == digest,
                        self.snapshots.c.expires_at > now,
                    )
                    .order_by(self.snapshots.c.issue_order.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None and c.scalar(
                select(self.snapshots.c.issue_order).where(
                    self.snapshots.c.issue_order > previous["issue_order"],
                    *self._same_selection(),
                ).limit(1)
            ) is None:
                return {**previous, "_reused": True}
            row = {
                "snapshot_id": str(uuid4()),
                "manifest_digest": digest,
                "manifest": manifest,
                **_freshness(heads.values(), now),
                "created_at": now,
                "expires_at": snapshot_expires_at(now),
            }
            c.execute(self.snapshots.insert().values(**row))
            c.execute(
                self.refs.insert(),
                [
                    {
                        "snapshot_id": row["snapshot_id"],
                        "ordinal": i,
                        "generation_id": scope["generation_id"],
                    }
                    for i, scope in enumerate(scopes)
                ],
            )
            return row

    def _same_selection(self):
        """이 reader와 같은 forecasts selection(style·since·provider 서명)인 snapshot 행 조건."""
        return (
            self.snapshots.c.selection_kind == "forecasts",
            self.snapshots.c.forecast_style == self.style,
            self.snapshots.c.forecast_since == self.selection["since"],
            self.snapshots.c.forecast_provider_signature == self.selection.get(
                "provider_signature",
            ),
        )

    def _previous_forecasts(self):
        with self.heads._transaction() as s:
            row = (
                s.execute(
                    select(self.snapshots)
                    .where(*self._same_selection())
                    .order_by(self.snapshots.c.issue_order.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ExportSnapshotUnavailable("이전 승인 예보가 없습니다.")
            frozen, now = dict(row), datetime.now(UTC)
            if frozen["expires_at"] > now:
                frozen["_reused"] = True
            else:
                frozen.pop("issue_order", None)
                for column in self.snapshots.c:
                    if column.computed is not None:
                        frozen.pop(column.name, None)
                frozen.update(
                    snapshot_id=str(uuid4()), created_at=now, expires_at=snapshot_expires_at(now)
                )
                s.execute(self.snapshots.insert().values(**frozen))
                s.execute(
                    self.refs.insert(),
                    [
                        {
                            "snapshot_id": frozen["snapshot_id"],
                            "ordinal": i,
                            "generation_id": scope["generation_id"],
                        }
                        for i, scope in enumerate(frozen["manifest"]["scopes"])
                    ],
                )
            return frozen

    def page(self, *, limit, cursor):
        # 공유 guard는 artifact 파일 하나 읽는 동안만 잡는다(read_shared). 읽을 파일은
        # commit된 snapshot ref·head·보존 창이 GC에서 지킨다(freeze·health는 DB만 본다).
        return self._page_guarded(limit=limit, cursor=cursor)

    def _page_guarded(self, *, limit, cursor):
        fresh_health = None
        if cursor:
            state = self._decode(cursor)
            if state["r"] != self.resource or state["l"] != limit:
                raise ExportCursorInvalid("예보 style/since/limit은 cursor 도중 바꿀 수 없습니다.")
            row = self._read_cursor_snapshot(state["s"])
            if row is None or row["expires_at"] <= datetime.now(UTC):
                raise ExportSnapshotExpired("예보 cursor가 만료됐습니다.")
            frozen = self._renew_reused(dict(row, _reused=True))
        else:
            def issue():
                self._fresh_forecast_health = None
                capture = getattr(self, "_capture_forecast_health", False)
                self._capture_forecast_health = True
                try:
                    return self._freeze_forecasts()
                except ExportCapacityExceeded:
                    self._fresh_forecast_health = None
                    return self._capacity_fallback(self._previous_forecasts())
                except ExportSnapshotUnavailable:
                    self._fresh_forecast_health = None
                    return self._previous_forecasts()
                finally:
                    self._capture_forecast_health = capture

            frozen = ensure_snapshot_files(self.artifacts, issue)
            fresh_health = self._fresh_forecast_health
            frozen = self._renew_reused(frozen)
            state = {
                "v": 1,
                "s": frozen["snapshot_id"],
                "r": self.resource,
                "l": limit,
                "g": 0,
                "p": 0,
            }
        manifest, now = frozen["manifest"], datetime.now(UTC)
        read_started = monotonic()
        if manifest["selection"] != self.selection:
            raise ExportCursorInvalid("cursor의 예보 selection이 다릅니다.")
        self.reuse_snapshot = (frozen["snapshot_id"], frozen["manifest_digest"])
        self.reuse_generations.update(scope["generation_id"] for scope in manifest["scopes"])
        self.reuse_namespaces.update(scope["namespace"] for scope in manifest["scopes"])
        self._expired_omitted = False
        output, more = self._page_items(manifest, state, limit, now)
        targets = [datetime.fromisoformat(target)
                   for item in output for target in item["target_at"]]
        # wall/monotonic을 payload 읽기 전에 묶어 느린 생성이 실제 경계를 뒤로 밀지 않게 한다.
        boundary = min([frozen["expires_at"], *targets])
        self.page_valid_until = read_started + max(0, (boundary - now).total_seconds())
        stale = (
            self._expired_omitted
            or bool(manifest.get("capacity_limited"))
            or self._health_after_freeze(manifest, frozen, now, fresh_health)
        )
        status = "stale" if stale else "ok"
        self.cached_source_status = status
        page = {
            "items": output,
            "meta": {
                "generated_at": now.isoformat(),
                "source_collected_at": frozen["source_collected_at"].isoformat(),
                "source_status": status,
                "item_count": len(output),
                "snapshot_id": frozen["snapshot_id"],
                "snapshot_generated_at": frozen["created_at"].isoformat(),
                "as_of": now.isoformat(),
                "next_cursor": self._encode(state) if more else None,
                "has_more": more,
                "next_since": manifest["next_since"],
            },
        }
        etag = 'W/"' + _digest([frozen["snapshot_id"], cursor, limit, status, output]) + '"'
        return page, etag

    def validate_cached_first_page(self, cached):
        """첫 조건부 페이지도 새 승인/실패를 즉시 확인하되 immutable payload는 재독하지 않는다."""
        from .export_page_cache import PageReuseExpired

        fresh = copy(self)
        fresh.reuse_generations, fresh.reuse_namespaces, fresh.reuse_artifacts = set(), set(), set()
        fresh.selection_deadline = fresh.reuse_snapshot = fresh.reuse_liveness = None
        fresh.begin_page_reuse()
        fresh._capture_forecast_health = True
        def issue():
            fresh._fresh_forecast_health = None
            try:
                return fresh._freeze_forecasts()
            except ExportCapacityExceeded:
                fresh._fresh_forecast_health = None
                return fresh._capacity_fallback(fresh._previous_forecasts())
            except ExportSnapshotUnavailable:
                fresh._fresh_forecast_health = None
                return fresh._previous_forecasts()

        with read_stage("metadata"):
            frozen = ensure_snapshot_files(fresh.artifacts, issue)
            if (frozen["snapshot_id"], frozen["manifest_digest"]) != cached.reuse_snapshot:
                raise PageReuseExpired()
            now = datetime.now(UTC)
            stale = fresh._health_after_freeze(
                frozen["manifest"], frozen, now, fresh._fresh_forecast_health,
            )
        status = "stale" if (stale or cached._expired_omitted
                             or frozen["manifest"].get("capacity_limited")) else "ok"
        if status != cached.cached_source_status:
            raise PageReuseExpired()
        # 새 source/health 판정 뒤에도 기존 본문의 snapshot·삭제 epoch·실제 ref를 모두 검산한다.
        # cached 자체를 갱신하지 않아 실패/경합/만료에서 이전 proof 수명을 연장하지 않는다.
        proof = copy(cached)
        proof.selection_checked_after = fresh.selection_checked_after
        proof.reuse_wall_started = fresh.reuse_wall_started
        proof.selection_deadline = fresh.selection_deadline
        proof.validate_reusable_page()
        return proof

    def _health_after_freeze(self, manifest, frozen, now, health):
        # 같은 요청의 RR freeze에서 모든 위치·head/owner/run/cohort를 이미 확인했다.
        # 본문 생성 뒤 실제 clock이 경계를 넘거나 fallback이면 기존 전체 health로 돌아간다.
        checked_now = datetime.now(UTC)
        if (health is not None and health[0] is not None and checked_now >= health[0]
                and (health[1] is None or checked_now < health[1])):
            return health[2]
        return self._health(manifest, frozen, now)

    def _prepare_manifest(self, manifest):
        """특수 source 조합도 완결을 검증한 뒤 불변 manifest로 보존한다."""
        return manifest

    def _page_items(self, manifest, state, limit, now):
        """일반 source의 기존 bounded 페이지 출력을 그대로 유지한다."""
        output, size, more = [], 0, False
        scopes = manifest["scopes"]
        if state["g"] > len(scopes):
            raise ExportCursorInvalid("cursor 위치가 올바르지 않습니다.")
        scope_reads, artifact_reads = 0, 0
        # forecast는 evidence 등 미출력 필드의 mutable 복사가 필요 없다.
        # DB reader는 projection 전에 chunk 전체의 무결성과 JSON 형식을 검증한다.
        artifact_window = ArtifactReadWindow(self.artifacts, fields=(
            "provider", "forecast_style", "location_id", "target_at",
            "metric_key", "value_number", "value_text", "known_at",
        ), max_bytes=MAX_PAGE_INPUT_BYTES)
        while state["g"] < len(scopes):
            if scope_reads >= MAX_PAGE_SCOPES or len(output) >= limit:
                return output, True
            scope_reads += 1
            scope = scopes[state["g"]]
            if self.since and datetime.fromisoformat(scope["published_at"]) <= self.since:
                state["g"], state["p"] = state["g"] + 1, 0
                continue
            keys = scope["location_ids"]
            if state["p"] > len(keys):
                raise ExportCursorInvalid("cursor 위치가 올바르지 않습니다.")
            cached_ref, cached = None, None
            while state["p"] < len(keys):
                key = keys[state["p"]]
                refs = [r for r in scope["refs"] if key in r["location_ids"]]
                if len(refs) != 1:
                    raise ExportSnapshotUnavailable(
                        "위치가 단일 bounded chunk에 있지 않습니다."
                    )
                ref = refs[0]
                if len(output) >= limit:
                    return output, True
                if cached_ref != ref["artifact_digest"]:
                    if (artifact_reads >= MAX_PAGE_ARTIFACTS
                            or not artifact_window.can_read(ref)):
                        return output, True
                    # 같은 scope 안에서도 현재 ref부터 소진된 window를 보충한다.
                    artifact_window.prepare(
                        ref,
                        lambda scope_reads=scope_reads: forecast_candidates(
                            scopes, state["g"], state["p"], locations=limit - len(output),
                            max_scopes=MAX_PAGE_SCOPES - scope_reads + 1, since=self.since,
                        ), remaining_reads=MAX_PAGE_ARTIFACTS - artifact_reads,
                    )
                    artifact_reads += 1
                    self.reuse_artifacts.add((
                        ref["artifact_digest"], ref["byte_count"], ref["point_count"],
                    ))
                    cached = defaultdict(list)
                    for point in artifact_window.read(ref):
                        if (
                            point["provider"] != scope.get("provider", "python-kma-api")
                            or point["forecast_style"]
                            != scope.get("forecast_style", self.style)
                            or point["location_id"] not in ref["location_ids"]
                        ):
                            raise ExportSnapshotUnavailable(
                                "예보 artifact/index가 일치하지 않습니다."
                            )
                        cached[point["location_id"]].append(point)
                    cached_ref = ref["artifact_digest"]
                points = cached[key]
                future = [p for p in points if datetime.fromisoformat(p["target_at"]) > now]
                if not future:
                    # 이 위치의 최신 승인 예보가 모두 지났다. 페이지를 실패시키면 재시도가
                    # 매번 같은 위치에서 멈추므로 이 위치만 빼고 응답을 stale로 표시한다.
                    self._expired_omitted = True
                    state["p"] += 1
                    continue
                targets = sorted({p["target_at"] for p in future})
                # metric마다 같은 point 목록을 반복 순회하지 않는다. 같은 target/metric의
                # 마지막 값 우선과 누락 target의 null, metric 정렬은 기존 출력과 같다.
                metrics = defaultdict(dict)
                for point in future:
                    metrics[point["metric_key"]][point["target_at"]] = (
                        float(point["value_number"])
                        if point["value_number"] is not None
                        else point["value_text"]
                    )
                item = {
                    "location_id": key,
                    "provider": scope.get("provider", "python-kma-api"),
                    "forecast_style": self.style,
                    "known_at": max(p["known_at"] for p in points),
                    "published_at": scope["published_at"],
                    "target_at": targets,
                    "metrics": {
                        metric: [values.get(t) for t in targets]
                        for metric, values in sorted(metrics.items())
                    },
                }
                item_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
                if item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                    raise ExportSnapshotUnavailable(
                        "한 위치의 예보가 페이지 상한을 넘었습니다."
                    )
                if (
                    len(output) >= limit
                    or size + item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE
                ):
                    more = True
                    break
                output.append(item)
                size += item_size
                state["p"] += 1
            if more:
                break
            state["g"], state["p"] = state["g"] + 1, 0
        return output, more

    def _health(self, manifest, frozen, now):
        if self.source_inventory:
            return self._inventory_forecast_health(manifest, frozen, now)
        scopes = manifest["scopes"]
        stale = now - frozen["source_collected_at"] > timedelta(
            hours=3 if self.style == "ultra_short" else 9
        )
        stale |= now - frozen["data_revision_at"] > timedelta(
            hours=3 if self.style == "ultra_short" else 9
        )
        with self.heads._transaction() as s:
            catalog = self.heads.metadata.tables["weather_locations"]
            actual = sorted(
                s.execute(
                    select(
                        catalog.c.location_id,
                        catalog.c.nx,
                        catalog.c.ny,
                        catalog.c.latitude,
                        catalog.c.longitude,
                    )
                    .where(catalog.c.enabled.is_(True))
                    .limit(MAX_SNAPSHOT_LOCATIONS + 1)
                ).all()
            )
            expected = sorted(
                (k, scope["nx"], scope["ny"]) for scope in scopes for k in scope["location_ids"]
            )
            if self.style == "external":
                from .export_artifacts import ExportArtifactError
                from .export_external_cohorts import selected_external_descriptors

                try:
                    descriptors = selected_external_descriptors(
                        s,
                        self.heads.metadata,
                        [r._mapping for r in actual],
                        self.current_providers,
                        forecast=True,
                    )
                    stale |= {d.namespace for d in descriptors} != {
                        scope["namespace"] for scope in scopes
                    }
                except (ExportArtifactError, KeyError, ValueError, TypeError):
                    stale = True
            else:
                stale |= [tuple(r)[:3] for r in actual] != expected
            current = set(
                s.scalars(
                    select(self.heads.heads.c.namespace).where(
                        self.heads.heads.c.namespace.in_([scope["namespace"] for scope in scopes])
                    )
                )
            )
            stale |= current != {scope["namespace"] for scope in scopes}
            owners, attempts = self.heads.lifecycle.owners, self.heads.lifecycle.attempts
            runs = self.heads.metadata.tables["weather_sync_runs"]
            statuses = s.execute(
                select(attempts.c.status, runs.c.status, self.heads.generations.c.generation_id)
                .select_from(
                    owners.join(attempts, owners.c.active_attempt_id == attempts.c.attempt_id)
                    .join(runs, runs.c.run_id == attempts.c.run_id)
                    .outerjoin(
                        self.heads.generations,
                        self.heads.generations.c.attempt_id == attempts.c.attempt_id,
                    )
                )
                .where(owners.c.source_scope.in_([scope["source_scope"] for scope in scopes]))
            ).all()
        return (
            stale
            or len(statuses) != len(scopes)
            or any(
                a == "failed"
                or run not in {"running", "success"}
                or (run == "success" and generation is None)
                for a, run, generation in statuses
            )
        )

    def _inventory_forecast_health(self, manifest, frozen, now):
        from .export_artifacts import ExportArtifactError, ExportGuardTimeout
        from .export_inventory_selection import counts_for_freshness, source_max_age

        stale = bool(manifest.get("unconfirmed_sources"))
        stale |= any(
            now - datetime.fromisoformat(scope[key]) > source_max_age(scope["dataset_key"])
            for scope in manifest["scopes"]
            if counts_for_freshness(datetime.fromisoformat(scope["source_collected_at"]), now)
            for key in ("source_collected_at", "data_revision_at")
        ) or not any(
            counts_for_freshness(datetime.fromisoformat(scope["source_collected_at"]), now)
            for scope in manifest["scopes"]
        )
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as c,
            c.begin(),
        ):
            c.execute(text("SET LOCAL statement_timeout = '5s'"))
            catalog = self.heads.metadata.tables["weather_locations"]
            source_from, source_columns = self._catalog_source(catalog)
            locations = c.execute(select(
                catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
                catalog.c.nx, catalog.c.ny, *source_columns,
            ).select_from(source_from).where(
                catalog.c.enabled.is_(True),
            ).limit(MAX_SNAPSHOT_LOCATIONS + 1)).mappings().all()
            if len(locations) > MAX_SNAPSHOT_LOCATIONS:
                return True
            try:
                # cursor 페이지는 앱 공용 승인 선택(최대 60초, #215)을 쓴다.
                # 예전에는 페이지마다 전체 선택을 다시 계산했다
                # (odroid 2026-11-10 6~16초/페이지).
                selected = self._approved_selection(c, locations)
                stale |= selected.unconfirmed
                actual = {(scope.descriptor.namespace, scope.location_ids)
                          for scope in selected.scopes}
                wanted = {(scope["namespace"], tuple(scope["location_ids"]))
                          for scope in manifest["scopes"]}
                stale |= actual != wanted
            except ExportGuardTimeout:
                # 공용 캐시의 대기·수명 초과는 current와 같이 503이다
                # (stale로 숨기지 않는다).
                raise
            except (ExportArtifactError, KeyError, ValueError, TypeError):
                stale = True
        return stale
