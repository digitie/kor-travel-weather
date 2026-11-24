"""요청의 다음 실제 출력 구간만 읽는 bounded DB 창. 파일 backend는 기존 경로 유지."""

from .export_artifacts import ExportArtifactError
from .export_database_artifacts import MAX_ARTIFACT_READ_BATCH, MAX_ARTIFACT_READ_BYTES


class ArtifactReadWindow:
    def __init__(self, artifacts, *, fields=None, max_bytes=None, batch=MAX_ARTIFACT_READ_BATCH):
        self.artifacts = artifacts
        self.batch = batch
        self.bulk = getattr(artifacts, "read_many", None)
        self.fields = fields if getattr(artifacts, "is_database", False) else None
        self.points = {}
        self.receipts = {}
        self.max_bytes, self.bytes_read = max_bytes, 0

    def can_read(self, ref):
        # 선읽기한 ref는 추가 I/O 없이 소비한다. 첫 단일 artifact는 진행을 허용한다.
        return (self.max_bytes is None or ref["artifact_digest"] in self.points
                or self.bytes_read == 0
                or self.bytes_read + ref["byte_count"] <= self.max_bytes)

    def prepare(self, first, candidates, *, remaining_reads):
        if (not callable(self.bulk) or first["artifact_digest"] in self.points
                or remaining_reads <= 0 or not self.can_read(first)):
            return
        refs, seen, size = [], set(), 0
        byte_limit = MAX_ARTIFACT_READ_BYTES
        if self.max_bytes is not None:
            byte_limit = min(byte_limit, self.max_bytes - self.bytes_read)
        for ref in candidates():
            digest = ref["artifact_digest"]
            if digest in seen:
                continue
            if len(refs) >= min(self.batch, remaining_reads):
                break
            if size + ref["byte_count"] > byte_limit and (
                refs or self.bytes_read or ref["byte_count"] > MAX_ARTIFACT_READ_BYTES
            ):
                break
            refs.append(ref)
            seen.add(digest)
            size += ref["byte_count"]
        self.points.clear()
        self.receipts.clear()
        if refs:
            options = {} if self.fields is None else {"fields": self.fields}
            if self.batch != MAX_ARTIFACT_READ_BATCH:
                options["max_batch"] = self.batch
            self.points.update(self.bulk(refs, **options))
            self.receipts.update({ref["artifact_digest"]: (ref["byte_count"], ref["point_count"])
                                  for ref in refs})
            # 검산된 실제 payload 크기와 같은 receipt를 선읽기 전체에 대해 센다.
            self.bytes_read += size

    def read(self, ref):
        digest = ref["artifact_digest"]
        if digest in self.points:
            if self.receipts[digest] != (ref["byte_count"], ref["point_count"]):
                raise ExportArtifactError("같은 artifact의 receipt가 서로 다릅니다.")
            return self.points[digest]
        if not self.can_read(ref):
            raise ExportArtifactError("페이지 artifact 입력 byte 상한을 넘었습니다.")
        points = self.artifacts.read_shared(
            digest, byte_count=ref["byte_count"], point_count=ref["point_count"],
        )
        self.bytes_read += ref["byte_count"]
        return points


def current_candidates(scopes, start, *, points, max_scopes, ref_start=0):
    observed = 0
    for offset, scope in enumerate(scopes[start:start + max_scopes]):
        for ref in scope["refs"][ref_start if offset == 0 else 0:]:
            yield ref
            observed += ref["point_count"]
        if observed >= points:
            return


def forecast_candidates(scopes, start, position, *, locations, max_scopes, since):
    observed = 0
    for offset, scope in enumerate(scopes[start:start + max_scopes]):
        if since is not None:
            from datetime import datetime
            if datetime.fromisoformat(scope["published_at"]) <= since:
                continue
        keys = scope["location_ids"][position if offset == 0 else 0:]
        for key in keys:
            refs = [ref for ref in scope["refs"] if key in ref["location_ids"]]
            if len(refs) != 1:
                return  # 실제 소비자가 같은 위치에 도달하면 기존 계약대로 실패한다.
            yield refs[0]
            observed += 1
            if observed >= locations:
                return
