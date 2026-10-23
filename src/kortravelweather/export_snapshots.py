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
        Column("source_collected_at", DateTime(timezone=True), nullable=False),
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
    def __init__(self, repository, artifacts: ExportArtifactStore, *, signing_key: str):
        if not signing_key:
            raise ValueError("기존 service token이 필요합니다.")
        self.repository = repository
        self.heads = ExportHeads(repository)
        self.snapshots, self.refs = export_snapshot_tables(self.heads.metadata)
        self.artifacts = artifacts
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
                or value["r"] not in {"locations", "current"}
                or type(value["l"]) is not int
                or not 1 <= value["l"] <= 5000
            ):
                raise ValueError
            if any(type(value[k]) is not int or value[k] < 0 for k in ("g", "p")):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ExportCursorInvalid("cursor 형식/서명이 올바르지 않습니다.") from exc

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
            locations = list(
                connection.execute(
                    select(catalog)
                    .where(catalog.c.enabled.is_(True))
                    .order_by(catalog.c.location_id)
                    .limit(MAX_SNAPSHOT_LOCATIONS + 1)
                ).mappings()
            )
            if not locations or len(locations) > MAX_SNAPSHOT_LOCATIONS:
                raise ExportSnapshotUnavailable("활성 location 전체 집합이 준비되지 않았습니다.")
            grids = defaultdict(list)
            for location in locations:
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
                        "location_ids": list(descriptor.location_ids),
                        "refs": refs,
                    }
                )
            entries = self.heads.metadata.tables["reverse_address_entries"]
            addresses = {
                row["target_key"]: row
                for row in connection.execute(
                    select(entries).where(
                        entries.c.provider_id == VworldKakaoAddressResolver.provider_id,
                        entries.c.target_key.in_([row["location_id"] for row in locations]),
                    )
                ).mappings()
            }
            items = []
            for location in locations:
                entry = addresses.get(location["location_id"])
                payload = (
                    entry["payload"]
                    if (
                        entry
                        and entry["coordinate_key"]
                        == AddressTarget(
                            location["location_id"],
                            float(location["longitude"]),
                            float(location["latitude"]),
                        ).coordinate_key
                    )
                    else {}
                )
                payload = payload or {}
                address, region = payload.get("address") or {}, payload.get("region") or {}
                code = address.get("legal_dong_code")
                items.append(
                    {
                        "location_id": location["location_id"],
                        "name": location["name"],
                        "latitude": float(location["latitude"]),
                        "longitude": float(location["longitude"]),
                        "legal_dong_code": code
                        if isinstance(code, str) and re.fullmatch(r"\d{10}", code)
                        else None,
                        "sido_name": region.get("sido"),
                        "sigungu_name": region.get("sigungu"),
                    }
                )
            manifest = {"schema": 1, "scopes": scopes, "locations": items}
            digest = _digest(manifest)
            now = datetime.now(UTC)
            existing = (
                connection.execute(
                    select(self.snapshots)
                    .where(
                        self.snapshots.c.manifest_digest == digest,
                        self.snapshots.c.expires_at > now,
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

    def page(self, resource: str, *, limit: int, cursor: str | None):
        if resource not in {"locations", "current"} or not 1 <= limit <= 5000:
            raise ExportCursorInvalid("지원하지 않는 resource/limit입니다.")
        if cursor:
            state = self._decode(cursor)
            if state["r"] != resource or state["l"] != limit:
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
            frozen = self._freeze()
            state = {"v": 1, "s": frozen["snapshot_id"], "r": resource, "l": limit, "g": 0, "p": 0}
        manifest = frozen["manifest"]
        output, size = [], 0
        if resource == "locations":
            start = state["p"]
            if state["g"] != 0 or start > len(manifest["locations"]):
                raise ExportCursorInvalid("cursor 범위가 올바르지 않습니다.")
            output = manifest["locations"][start : start + limit]
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
                    for ref in scope["refs"]:
                        for point in self.artifacts.read(
                            ref["artifact_digest"],
                            byte_count=ref["byte_count"],
                            point_count=ref["point_count"],
                        ):
                            if (
                                point["provider"] != "python-kma-api"
                                or point["forecast_style"] != "nowcast"
                                or point["location_id"] not in selected
                            ):
                                raise ExportSnapshotUnavailable(
                                    "승인된 scope와 artifact가 다릅니다."
                                )
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
                        item_size = len(json.dumps(item, ensure_ascii=False).encode())
                        if len(output) >= limit or size + item_size > MAX_PAGE_BYTES:
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
            now - frozen["source_collected_at"] > SOURCE_MAX_AGE
            or now - frozen["data_revision_at"] > SOURCE_MAX_AGE
        )
        owners, attempts = self.heads.lifecycle.owners, self.heads.lifecycle.attempts
        runs = self.heads.metadata.tables["weather_sync_runs"]
        with self.heads._transaction() as session:
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
        stale |= len(statuses) != len(manifest["scopes"]) or any(
            a == "failed"
            or run in {"failed", "partial", "skipped"}
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
        etag = '"' + _digest([frozen["manifest_digest"], resource, limit, cursor, status]) + '"'
        return {"items": output, "meta": meta}, etag
