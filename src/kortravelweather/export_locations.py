"""W1 catalog은 날씨 head와 독립된 reference다. 미확인 원천 clock은 null이다."""

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import AddressTarget
from sqlalchemy import select, text

from .export_heads import _digest
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


class FrozenLocationExports(FrozenCurrentExports):
    def _previous(self, connection):
        row = (
            connection.execute(
                select(self.snapshots)
                .where(self.snapshots.c.manifest["selection"]["kind"].as_string() == "locations")
                .order_by(self.snapshots.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def _renew(self, connection, frozen):
        now = datetime.now(UTC)
        if frozen["expires_at"] <= now:
            frozen = {
                **frozen,
                "snapshot_id": str(uuid4()),
                "created_at": now,
                "expires_at": now + SNAPSHOT_TTL,
            }
            connection.execute(self.snapshots.insert().values(**frozen))
        return frozen

    def _freeze_locations(self):
        with (
            self.repository.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as c,
            c.begin(),
        ):
            c.execute(text("SET LOCAL statement_timeout = '5s'"))
            catalog = self.heads.metadata.tables["weather_locations"]
            rows = list(
                c.execute(
                    select(
                        catalog.c.location_id,
                        catalog.c.name,
                        catalog.c.latitude,
                        catalog.c.longitude,
                        catalog.c.updated_at,
                    )
                    .where(catalog.c.enabled.is_(True))
                    .order_by(catalog.c.location_id)
                    .limit(MAX_SNAPSHOT_LOCATIONS + 1)
                ).mappings()
            )
            previous = self._previous(c)
            if not rows or len(rows) > MAX_SNAPSHOT_LOCATIONS:
                if previous is not None:
                    return self._renew(c, previous)
                raise ExportSnapshotUnavailable("활성 reference 전체 집합이 준비되지 않았습니다.")
            if previous and len(rows) * 2 < len(previous["manifest"]["locations"]):
                removed = {r["location_id"] for r in previous["manifest"]["locations"]} - {
                    r["location_id"] for r in rows
                }
                disabled = set(
                    c.scalars(
                        select(catalog.c.location_id).where(
                            catalog.c.location_id.in_(removed), catalog.c.enabled.is_(False)
                        )
                    )
                )
                if removed != disabled:
                    return self._renew(c, previous)
            entries = self.heads.metadata.tables["reverse_address_entries"]
            addresses = {
                r["target_key"]: r
                for r in c.execute(
                    select(
                        entries.c.target_key,
                        entries.c.coordinate_key,
                        entries.c.payload["address"]["legal_dong_code"].as_string().label("code"),
                        entries.c.payload["region"]["sido"].as_string().label("sido"),
                        entries.c.payload["region"]["sigungu"].as_string().label("sigungu"),
                    ).where(
                        entries.c.provider_id == VworldKakaoAddressResolver.provider_id,
                        entries.c.status.in_(["ok", "error"]),
                        entries.c.target_key.in_([r["location_id"] for r in rows]),
                    )
                ).mappings()
            }
            items = []
            for row in rows:
                address = addresses.get(row["location_id"])
                if (
                    address
                    and address["coordinate_key"]
                    != AddressTarget(
                        row["location_id"], float(row["longitude"]), float(row["latitude"])
                    ).coordinate_key
                ):
                    address = None
                code = address["code"] if address else None
                items.append(
                    {
                        "location_id": row["location_id"],
                        "name": row["name"],
                        "latitude": float(row["latitude"]),
                        "longitude": float(row["longitude"]),
                        "legal_dong_code": code
                        if isinstance(code, str) and re.fullmatch(r"[0-9]{10}", code)
                        else None,
                        "sido_name": address["sido"] if address else None,
                        "sigungu_name": address["sigungu"] if address else None,
                    }
                )
            manifest = {"selection": {"kind": "locations"}, "locations": items}
            digest, now = _digest(manifest), datetime.now(UTC)
            existing = (
                c.execute(
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
            frozen = {
                "snapshot_id": str(uuid4()),
                "manifest_digest": digest,
                "manifest": manifest,
                # updated_at은 catalog revision이며 provider 실제 조회 시각으로 대신하지 않는다.
                "source_collected_at": None,
                "data_revision_at": max(r["updated_at"] for r in rows),
                "created_at": now,
                "expires_at": now + SNAPSHOT_TTL,
            }
            c.execute(self.snapshots.insert().values(**frozen))
            return frozen

    def page(self, resource, *, limit, cursor):
        if resource != "locations" or not 1 <= limit <= 5000:
            raise ExportCursorInvalid("reference resource/limit이 올바르지 않습니다.")
        if cursor:
            state = self._decode(cursor)
            if state["r"] != "locations" or state["l"] != limit or state["g"] != 0:
                raise ExportCursorInvalid("reference cursor 조건을 바꿀 수 없습니다.")
            with self.heads._transaction() as s:
                row = (
                    s.execute(
                        select(self.snapshots).where(self.snapshots.c.snapshot_id == state["s"])
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None or row["expires_at"] <= datetime.now(UTC):
                raise ExportSnapshotExpired("reference cursor가 만료됐습니다.")
            frozen = dict(row)
        else:
            frozen = self._freeze_locations()
            state = {
                "v": 1,
                "s": frozen["snapshot_id"],
                "r": "locations",
                "l": limit,
                "g": 0,
                "p": 0,
            }
        if frozen["manifest"].get("selection") != {"kind": "locations"}:
            raise ExportCursorInvalid("다른 resource의 reference cursor입니다.")
        items = frozen["manifest"]["locations"]
        if state["p"] > len(items):
            raise ExportCursorInvalid("reference cursor 위치가 올바르지 않습니다.")
        output, size = [], 0
        for item in items[state["p"] : state["p"] + limit]:
            encoded_size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
            if encoded_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                raise ExportSnapshotUnavailable("단일 reference가 페이지 byte 상한을 넘었습니다.")
            if size + encoded_size > MAX_PAGE_BYTES - PAGE_META_RESERVE:
                break
            output.append(item)
            size += encoded_size
        state["p"] += len(output)
        more = state["p"] < len(items)
        page = {
            "items": output,
            "meta": {
                "generated_at": datetime.now(UTC).isoformat(),
                "source_collected_at": None,
                "source_status": "stale",
                "item_count": len(output),
                "snapshot_id": frozen["snapshot_id"],
                "snapshot_generated_at": frozen["created_at"].isoformat(),
                "as_of": frozen["created_at"].isoformat(),
                "has_more": more,
                "next_cursor": self._encode(state) if more else None,
            },
        }
        return page, 'W/"' + _digest([frozen["snapshot_id"], cursor, limit]) + '"'
