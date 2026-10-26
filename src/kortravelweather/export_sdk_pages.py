"""SDK 공개 Page의 완결 증거와 KHOA 페이지 경계 조합. HTTP는 호출하지 않는다."""

from dataclasses import dataclass, field

from .export_artifacts import ExportArtifactError

MAX_RAW_ROWS = 25000
MAX_PLACES = 1000


@dataclass
class SdkPageReceipt:
    """동일 total_count와 연속 페이지/원문 개수를 확인한다. DTO만 세지 않는다."""

    total: int | None = None
    page_no: int = 0
    consumed: int = 0
    page_size: int | None = None
    failed: bool = False

    def add(self, page, *, raw_count):
        total, number, size = page.total_count, page.page_no, page.num_of_rows
        if (
            self.failed or self.complete
            or type(total) is not int or not 1 <= total <= MAX_RAW_ROWS
            or type(number) is not int or number != self.page_no + 1
            or type(size) is not int or not 1 <= size <= MAX_RAW_ROWS
            or type(raw_count) is not int or raw_count < 0
            or (self.total is not None and total != self.total)
            or (self.page_size is not None and size != self.page_size)
            or raw_count != min(size, total - self.consumed)
        ):
            self.failed = True
            raise ExportArtifactError("SDK 페이지의 전체 개수/연속성/파싱 결과가 불완전합니다.")
        self.total, self.page_no, self.page_size = total, number, size
        self.consumed += raw_count

    def add_snapshot(self, page, *, raw_count):
        """페이지 없이 전체를 한 번에 주는 응답(``count``와 원문 목록만 있음)의 완결.

        data.ex.co.kr restWeatherList는 pageNo/numOfRows 없이 ``count``와 ``list``만
        돌려준다(운영 실측 2026-10-27: count 187, list 187). 두 페이지 필드가 모두 없고
        ``count``가 원문 행 수와 같을 때만 완결이다. 한쪽 필드만 있는 응답은 거부한다.
        """
        total = page.total_count
        if (
            self.failed or self.total is not None
            or page.page_no is not None or page.num_of_rows is not None
            or type(total) is not int or not 1 <= total <= MAX_RAW_ROWS
            or type(raw_count) is not int or raw_count != total
        ):
            self.failed = True
            raise ExportArtifactError("SDK 전체 응답의 개수/파싱 결과가 불완전합니다.")
        self.total, self.page_no, self.page_size = total, 1, total
        self.consumed = raw_count

    @property
    def complete(self):
        return not self.failed and self.total is not None and self.consumed == self.total


@dataclass
class BeachPageAssembly:
    """페이지마다 분리된 같은 해수욕장 원문을 bounded 장소 단위로 조합한다."""

    receipt: SdkPageReceipt = field(default_factory=SdkPageReceipt)
    _places: dict = field(default_factory=dict)

    def add(self, page):
        try:
            self._add(page)
        except (ExportArtifactError, AttributeError, TypeError, ValueError, KeyError) as exc:
            self.receipt.failed = True
            raise ExportArtifactError("해수욕장 페이지 조합 증명이 불완전합니다.") from exc

    def _add(self, page):
        rows = 0
        for place in page.items:
            raw = place.raw.get("rows")
            if not isinstance(raw, list) or not 1 <= len(raw) <= 62:
                raise ExportArtifactError("해수욕장 공개 DTO의 원문 행이 불완전합니다.")
            rows += len(raw)
        self.receipt.add(page, raw_count=rows)
        for place in page.items:
            key = (place.id, place.name, place.latitude, place.longitude)
            if key not in self._places:
                if len(self._places) >= MAX_PLACES:
                    raise ExportArtifactError("해수욕장 조합 장소 상한을 넘었습니다.")
                self._places[key] = (place, [])
            target = self._places[key][1]
            if len(target) + len(place.raw["rows"]) > 62:
                raise ExportArtifactError("해수욕장별 예보 원문 상한을 넘었습니다.")
            target.extend(place.raw["rows"])

    def finish(self):
        from khoa import Observatory, parse_beach_index_place

        if not self.receipt.complete:
            raise ExportArtifactError("해수욕장 원문 전체 페이지가 아직 준비되지 않았습니다.")
        result = []
        for key in sorted(self._places):
            place, rows = self._places[key]
            combined = parse_beach_index_place(
                Observatory.model_validate({
                    key: getattr(place, key) for key in Observatory.model_fields
                    if hasattr(place, key)
                }), rows,
            )
            slots = [(item.predicted_on, item.forecast_period) for item in combined.forecasts]
            if len(slots) != len(set(slots)):
                raise ExportArtifactError("페이지 사이 같은 날짜/구간 예보가 중복됐습니다.")
            result.append(combined)
        return result
