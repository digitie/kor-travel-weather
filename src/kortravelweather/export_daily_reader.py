"""사전 계산 summary만 조회한다. fact와 비공개 winner JSON은 읽지 않는다."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text, tuple_

from .export_daily import KST, SLOTS, summarize
from .export_daily_store import daily_coverage_table, daily_summary_tables
from .export_heads import _digest
from .export_inventory_selection import (
    MAX_SELECTED_ROWS,
    approved_source_selection,
    catalog_source_columns,
    source_max_age,
)
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


class DailySummaryExports(FrozenCurrentExports):
    def __init__(self, *args, days=14, location_id=None, providers=("python-kma-api",), **kwargs):
        super().__init__(*args, providers=providers, **kwargs)
        if not 1 <= days <= 31:
            raise ExportCursorInvalid("요약 기간은 1~31일입니다.")
        self.table, self.progress = daily_summary_tables(self.heads.metadata)
        self.coverage = daily_coverage_table(self.heads.metadata)
        self.providers = tuple(sorted(set(providers)))
        self.days, self.location_id = days, location_id
        self.selection = {
            "kind": "daily-summaries",
            "days": days,
            "location_id": location_id,
            "providers": list(self.providers),
        }
        if self.source_inventory:
            self.selection.update({
                "source_inventory": 1,
                "expected_sources": [list(source) for source in sorted(self.expected_sources)],
                "optional_sources": [list(source) for source in sorted(self.optional_sources)],
            })
        self.resource = "daily-summaries:" + _digest(self.selection)

    def _selected_coverage(self, connection, keys):
        catalog = self.heads.metadata.tables["weather_locations"]
        coverage = self.coverage
        rows = list(
            connection.execute(
                select(coverage)
                .join(catalog, catalog.c.location_id == coverage.c.location_id)
                .where(
                    coverage.c.location_id.in_(keys),
                    coverage.c.provider.in_(self.providers),
                    catalog.c.enabled.is_(True),
                    or_(
                        and_(
                            coverage.c.provider == "python-kma-api",
                            coverage.c.nx == catalog.c.nx,
                            coverage.c.ny == catalog.c.ny,
                        ),
                        and_(
                            coverage.c.provider != "python-kma-api",
                            coverage.c.latitude == catalog.c.latitude,
                            coverage.c.longitude == catalog.c.longitude,
                        ),
                    ),
                )
                .order_by(coverage.c.location_id, coverage.c.namespace)
                .limit(MAX_SELECTED_ROWS + 1)
            ).mappings()
        )
        if len(rows) > MAX_SELECTED_ROWS:
            raise ExportSnapshotUnavailable("관측 coverage metadata 상한을 넘었습니다.")
        if self.source_inventory:
            rows, unconfirmed = self._inventory_coverage(connection, keys, rows)
        else:
            unconfirmed = False
        if not self.source_inventory and {r["location_id"] for r in rows} != set(keys):
            raise ExportSnapshotUnavailable("선택 위치의 승인 관측 coverage가 준비되지 않았습니다.")
        owners, attempts = self.heads.lifecycle.owners, self.heads.lifecycle.attempts
        runs = self.heads.metadata.tables["weather_sync_runs"]
        scopes = {r["source_scope"] for r in rows}
        statuses = list(
            connection.execute(
                select(attempts.c.status, runs.c.status, self.heads.generations.c.generation_id)
                .select_from(
                    owners.join(attempts, owners.c.active_attempt_id == attempts.c.attempt_id)
                    .join(runs, runs.c.run_id == attempts.c.run_id)
                    .outerjoin(
                        self.heads.generations,
                        self.heads.generations.c.attempt_id == attempts.c.attempt_id,
                    )
                )
                .where(owners.c.source_scope.in_(scopes))
            )
        )
        self.daily_health_digest = _digest(sorted([list(row) for row in statuses], key=str))
        unconfirmed |= len(statuses) != len(scopes) or any(
            attempt == "failed"
            or run not in {"running", "success"}
            or (run == "success" and generation is None)
            for attempt, run, generation in statuses
        )
        return rows, unconfirmed

    def _inventory_coverage(self, connection, keys, rows):
        """실제 승인 관측별 reducer 진도를 확인하고 예보 전용 위치와 구분한다."""
        catalog = self.heads.metadata.tables["weather_locations"]
        locations = connection.execute(select(
            catalog.c.location_id, catalog.c.latitude, catalog.c.longitude,
            catalog.c.nx, catalog.c.ny, *catalog_source_columns(catalog),
        ).where(catalog.c.enabled.is_(True)).order_by(catalog.c.location_id).limit(
            MAX_SNAPSHOT_LOCATIONS + 1,
        )).mappings().all()
        if len(locations) > MAX_SNAPSHOT_LOCATIONS:
            raise ExportSnapshotUnavailable("활성 catalog 상한을 넘었습니다.")
        from .export_artifacts import ExportArtifactError

        try:
            selection = approved_source_selection(
                connection, self.heads.metadata, locations,
                expected=self.expected_sources, optional=self.optional_sources,
                health_location_ids=keys if self.location_id else None,
            )
        except (ExportArtifactError, KeyError, TypeError, ValueError) as exc:
            raise ExportSnapshotUnavailable("관측 source 승인 집합이 준비되지 않았습니다.") from exc
        expected = {
            (scope.descriptor.namespace, key): scope
            for scope in selection.scopes for key in scope.location_ids if key in keys
        }
        coverage = {(r["namespace"], r["location_id"]): r for r in rows}
        from .export_external_cohorts import external_cohort_table

        cohorts = external_cohort_table(self.heads.metadata)
        target = func.json_array_elements(cohorts.c.targets).column_valued(
            "daily_target", joins_implicitly=True,
        )
        target_key = target.op("->>")("location_id")
        declared = set(connection.scalars(select(target_key).select_from(cohorts).where(
            tuple_(cohorts.c.provider, cohorts.c.dataset_key).in_(self.expected_sources),
            target_key.in_(keys),
        ).distinct().limit(MAX_SNAPSHOT_LOCATIONS + 1)))
        if declared - {row["location_id"] for row in rows}:
            raise ExportSnapshotUnavailable("사전 계획된 관측 위치의 최초 요약이 없습니다.")
        if expected.keys() - coverage.keys():
            # 승인 관측이 있으나 요약이 한번도 계산되지 않은 경우를 무관측0으로 꾸미지 않는다.
            raise ExportSnapshotUnavailable("승인 관측의 사전 계산이 아직 준비되지 않았습니다.")
        selected = [coverage[key] for key in sorted(expected)]
        now = datetime.now(UTC)
        unconfirmed = selection.unconfirmed or any(
            coverage[key]["generation_id"] != scope.generation_id
            or now - coverage[key]["source_collected_at"]
            > source_max_age(scope.descriptor.dataset_key)
            for key, scope in expected.items()
        )
        # 관측 dataset 대상이 아닌 위치는 null/sample_count=0을 제공할 수 있다.
        # 원천 clock을 다른 위치에서 빌리지 않고 메타는 unknown/stale로 둔다.
        return selected, unconfirmed or not expected

    def _freeze_daily(self):
        now = datetime.now(UTC)
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as c,
            c.begin(),
        ):
            c.execute(text("SET LOCAL statement_timeout = '5s'"))
            ready = c.execute(select(self.progress)).mappings().one_or_none()
            if ready is None or ready["source_collected_at"] is None:
                raise ExportSnapshotUnavailable("승인된 관측 요약이 아직 없습니다.")
            catalog = self.heads.metadata.tables["weather_locations"]
            query = select(catalog.c.location_id).where(catalog.c.enabled.is_(True))
            if self.location_id:
                query = query.where(catalog.c.location_id == self.location_id)
            keys = list(
                c.scalars(query.order_by(catalog.c.location_id).limit(MAX_SNAPSHOT_LOCATIONS + 1))
            )
            if not keys or len(keys) > MAX_SNAPSHOT_LOCATIONS:
                raise ExportSnapshotUnavailable("선택 위치 집합이 준비되지 않았습니다.")
            coverage, _ = self._selected_coverage(c, keys)
            today = now.astimezone(KST).date()
            manifest = {
                "selection": self.selection,
                "locations": keys,
                "first_date": (today - timedelta(days=self.days - 1)).isoformat(),
                "last_date": today.isoformat(),
                "coverage": _digest(
                    [[r["location_id"], r["namespace"], r["generation_id"]] for r in coverage]
                ),
            }
            digest = _digest(manifest)
            existing = (
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
            if existing is not None:
                return dict(existing)
            row = {
                "snapshot_id": str(uuid4()),
                "manifest_digest": digest,
                "manifest": manifest,
                "source_collected_at": min(
                    (r["source_collected_at"] for r in coverage), default=None,
                ),
                "data_revision_at": min(
                    (r["published_at"] for r in coverage), default=ready["published_at"],
                ),
                "created_at": now,
                "expires_at": now + SNAPSHOT_TTL,
            }
            c.execute(self.snapshots.insert().values(**row))
            return row

    def page(self, *, limit, cursor):
        if cursor:
            state = self._decode(cursor)
            if state["r"] != self.resource or state["l"] != limit:
                raise ExportCursorInvalid(
                    "days/location_id/limit은 cursor 도중 변경할 수 없습니다."
                )
            with self.heads._transaction() as s:
                row = (
                    s.execute(
                        select(self.snapshots).where(self.snapshots.c.snapshot_id == state["s"])
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None or row["expires_at"] <= datetime.now(UTC):
                raise ExportSnapshotExpired("요약 cursor가 만료됐습니다.")
            frozen = dict(row)
        else:
            frozen = self._freeze_daily()
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
            raise ExportCursorInvalid("요약 cursor selection이 다릅니다.")
        first, last = (
            datetime.fromisoformat(manifest["first_date"]).date(),
            datetime.fromisoformat(manifest["last_date"]).date(),
        )
        positions = [(first + timedelta(days=i), slot) for i in range(self.days) for slot in SLOTS]
        keys, output, size = manifest["locations"], [], 0
        if state["g"] > len(keys) or state["p"] > len(positions):
            raise ExportCursorInvalid("요약 cursor 위치가 올바르지 않습니다.")
        # scalar summary만 선택한다. 한 위치의 최대 31일×3구간만 메모리에 유지한다.
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as s,
            s.begin(),
        ):
            s.execute(text("SET LOCAL statement_timeout = '5s'"))
            while state["g"] < len(keys):
                key = keys[state["g"]]
                rows = {
                    (r.date, r.slot): r.summary
                    for r in s.execute(
                        select(
                            self.table.c.date,
                            self.table.c.slot,
                            self.table.c.summary,
                        ).where(
                            self.table.c.location_id == key,
                            self.table.c.date >= first,
                            self.table.c.date <= last,
                        )
                    )
                }
                while state["p"] < len(positions):
                    day, slot = positions[state["p"]]
                    item = dict(
                        rows.get((day, slot)) or summarize(key, day.isoformat(), slot, {}, now=now)
                    )
                    end = datetime.combine(day, datetime.min.time(), tzinfo=KST).replace(
                        hour=SLOTS[slot][1]
                    )
                    item["partial"] = now < end.astimezone(UTC)
                    item_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
                    if item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                        raise ExportSnapshotUnavailable(
                            "단일 요약이 페이지 byte 상한을 넘었습니다."
                        )
                    if (
                        len(output) >= limit
                        or size + item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE
                    ):
                        break
                    output.append(item)
                    size += item_size
                    state["p"] += 1
                if state["p"] < len(positions):
                    break
                state["g"], state["p"] = state["g"] + 1, 0
            ready = s.execute(select(self.progress)).mappings().one_or_none()
            coverage, unconfirmed = self._selected_coverage(s, keys)
        more = state["g"] < len(keys)
        if ready is None or ready["source_collected_at"] is None:
            raise ExportSnapshotUnavailable("사전 계산 요약 checkpoint가 없습니다.")
        # roster/날짜 범위를 고정하고 각 페이지의 최신 사전계산 값을 반환한다.
        source = min((r["source_collected_at"] for r in coverage), default=None)
        status = "stale" if (
            unconfirmed or source is None
            or not self.source_inventory and now - source > timedelta(hours=3)
        ) else "ok"
        page = {
            "items": output,
            "meta": {
                "generated_at": now.isoformat(),
                "source_collected_at": source.isoformat() if source is not None else None,
                "source_status": status,
                "item_count": len(output),
                "snapshot_id": frozen["snapshot_id"],
                "snapshot_generated_at": frozen["created_at"].isoformat(),
                "as_of": now.isoformat(),
                "next_cursor": self._encode(state) if more else None,
                "has_more": more,
            },
        }
        return page, 'W/"' + _digest([
            frozen["snapshot_id"], limit, cursor, status, output, self.daily_health_digest,
        ]) + '"'
