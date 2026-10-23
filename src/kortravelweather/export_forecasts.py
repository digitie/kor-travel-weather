"""승인된 예보를 위치별 배열로 읽는다. 날짜 이력/current 표를 스캔하지 않는다."""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, text

from .export_heads import ScopeDescriptor, _digest
from .export_snapshots import (
    MAX_PAGE_BYTES,
    MAX_SNAPSHOT_LOCATIONS,
    PAGE_META_RESERVE,
    SNAPSHOT_TTL,
    ExportCursorInvalid,
    ExportSnapshotExpired,
    ExportSnapshotUnavailable,
    FrozenCurrentExports,
)


class FrozenForecastExports(FrozenCurrentExports):
    def __init__(self, *args, style, since=None, **kwargs):
        super().__init__(*args, **kwargs)
        if style not in {"ultra_short", "short", "external"}:
            raise ExportSnapshotUnavailable("해당 예보 style의 전체 원천이 준비되지 않았습니다.")
        if since is not None:
            since = since.astimezone(UTC)
        self.style, self.since = style, since
        self.dataset = (
            "kma_ultra_short_forecast" if style == "ultra_short" else "kma_short_forecast"
        )
        self.selection = {
            "kind": "forecasts",
            "style": style,
            "since": since.isoformat() if since else None,
        }
        if style == "external":
            from .export_external import ADAPTERS

            if not self.current_providers or not set(self.current_providers).issubset(ADAPTERS):
                raise ExportSnapshotUnavailable("요청 외부 provider 전체가 준비되지 않았습니다.")
            self.selection["providers"] = list(self.current_providers)
            self.selection["provider_signature"] = _digest(self.current_providers)
        self.resource = f"forecasts:{style}:{_digest(self.selection)}"

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
            roster = c.execute(
                select(
                    catalog.c.location_id,
                    catalog.c.nx,
                    catalog.c.ny,
                    catalog.c.latitude,
                    catalog.c.longitude,
                )
                .where(catalog.c.enabled.is_(True))
                .order_by(catalog.c.location_id)
                .limit(MAX_SNAPSHOT_LOCATIONS + 1)
            ).all()
            if not roster or len(roster) > MAX_SNAPSHOT_LOCATIONS:
                raise ExportSnapshotUnavailable("활성 위치 전체가 준비되지 않았습니다.")
            groups = defaultdict(list)
            for key, nx, ny, _latitude, _longitude in roster:
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
            if self.style == "external":
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
            if len(descriptors) > 5000:
                raise ExportSnapshotUnavailable("예보 source vector 상한을 넘었습니다.")
            heads = {
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
            if len(heads) != len(descriptors):
                raise ExportSnapshotUnavailable("모든 위치의 승인된 예보가 필요합니다.")
            refs = defaultdict(list)
            for ref in c.execute(
                select(self.heads.refs)
                .where(
                    self.heads.refs.c.generation_id.in_([r.generation_id for r in heads.values()])
                )
                .order_by(self.heads.refs.c.ordinal)
            ).mappings():
                refs[ref["generation_id"]].append(dict(ref))
            scopes = []
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
                        "location_ids": list(d.location_ids),
                        "refs": chunks,
                        "published_at": head.created_at.isoformat(),
                    }
                )
            manifest = {
                "selection": self.selection,
                "as_of": anchor.isoformat(),
                "scopes": scopes,
                "next_since": watermark.isoformat(),
            }
            digest = _digest(manifest)
            latest = select(self.snapshots.c.issue_order).where(
                self.snapshots.c.manifest["selection"]["kind"].as_string() == "forecasts",
                self.snapshots.c.manifest["selection"]["style"].as_string() == self.style,
                self.snapshots.c.manifest["selection"]["since"].as_string()
                == self.selection["since"],
                self.snapshots.c.manifest["selection"]["provider_signature"].as_string()
                == self.selection.get("provider_signature"),
            ).order_by(self.snapshots.c.issue_order.desc()).limit(1).correlate(None).scalar_subquery()
            previous = (
                c.execute(
                    select(self.snapshots)
                    .where(
                        self.snapshots.c.manifest_digest == digest,
                        self.snapshots.c.expires_at > now,
                        self.snapshots.c.issue_order == latest,
                    )
                    .order_by(self.snapshots.c.issue_order.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None:
                return dict(previous)
            row = {
                "snapshot_id": str(uuid4()),
                "manifest_digest": digest,
                "manifest": manifest,
                "source_collected_at": min(h.source_collected_at for h in heads.values()),
                "data_revision_at": min(h.data_revision_at for h in heads.values()),
                "created_at": now,
                "expires_at": now + SNAPSHOT_TTL,
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

    def _previous_forecasts(self):
        with self.heads._transaction() as s:
            row = (
                s.execute(
                    select(self.snapshots)
                    .where(
                        self.snapshots.c.manifest["selection"]["kind"].as_string() == "forecasts",
                        self.snapshots.c.manifest["selection"]["style"].as_string() == self.style,
                        self.snapshots.c.manifest["selection"]["since"].as_string()
                        == self.selection["since"],
                        self.snapshots.c.manifest["selection"]["provider_signature"].as_string()
                        == self.selection.get("provider_signature"),
                    )
                    .order_by(self.snapshots.c.issue_order.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ExportSnapshotUnavailable("이전 승인 예보가 없습니다.")
            frozen, now = dict(row), datetime.now(UTC)
            if frozen["expires_at"] <= now:
                frozen.pop("issue_order", None)
                frozen.update(
                    snapshot_id=str(uuid4()), created_at=now, expires_at=now + SNAPSHOT_TTL
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
        if cursor:
            state = self._decode(cursor)
            if state["r"] != self.resource or state["l"] != limit:
                raise ExportCursorInvalid("예보 style/since/limit은 cursor 도중 바꿀 수 없습니다.")
            with self.heads._transaction() as s:
                row = (
                    s.execute(
                        select(self.snapshots).where(self.snapshots.c.snapshot_id == state["s"])
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None or row["expires_at"] <= datetime.now(UTC):
                raise ExportSnapshotExpired("예보 cursor가 만료됐습니다.")
            frozen = dict(row)
        else:
            try:
                frozen = self._freeze_forecasts()
            except ExportSnapshotUnavailable:
                frozen = self._previous_forecasts()
            state = {
                "v": 1,
                "s": frozen["snapshot_id"],
                "r": self.resource,
                "l": limit,
                "g": 0,
                "p": 0,
            }
        manifest, now = frozen["manifest"], datetime.now(UTC)
        if manifest["selection"] != self.selection:
            raise ExportCursorInvalid("cursor의 예보 selection이 다릅니다.")
        output, size, more = [], 0, False
        scopes = manifest["scopes"]
        if state["g"] > len(scopes):
            raise ExportCursorInvalid("cursor 위치가 올바르지 않습니다.")
        with self.artifacts.guard(timeout_seconds=2):
            while state["g"] < len(scopes):
                scope = scopes[state["g"]]
                if self.since and datetime.fromisoformat(scope["published_at"]) <= self.since:
                    state["g"], state["p"] = state["g"] + 1, 0
                    continue
                keys = scope["location_ids"]
                if state["p"] > len(keys):
                    raise ExportCursorInvalid("cursor 위치가 올바르지 않습니다.")
                # 하나의 chunk를 최대 한 번 읽고 그 안의 소수 위치만 묶는다.
                # chunk당 <=5,000 point이며 전체 격자 값을 유지하지 않는다.
                cached_ref, cached = None, None
                while state["p"] < len(keys):
                    key = keys[state["p"]]
                    refs = [r for r in scope["refs"] if key in r["location_ids"]]
                    if len(refs) != 1:
                        raise ExportSnapshotUnavailable(
                            "위치가 단일 bounded chunk에 있지 않습니다."
                        )
                    ref = refs[0]
                    if cached_ref != ref["artifact_digest"]:
                        cached = defaultdict(list)
                        for point in self.artifacts.read(
                            ref["artifact_digest"],
                            byte_count=ref["byte_count"],
                            point_count=ref["point_count"],
                        ):
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
                        raise ExportSnapshotUnavailable("현재 이후의 승인 예보가 없습니다.")
                    targets = sorted({p["target_at"] for p in future})
                    metrics = {
                        metric: {
                            p["target_at"]: (
                                float(p["value_number"])
                                if p["value_number"] is not None
                                else p["value_text"]
                            )
                            for p in future
                            if p["metric_key"] == metric
                        }
                        for metric in sorted({p["metric_key"] for p in future})
                    }
                    item = {
                        "location_id": key,
                        "provider": scope.get("provider", "python-kma-api"),
                        "forecast_style": self.style,
                        "known_at": max(p["known_at"] for p in points),
                        "published_at": scope["published_at"],
                        "target_at": targets,
                        "metrics": {
                            metric: [values.get(t) for t in targets]
                            for metric, values in metrics.items()
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
        stale = self._health(manifest, frozen, now)
        status = "stale" if stale else "ok"
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

    def _health(self, manifest, frozen, now):
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
