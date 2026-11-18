"""기존 JSONL의 행별 의미와 nested 반환 객체 격리를 유지한다."""

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from kortravelweather.export_artifacts import ExportArtifactError
from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore


@pytest.fixture
def decoder():
    engine = create_engine("sqlite://")
    yield DatabaseExportArtifactStore(SimpleNamespace(engine=engine))
    engine.dispose()


def decode(decoder, payload):
    digest = hashlib.sha256(payload).hexdigest()
    decoder._verify_payload_row(
        SimpleNamespace(payload=payload, byte_count=len(payload),
                        point_count=len(payload.splitlines())),
        digest, len(payload), len(payload.splitlines()),
    )
    return list(decoder._decoded_points(digest, payload, len(payload)))


@pytest.mark.parametrize("payload", [
    b'{"x":1}\n', b' {"x":1} \r\n', b'{"x":-0.0,"y":true,"z":null}\n',
    b'{"x":NaN,"y":Infinity,"z":-Infinity}\n',
    b'{"x":1,"x":2,"nested":{"x":[],"x":[1]}}\n',
    b'{"x":"\\ud800"}\n', b'{"x":"\\udfff"}\n',
    '{"x":"한글 🧭"}\n'.encode(),
    b'\xef\xbb\xbf{"x":1}\n', '{"x":1}'.encode("utf-16"),
    '{"x":1}'.encode("utf-32"),
    b'{"x":5e-324,"y":2.2250738585072014e-308,"z":1.7976931348623157e308}\n',
    b'{"x":1.234567890123456789,"y":1e309}\n',
    b'{"x":{},"y":[null,{},[1]],"escaped":"}\\n{"}\n', b'',
])
def test_decoder_preserves_old_valid_jsonl_and_encodings(decoder, payload):
    expected = [json.loads(line) for line in payload.splitlines()]
    for _ in range(2):  # cold and cached return copies
        actual = decode(decoder, payload)
        assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


@pytest.mark.parametrize("payload", [
    b'\n', b'{}\n\n', b'{},{}\n', b'{"x":1} trailing\n',
    b'{}, {"a":"}\n{","b":"x"}\n', b'{"x":{}\n,"y":2}\n',
    b'{"x":\xff}\n', b'{"x":-NaN}\n',
])
def test_decoder_keeps_rejecting_invalid_per_line_json(decoder, payload):
    with pytest.raises((ValueError, UnicodeError)):
        [json.loads(line) for line in payload.splitlines()]
    with pytest.raises((ValueError, UnicodeError)):
        decode(decoder, payload)


@pytest.mark.parametrize("payload", [b'[]\n', b'null\n', b'1\n', b'{}\n[]\n'])
def test_decoder_keeps_point_object_requirement(decoder, payload):
    with pytest.raises(ExportArtifactError):
        decode(decoder, payload)


def test_selective_copy_isolates_every_nested_value_and_top_level(decoder):
    payload = b'{"scalar":"x","a":{"values":[1,{"deep":2}]},"b":[{"n":3}]}\n'
    original = json.loads(payload)
    first = decode(decoder, payload)[0]
    first["scalar"] = "changed"
    first["a"]["values"][1]["deep"] = 99
    first["b"][0]["n"] = 99
    again = decode(decoder, payload)[0]
    assert again == original
    again["a"]["values"].append("new")
    assert decode(decoder, payload)[0] == original


@pytest.mark.parametrize("limit,digits", [(640, 1000), (0, 5000)])
def test_decoder_obeys_configured_python_integer_policy(decoder, limit, digits):
    previous = sys.get_int_max_str_digits()
    payload = b'{"x":' + b'1' * digits + b'}\n'
    try:
        sys.set_int_max_str_digits(limit)
        if limit:
            with pytest.raises(ValueError):
                decode(decoder, payload)
        else:
            assert decode(decoder, payload)[0]["x"] == json.loads(payload)["x"]
    finally:
        sys.set_int_max_str_digits(previous)


def test_full_5000_point_chunk_remains_independent_on_warm_reads(decoder):
    payload = b"".join(
        json.dumps({"n":i,"observation_evidence":{"raw":[i]}},
                   separators=(",", ":")).encode() + b"\n" for i in range(5000)
    )
    rows = decode(decoder, payload)
    rows[4096]["observation_evidence"]["raw"].append("changed")
    repeated = decode(decoder, payload)
    assert len(repeated) == 5000
    assert repeated[4096]["observation_evidence"] == {"raw": [4096]}
