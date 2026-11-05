"""공식 구역 코드·상위 관계·전체 행정구역 안내·Juso를 대조한다. 원본은 수정하지 않는다."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


class GuideRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.cells = None
        self.cell = None

    def finish(self):
        if self.cell is not None:
            self.cells.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        if tag in {"td", "th"} and self.cells is not None:
            self.finish()
            self.cell = []
        if tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cells is not None:
            self.finish()
        if tag == "tr" and self.cells is not None:
            self.finish()
            self.rows.append(self.cells)
            self.cells = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def registry_rows(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared = [
            "".join(cell.itertext())
            for cell in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)
        ]
        rows = []
        for row in ET.fromstring(archive.read("xl/worksheets/sheet2.xml")).findall(".//m:row", ns):
            fields = {}
            for cell in row.findall("m:c", ns):
                value = cell.find("m:v", ns)
                raw = "" if value is None else value.text
                fields[re.sub("[0-9]", "", cell.get("r"))] = (
                    shared[int(raw)] if cell.get("t") == "s" else raw
                )
            rows.append(fields)
    assert [rows[0].get(k) for k in "ABCDE"] == [
        "# REG_ID",
        "TM_ST",
        "TM_ED",
        "REG_SP",
        "REG_UP",
    ]
    return {row["A"]: row for row in rows[1:] if row.get("A")}


def juso_administration(path):
    provinces = defaultdict(set)
    districts = defaultdict(set)
    suffixes = defaultdict(set)
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".txt"):
                continue
            with archive.open(name) as source:
                for line in io.TextIOWrapper(source, encoding="cp949"):
                    row = line.rstrip("\r\n").split("|")
                    if len(row) < 5 or not re.fullmatch("[0-9]{10}", row[1]):
                        continue
                    provinces[row[1][:2]].add(row[2])
                    districts[row[1][:5]].add((row[2], row[3]))
                    suffixes[row[1][:5]].add(row[1][5:])
    return (
        {k: next(iter(v)) for k, v in provinces.items() if len(v) == 1},
        {k: next(iter(v)) for k, v in districts.items() if len(v) == 1},
        {k: ",".join(sorted(v)) for k, v in sorted(suffixes.items())},
    )


def build(registry, zones, guide, juso, verified_at):
    if verified_at.tzinfo is None:
        raise ValueError("verified-at은 timezone-aware여야 합니다.")
    records = registry_rows(registry)
    provinces, districts, suffixes = juso_administration(juso)
    province_codes = {name: code for code, name in provinces.items()}
    parser = GuideRows()
    parser.feed(guide.read_text())
    by_name = defaultdict(set)
    for row in parser.rows:
        if len(row) == 3:
            by_name[row[1]].add(row[2])

    # 공식 상위 코드의 행정구역명 또는 같은 안내의 시도 전체 이름만 별칭으로 연결한다.
    aliases = dict(province_codes)
    registry_names = defaultdict(set)
    for row in records.values():
        registry_names[row.get("F")].add(row.get("G"))
    for row in records.values():
        # 광주시/광주광역시처럼 중복된 약칭은 상위 코드 탐색을 가로채지 않는다.
        if len(registry_names[row.get("F")]) != 1:
            continue
        canonical = province_codes.get(row.get("G"))
        if canonical:
            aliases[row["F"]] = canonical
        for coverage in by_name[row.get("F")]:
            if coverage in province_codes:
                aliases.setdefault(row["F"], province_codes[coverage])

    def province_for(code):
        seen = set()
        while code in records and code not in seen:
            seen.add(code)
            row = records[code]
            province = province_codes.get(row.get("G")) or aliases.get(row.get("F"))
            if province:
                return province
            code = row["E"]
        return None

    def resolve(coverage, province):
        # 명시된 다른 시도 안내는 동명이역이다.
        if coverage in province_codes:
            return ("other", []) if province_codes[coverage] != province else ("ok", [province])
        for alias, code in sorted(aliases.items(), key=lambda item: -len(item[0])):
            if coverage.startswith(alias + " "):
                if code != province:
                    return "other", []
                coverage = coverage[len(alias) + 1 :]
                break
            if coverage.startswith(alias + "(") and code != province:
                return "other", []
        parts = coverage.split(".")
        if not all(re.fullmatch("[가-힣]+[시군구]", part) for part in parts):
            return "unknown", []
        selected = []
        for part in parts:
            matches = [
                (key, item)
                for key, item in districts.items()
                if item[1] == part or item[1].startswith(part + " ")
            ]
            local = [key for key, item in matches if key.startswith(province)]
            if not local:
                return ("other" if matches else "unknown"), []
            selected.extend(local)
        return "ok", sorted(set(selected))

    result = []
    for zone in csv.DictReader(zones.read_text(encoding="cp949").splitlines()):
        code, name = zone["코드"], zone["특보구역 명"]
        row = records.get(code)
        entry = {
            "area_code": code,
            "area_name": name,
            "scope_status": "unknown",
            "bjd_prefixes": [],
            "guide_coverage": [],
        }
        if row and row["F"] == name:
            entry.update(parent_code=row["E"], code_valid_from=row["B"], code_valid_until=row["C"])
            province = province_for(code)
            outcomes = (
                [(coverage, *resolve(coverage, province)) for coverage in sorted(by_name[name])]
                if province and code.startswith("L")
                else []
            )
            relevant = [item for item in outcomes if item[1] != "other"]
            scopes = {tuple(item[2]) for item in relevant if item[1] == "ok"}
            if relevant and all(item[1] == "ok" for item in relevant) and len(scopes) == 1:
                entry.update(
                    scope_status="verified",
                    bjd_prefixes=list(next(iter(scopes))),
                    guide_coverage=[item[0] for item in relevant],
                )
        result.append(entry)
    return {
        "version": "official-260601-juso-202605-v1",
        "namespace": "kr_bjd",
        "mapping_valid_from": verified_at.isoformat(),
        "mapping_valid_until": None,
        "sources": {
            "registry": {
                "sha256": digest(registry),
                "url": "https://www.data.go.kr/data/15000415/openapi.do",
            },
            "zones": {
                "sha256": digest(zones),
                "url": "https://www.data.go.kr/data/15043573/fileData.do",
            },
            "guide": {
                "sha256": digest(guide),
                "url": "https://www.weather.go.kr/w/forecast/guide/wrn-area.do",
            },
            "juso": {
                "file": juso.name,
                "sha256": digest(juso),
                "url": "https://business.juso.go.kr/addrlink/elctrnMapProvd/geoDBDwldList.do",
            },
        },
        "known_province_codes": sorted(provinces),
        "known_district_codes": sorted(districts),
        "known_bjd_suffixes": suffixes,
        "areas": result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registry", "zones", "guide", "juso", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--verified-at", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    document = build(args.registry, args.zones, args.guide, args.juso, args.verified_at)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    print(
        len(document["areas"]),
        "구역, verified",
        sum(row["scope_status"] == "verified" for row in document["areas"]),
    )
