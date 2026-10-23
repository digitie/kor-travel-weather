"""승인된 KMA 실황 generation vector를 고정하는 bounded REST reader.

mutable current/fact/원천 API를 다시 읽지 않는다. provider 미지원은 빈 성공 대신 실패한다.
"""

from __future__ import annotations

import base64
import hmac
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import AddressTarget
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, select

from .export_artifacts import ExportArtifactError, ExportArtifactStore
from .export_heads import ExportHeads, ScopeDescriptor, _digest

MAX_SNAPSHOT_LOCATIONS = 5000
MAX_PAGE_BYTES = 4 * 1024 * 1024
PAGE_META_RESERVE = 8192
SNAPSHOT_TTL = timedelta(minutes=15)
SOURCE_MAX_AGE = timedelta(hours=3)


class ExportSnapshotUnavailable(ExportArtifactError):
    pass


class ExportCursorInvalid(ExportArtifactError):
    pass


class ExportSnapshotExpired(ExportArtifactError):
    pass


def export_snapshot_tables(metadata: MetaData):
    if "weather_export_snapshots" in metadata.tables:
        return metadata.tables["weather_export_snapshots"], metadata.tables[
            "weather_export_snapshot_refs"
        ]
    snapshots = Table(
        "weather_export_snapshots",
        metadata,
        Column("snapshot_id", String(36), primary_key=True),
        Column("manifest_digest", String(64), nullable=False, index=True),
        Column("manifest", JSON, nullable=False),
        Column("source_collected_at", DateTime(timezone=True)),
        Column("data_revision_at", DateTime(timezone=True), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    )
    refs = Table(
        "weather_export_snapshot_refs",
        metadata,
        Column(
            "snapshot_id",
            String(36),
            ForeignKey("weather_export_snapshots.snapshot_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column("ordinal", Integer, primary_key=True),
        Column(
            "generation_id",
            String(36),
            ForeignKey("weather_export_generations.generation_id"),
            nullable=False,
            index=True,
        ),
    )
    return snapshots, refs


class FrozenCurrentExports:
    def __init__(
        self, repository, artifacts: ExportArtifactStore, *, signing_key: str, providers=None,
        source_inventory=False, expected_sources=(), optional_sources=(),
    ):
        if not signing_key:
            raise ValueError("기존 service token이 필요합니다.")
        self.repository = repository
        self.heads = ExportHeads(repository)
        self.snapshots, self.refs = export_snapshot_tables(self.heads.metadata)
        self.artifacts = artifacts
        self.current_providers = tuple(sorted(set(providers or ["python-kma-api"])))
        self.source_inventory = source_inventory
        self.expected_sources = tuple(expected_sources)
        self.optional_sources = tuple(optional_sources)
        self.current_selection = (
            None
            if self.current_providers == ("python-kma-api",)
            else {
                "kind": "current",
                "signature": _digest(self.current_providers),
                "providers": list(self.current_providers),
            }
        )
        if source_inventory:
            self.current_selection = {
                "kind": "current", "providers": list(self.current_providers),
                "source_inventory": 1,
                "signature": _digest([
                    self.current_providers, sorted(self.expected_sources),
                    sorted(self.optional_sources), 1,
                ]),
            }
        # 인증 체계는 기존 service header 그대로다. 이것은 cursor 변조 방지 전용이다.
        self.key = hmac.digest(signing_key.encode(), b"weather-export-cursor-v1", "sha256")

    def _encode(self, value):
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return (
            base64.urlsafe_b64encode(raw + hmac.digest(self.key, raw, "sha256"))
            .decode()
            .rstrip("=")
        )

    def _decode(self, cursor):
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
                raise ValueError
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.key, payload, "sha256")):
                raise ValueError
            value = json.loads(payload)
            if (
                not isinstance(value, dict)
                or set(value) != {"v", "s", "r", "l", "g", "p"}
                or type(value["v"]) is not int
                or value["v"] != 1
                or not isinstance(value["s"], str)
                or len(value["s"]) != 36
                or (
                    value["r"] not in {"locations", "current"}
                    and re.fullmatch(r"current:[0-9a-f]{64}", value["r"]) is None
                    and re.fullmatch(
                        r"forecasts:(ultra_short|short|external):[0-9a-f]{64}", value["r"]
                    )
                    is None
                    and re.fullmatch(r"daily-summaries:[0-9a-f]{64}", value["r"]) is None
                )
                or type(value["l"]) is not int
                or not 1 <= value["l"] <= 5000
            ):
                raise ValueError
            if any(type(value[k]) is not int or value[k] < 0 for k in ("g", "p")):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ExportCursorInvalid("cursor 형식/서명이 올바르지 않습니다.") from exc

    def _select_current_scopes(self, connection, locations):
        if self.source_inventory:
            from .export_inventory_selection import approved_source_selection

            try:
                selection = approved_source_selection(
                    connection, self.heads.metadata, locations,
                    expected=self.expected_sources, optional=self.optional_sources,
                )
            except (ExportArtifactError, KeyError, ValueError, TypeError) as exc:
                raise ExportSnapshotUnavailable(
                    "실제 승인 source 집합이 준비되지 않았습니다."
                ) from exc
            selected = {item.descriptor.namespace: item.location_ids for item in selection.scopes}
            heads = {item.descriptor.namespace: {
                "generation_id": item.generation_id,
                "source_collected_at": item.source_collected_at,
                "data_revision_at": item.data_revision_at,
            } for item in selection.scopes}
            return (
                [item.descriptor for item in selection.scopes], heads, selected,
                selection.unconfirmed,
            )
        grids = defaultdict(list)
        for location in locations:
            if "python-kma-api" in self.current_providers:
                if location["nx"] is None or location["ny"] is None:
                    raise ExportSnapshotUnavailable("활성 location grid가 준비되지 않았습니다.")
                grids[(location["nx"], location["ny"])].append(location["location_id"])
        descriptors = [
            ScopeDescriptor(
                entity_type="weather_response",
                entity_id=f"grid:{nx}:{ny}",
                nx=nx,
                ny=ny,
                location_ids=tuple(sorted(keys)),
                normalizer_revision="kma-v1",
                winner_policy_revision="kma-base-v1",
                coverage_contract="kma-grid-nowcast-v1",
            )
            for (nx, ny), keys in sorted(grids.items())
        ]
        external = set(self.current_providers) - {"python-kma-api"}
        if external:
            from .export_external_cohorts import selected_external_descriptors

            try:
                descriptors.extend(
                    selected_external_descriptors(
                        connection, self.heads.metadata, locations, external
                    )
                )
            except (ExportArtifactError, KeyError, ValueError, TypeError) as exc:
                raise ExportSnapshotUnavailable(
                    "외부 원천 선택 집합이 준비되지 않았습니다."
                ) from exc
        if len(descriptors) > 5000:
            raise ExportSnapshotUnavailable("현재 원천 vector 상한을 넘었습니다.")
        heads = {
            row["namespace"]: dict(row)
            for row in connection.execute(
                select(self.heads.heads).where(
                    self.heads.heads.c.namespace.in_([d.namespace for d in descriptors])
                )
            ).mappings()
        }
        if len(heads) != len(descriptors):
            raise ExportSnapshotUnavailable("선택 위치 전체의 성공 head가 준비되지 않았습니다.")
        return descriptors, heads, None, False

    def _freeze(self):
        # catalog와 head vector를 동일 MVCC에서 읽는다. 파일 I/O는 이 TX 밖이다.
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            connection.begin(),
        ):
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            catalog = self.heads.metadata.tables["weather_locations"]
            from .export_inventory_selection import catalog_source_columns

            locations = list(
                connection.execute(
                    select(
                        catalog.c.location_id,
                        catalog.c.name,
                        catalog.c.latitude,
                        catalog.c.longitude,
                        catalog.c.nx,
                        catalog.c.ny,
                        *(catalog_source_columns(catalog) if self.source_inventory else []),
                    )
                    .where(catalog.c.enabled.is_(True))
                    .order_by(catalog.c.location_id)
                    .limit(MAX_SNAPSHOT_LOCATIONS + 1)
                ).mappings()
            )
            if not locations or len(locations) > MAX_SNAPSHOT_LOCATIONS:
                raise ExportSnapshotUnavailable("활성 location 전체 집합이 준비되지 않았습니다.")
            descriptors, heads, selected, unconfirmed = self._select_current_scopes(
                connection, locations
            )
            generations = [heads[d.namespace]["generation_id"] for d in descriptors]
            grouped_refs = defaultdict(list)
            for row in connection.execute(
                select(self.heads.refs)
                .where(self.heads.refs.c.generation_id.in_(generations))
                .order_by(self.heads.refs.c.ordinal)
            ).mappings():
                grouped_refs[row["generation_id"]].append(dict(row))
            scopes = []
            for descriptor in descriptors:
                head = heads[descriptor.namespace]
                refs = grouped_refs[head["generation_id"]]
                if (
                    not refs
                    or len(refs) > 8
                    or sum(r["byte_count"] for r in refs) > 16 * 1024 * 1024
                ):
                    raise ExportSnapshotUnavailable(
                        "승인 manifest의 artifact 상한이 올바르지 않습니다."
                    )
                scopes.append(
                    {
                        "namespace": descriptor.namespace,
                        "generation_id": head["generation_id"],
                        "source_scope": descriptor.source_scope,
                        "nx": getattr(descriptor, "nx", None),
                        "ny": getattr(descriptor, "ny", None),
                        "provider": descriptor.provider,
                        "forecast_style": descriptor.forecast_style,
                        "location_ids": list(
                            selected[descriptor.namespace] if selected else descriptor.location_ids
                        ),
                        "refs": refs,
                        **({
                            "approved_location_ids": list(descriptor.location_ids),
                            "dataset_key": descriptor.dataset_key,
                            "source_collected_at": head["source_collected_at"].isoformat(),
                            "data_revision_at": head["data_revision_at"].isoformat(),
                        } if self.source_inventory else {}),
                    }
                )
            entries = self.heads.metadata.tables["reverse_address_entries"]
            addresses = {
                row["target_key"]: row
                for row in connection.execute(
                    select(
                        entries.c.target_key,
                        entries.c.coordinate_key,
                        entries.c.payload["address"]["legal_dong_code"]
                        .as_string()
                        .label("legal_dong_code"),
                        entries.c.payload["region"]["sido"].as_string().label("sido_name"),
                        entries.c.payload["region"]["sigungu"].as_string().label("sigungu_name"),
                    ).where(
                        entries.c.provider_id == VworldKakaoAddressResolver.provider_id,
                        entries.c.status.in_(["ok", "error"]),
                        entries.c.target_key.in_([row["location_id"] for row in locations]),
                    )
                ).mappings()
            }
            items = []
            for location in locations:
                entry = addresses.get(location["location_id"])
                if (
                    entry
                    and entry["coordinate_key"]
                    != AddressTarget(
                        location["location_id"],
                        float(location["longitude"]),
                        float(location["latitude"]),
                    ).coordinate_key
                ):
                    entry = None
                code = entry["legal_dong_code"] if entry else None
                items.append(
                    {
                        "location_id": location["location_id"],
                        "name": location["name"],
                        "latitude": float(location["latitude"]),
                        "longitude": float(location["longitude"]),
                        "legal_dong_code": code
                        if isinstance(code, str) and re.fullmatch(r"[0-9]{10}", code)
                        else None,
                        "sido_name": entry["sido_name"] if entry else None,
                        "sigungu_name": entry["sigungu_name"] if entry else None,
                    }
                )
            manifest = {"schema": 1, "scopes": scopes, "locations": items}
            if self.source_inventory:
                manifest["unconfirmed_sources"] = unconfirmed
            if self.current_selection is not None:
                manifest["selection"] = self.current_selection
            digest = _digest(manifest)
            now = datetime.now(UTC)
            latest = select(self.snapshots.c.created_at).where(
                self.snapshots.c.manifest["selection"]["kind"].as_string().is_(None)
                if self.current_selection is None
                else self.snapshots.c.manifest["selection"]["signature"].as_string()
                == self.current_selection["signature"]
            ).order_by(self.snapshots.c.created_at.desc()).limit(1).correlate(None).scalar_subquery()
            existing = (
                connection.execute(
                    select(self.snapshots)
                    .where(
                        self.snapshots.c.manifest_digest == digest,
                        self.snapshots.c.expires_at > now,
                        self.snapshots.c.created_at == latest,
                    )
                    .order_by(self.snapshots.c.created_at.desc())
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
                "source_collected_at": min(h["source_collected_at"] for h in heads.values()),
                "data_revision_at": min(h["data_revision_at"] for h in heads.values()),
                "created_at": now,
                "expires_at": now + SNAPSHOT_TTL,
            }
            connection.execute(self.snapshots.insert().values(**row))
            connection.execute(
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

    def _previous_success(self):
        # reader TTL와 마지막 승인 data manifest의 수명은 다르다. 새 handle만 만든다.
        with self.heads._transaction() as session:
            row = (
                session.execute(
                    select(self.snapshots)
                    .where(
                        self.snapshots.c.manifest["selection"]["kind"].as_string().is_(None)
                        if self.current_selection is None
                        else self.snapshots.c.manifest["selection"]["signature"].as_string()
                        == self.current_selection["signature"]
                    )
                    .order_by(self.snapshots.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ExportSnapshotUnavailable("이전 승인 집합이 없습니다.")
            frozen = dict(row)
            now = datetime.now(UTC)
            if frozen["expires_at"] <= now:
                frozen.update(
                    snapshot_id=str(uuid4()), created_at=now, expires_at=now + SNAPSHOT_TTL
                )
                session.execute(self.snapshots.insert().values(**frozen))
                session.execute(
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
            frozen["_source_unconfirmed"] = True
            return frozen

    def page(self, resource: str, *, limit: int, cursor: str | None):
        if resource not in {"locations", "current"} or not 1 <= limit <= 5000:
            raise ExportCursorInvalid("지원하지 않는 resource/limit입니다.")
        cursor_resource = (
            resource
            if self.current_selection is None
            else (resource + ":" + self.current_selection["signature"])
        )
        if cursor:
            state = self._decode(cursor)
            if state["r"] != cursor_resource or state["l"] != limit:
                raise ExportCursorInvalid("cursor의 resource/limit은 변경할 수 없습니다.")
            with self.heads._transaction() as session:
                frozen = (
                    session.execute(
                        select(self.snapshots).where(self.snapshots.c.snapshot_id == state["s"])
                    )
                    .mappings()
                    .one_or_none()
                )
                if frozen is None or frozen["expires_at"] <= datetime.now(UTC):
                    raise ExportSnapshotExpired(
                        "snapshot이 만료됐습니다. 첫 페이지부터 다시 요청하세요."
                    )
                frozen = dict(frozen)
        else:
            try:
                frozen = self._freeze()
            except ExportSnapshotUnavailable:
                frozen = self._previous_success()
            state = {
                "v": 1,
                "s": frozen["snapshot_id"],
                "r": cursor_resource,
                "l": limit,
                "g": 0,
                "p": 0,
            }
        manifest = frozen["manifest"]
        output, size = [], 0
        if resource == "locations":
            start = state["p"]
            if state["g"] != 0 or start > len(manifest["locations"]):
                raise ExportCursorInvalid("cursor 범위가 올바르지 않습니다.")
            for item in manifest["locations"][start : start + limit]:
                item_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
                if item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                    raise ExportSnapshotUnavailable(
                        "단일 location이 페이지 byte 상한을 넘었습니다."
                    )
                if size + item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                    break
                output.append(item)
                size += item_size
            state["p"] = start + len(output)
            more = state["p"] < len(manifest["locations"])
        else:
            scopes = manifest["scopes"]
            if state["g"] > len(scopes):
                raise ExportCursorInvalid("cursor 범위가 올바르지 않습니다.")
            more = False
            # 현재는 GC가 없으며 durable snapshot_refs가 generation의 삭제도 막는다.
            # 파일은 매 페이지 동일fd hash/count를 재검증하고 누락은 성공으로 반환하지 않는다.
            with self.artifacts.guard(timeout_seconds=2):
                while state["g"] < len(scopes):
                    scope = scopes[state["g"]]
                    points = []
                    selected = set(scope["location_ids"])
                    approved_ids = set(scope.get("approved_location_ids", scope["location_ids"]))
                    for ref in scope["refs"]:
                        for point in self.artifacts.read(
                            ref["artifact_digest"],
                            byte_count=ref["byte_count"],
                            point_count=ref["point_count"],
                        ):
                            if (
                                point["provider"] != scope.get("provider", "python-kma-api")
                                or point["forecast_style"] != scope.get("forecast_style", "nowcast")
                                or point["location_id"] not in approved_ids
                            ):
                                raise ExportSnapshotUnavailable(
                                    "승인된 scope와 artifact가 다릅니다."
                                )
                            if point["location_id"] in selected:
                                points.append(point)
                    points.sort(key=lambda p: (p["location_id"], p["provider"], p["metric_key"]))
                    if state["p"] > len(points):
                        raise ExportCursorInvalid("cursor 범위가 올바르지 않습니다.")
                    while state["p"] < len(points):
                        point = points[state["p"]]
                        item = {
                            "location_id": point["location_id"],
                            "provider": point["provider"],
                            "metric_key": point["metric_key"],
                            "value": float(point["value_number"])
                            if point["value_number"] is not None
                            else point["value_text"],
                            "unit": point["unit"],
                            "target_at": point["target_at"],
                            "known_at": point["known_at"],
                            "forecast_style": point["forecast_style"],
                        }
                        item_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
                        if item_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                            raise ExportSnapshotUnavailable(
                                "단일 point가 페이지 byte 상한을 넘었습니다."
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
                    state["g"] += 1
                    state["p"] = 0
        now = datetime.now(UTC)
        stale = (
            frozen.get("_source_unconfirmed", False)
            or now - frozen["source_collected_at"] > SOURCE_MAX_AGE
            or now - frozen["data_revision_at"] > SOURCE_MAX_AGE
        )
        if self.source_inventory:
            from .export_inventory_selection import source_max_age

            stale = frozen.get("_source_unconfirmed", False) or manifest["unconfirmed_sources"]
            stale |= any(
                now - datetime.fromisoformat(scope[key]) > source_max_age(scope["dataset_key"])
                for scope in manifest["scopes"]
                for key in ("source_collected_at", "data_revision_at")
            )
        owners, attempts = self.heads.lifecycle.owners, self.heads.lifecycle.attempts
        runs = self.heads.metadata.tables["weather_sync_runs"]
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as session, session.begin(),
        ):
            session.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            catalog = self.heads.metadata.tables["weather_locations"]
            from .export_inventory_selection import catalog_source_columns

            roster = session.execute(
                select(
                    catalog.c.location_id,
                    catalog.c.nx,
                    catalog.c.ny,
                    catalog.c.latitude,
                    catalog.c.longitude,
                    *(catalog_source_columns(catalog) if self.source_inventory else []),
                )
                .where(catalog.c.enabled.is_(True))
                .order_by(catalog.c.location_id)
                .limit(MAX_SNAPSHOT_LOCATIONS + 1)
            ).all()
            stale |= len(roster) > MAX_SNAPSHOT_LOCATIONS
            if self.source_inventory:
                from .export_inventory_selection import approved_source_selection

                try:
                    latest = approved_source_selection(
                        session, self.heads.metadata, [row._mapping for row in roster],
                        expected=self.expected_sources, optional=self.optional_sources,
                    )
                    stale |= latest.unconfirmed or {
                        (item.descriptor.namespace, item.location_ids) for item in latest.scopes
                    } != {
                        (scope["namespace"], tuple(scope["location_ids"]))
                        for scope in manifest["scopes"]
                    }
                except (ExportArtifactError, KeyError, ValueError, TypeError):
                    stale = True
            else:
                expected = sorted(
                    (key, scope["nx"], scope["ny"])
                    for scope in manifest["scopes"]
                    if scope.get("provider", "python-kma-api") == "python-kma-api"
                    for key in scope["location_ids"]
                )
                if "python-kma-api" in self.current_providers:
                    stale |= [tuple(row)[:3] for row in roster] != expected
                else:
                    stale |= [
                        (row.location_id, float(row.latitude), float(row.longitude))
                        for row in roster
                    ] != [
                        (row["location_id"], row["latitude"], row["longitude"])
                        for row in manifest["locations"]
                    ]
                external = set(self.current_providers) - {"python-kma-api"}
                if external:
                    from .export_external_cohorts import selected_external_descriptors

                    try:
                        current_external = selected_external_descriptors(
                            session, self.heads.metadata, [row._mapping for row in roster], external
                        )
                        stale |= {d.namespace for d in current_external} != {
                            scope["namespace"]
                            for scope in manifest["scopes"]
                            if scope.get("provider", "python-kma-api") != "python-kma-api"
                        }
                    except (ExportArtifactError, KeyError, ValueError, TypeError):
                        stale = True
            # cursor로 같은 reader handle을 이어 읽어도 현재 head 유효성을 확인한다.
            # 이전 manifest를 반환했다는 메모리 flag만으로는 다음 요청에 stale이
            # 사라져 삭제/복구 중인 source를 정상이라고 표시할 수 있다.
            current_namespaces = set(
                session.scalars(
                    select(self.heads.heads.c.namespace)
                    .join(
                        self.heads.generations,
                        self.heads.generations.c.generation_id == self.heads.heads.c.generation_id,
                    )
                    .where(
                        self.heads.heads.c.namespace.in_(
                            [scope["namespace"] for scope in manifest["scopes"]]
                        )
                    )
                )
            )
            stale |= current_namespaces != {scope["namespace"] for scope in manifest["scopes"]}
            statuses = session.execute(
                select(attempts.c.status, runs.c.status, self.heads.generations.c.generation_id)
                .select_from(
                    owners.join(attempts, owners.c.active_attempt_id == attempts.c.attempt_id)
                    .join(runs, runs.c.run_id == attempts.c.run_id)
                    .outerjoin(
                        self.heads.generations,
                        self.heads.generations.c.attempt_id == attempts.c.attempt_id,
                    )
                )
                .where(
                    owners.c.source_scope.in_(
                        [scope["source_scope"] for scope in manifest["scopes"]]
                    )
                )
            ).all()
        stale |= len(statuses) != len({s["source_scope"] for s in manifest["scopes"]}) or any(
            a == "failed"
            or run not in {"running", "success"}
            or (run == "success" and generation is None)
            for a, run, generation in statuses
        )
        status = "stale" if stale else "ok"
        meta = {
            "generated_at": now.isoformat(),
            "source_collected_at": frozen["source_collected_at"].isoformat(),
            "source_status": status,
            "item_count": len(output),
            "snapshot_id": frozen["snapshot_id"],
            "snapshot_generated_at": frozen["created_at"].isoformat(),
            "as_of": frozen["created_at"].isoformat(),
            "next_cursor": self._encode(state) if more else None,
            "has_more": more,
        }
        # generated_at은 ETag에 넣지 않는다. 데이터/vector/페이지/상태 변화는 포함한다.
        etag = (
            'W/"'
            + _digest(
                [frozen["snapshot_id"], frozen["manifest_digest"], resource, limit, cursor, status]
            )
            + '"'
        )
        return {"items": output, "meta": meta}, etag
