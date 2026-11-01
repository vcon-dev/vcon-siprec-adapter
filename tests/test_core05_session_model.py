"""core-05 (syntax 0.5.0) shape of converter output: Session, Events, scoping."""

import tempfile
from pathlib import Path

import jsonschema
import json

from siprec_srs.config import LawfulBasisConfig
from siprec_srs.siprec_parser import SIPRECParser, find_calling_participant_id
from siprec_srs.vcon_converter import VConConverter

from tests.test_vcon_converter import (
    _empty_rtp_handler,
    _make_wav,
    _rtp_handler_with_audio,
    _session_data,
)

SCHEMA = json.loads(
    (Path(__file__).parent / "schema" / "vcon_json_schema.json").read_text())


def _convert(handler=None, converter=None, **overrides):
    converter = converter or VConConverter(
        lawful_basis_config=LawfulBasisConfig(enabled=True, lawful_basis="consent"))
    return converter.convert_session_to_vcon(
        _session_data(**overrides), handler or _empty_rtp_handler())


def _two_stream_handler(tmp):
    files = {}
    for i in range(2):
        p = Path(tmp) / f"stream_{i}.wav"
        _make_wav(p, payload_bytes=8)
        files[f"stream_{i}"] = str(p)
    return _rtp_handler_with_audio(files)


def test_one_session_references_every_stream_dialog():
    with tempfile.TemporaryDirectory() as tmp:
        d = _convert(_two_stream_handler(tmp)).vcon_dict
    assert len(d["sessions"]) == 1
    s = d["sessions"][0]
    assert s["dialog"] == [0, 1]
    assert s["parties"] == [0, 1]
    assert s["start"] == "2026-05-08T12:00:00+00:00"
    assert s["duration"] == 300
    assert all(x["type"] == "recording" for x in d["dialog"])


def test_no_legacy_dialog_fields():
    with tempfile.TemporaryDirectory() as tmp:
        d = _convert(_two_stream_handler(tmp)).vcon_dict
    for dlg in d["dialog"]:
        for legacy in ("originator", "party_history", "recordings",
                       "recording_set", "session_id"):
            assert legacy not in dlg
    assert not any(x.get("type") == "recording-set" for x in d["dialog"])


def test_session_exists_even_without_audio():
    d = _convert().vcon_dict
    assert len(d["sessions"]) == 1
    assert "dialog" not in d["sessions"][0]


def test_originator_absent_when_unknown():
    d = _convert().vcon_dict
    assert "originator" not in d["sessions"][0]


def test_originator_set_only_when_not_first_party():
    vendor = {"callingParty": {"participant_id": "callee_1"}}
    d = _convert(vendor_extension=vendor).vcon_dict
    assert d["sessions"][0]["originator"] == 1
    vendor = {"callingParty": {"participant_id": "caller_1"}}
    d = _convert(vendor_extension=vendor).vcon_dict
    assert "originator" not in d["sessions"][0]


def test_find_calling_participant_id_nested_and_missing():
    assert find_calling_participant_id(
        {"a": [{"callingParty": {"participant_id": "x"}}]}) == "x"
    assert find_calling_participant_id({"calledParty": {"participant_id": "y"}}) is None
    assert find_calling_participant_id({}) is None


def test_join_and_drop_events_from_metadata():
    participants = [
        {'id': 'caller_1', 'name': 'A', 'tel': '+1234567890',
         'associate_time': '2026-05-08T12:00:01+00:00',
         'disassociate_time': '2026-05-08T12:04:59+00:00'},
        {'id': 'callee_1', 'name': 'B', 'tel': '+1987654321',
         'associate_time': '2026-05-08T12:00:03+00:00'},
    ]
    d = _convert(participants=participants).vcon_dict
    assert d["events"] == [
        {"type": "join", "time": "2026-05-08T12:00:01+00:00", "sessions": 0, "party": 0},
        {"type": "drop", "time": "2026-05-08T12:04:59+00:00", "sessions": 0, "party": 0},
        {"type": "join", "time": "2026-05-08T12:00:03+00:00", "sessions": 0, "party": 1},
    ]
    assert all("dialog" not in e for e in d["events"])
    jsonschema.validate(instance=d, schema=SCHEMA)


def test_no_events_without_times():
    d = _convert().vcon_dict
    assert "events" not in d or d["events"] == []


def test_parser_reads_participantsessionassoc():
    xml = (
        '<recording xmlns="urn:ietf:params:xml:ns:recording:1">'
        '<session session_id="s1"/>'
        '<participant participant_id="p1"><nameID aor="sip:a@x.example"><name>A</name></nameID></participant>'
        '<participant participant_id="p2"><nameID aor="sip:b@x.example"><name>B</name></nameID></participant>'
        '<participantsessionassoc participant_id="p1" session_id="s1">'
        '<associate-time>2026-05-08T12:00:01Z</associate-time>'
        '<disassociate-time>2026-05-08T12:03:00Z</disassociate-time>'
        '</participantsessionassoc>'
        '</recording>')
    parts = SIPRECParser().parse_rs_metadata(xml)
    assert parts[0]["associate_time"] == "2026-05-08T12:00:01Z"
    assert parts[0]["disassociate_time"] == "2026-05-08T12:03:00Z"
    assert "associate_time" not in parts[1]


def test_attachments_are_session_scoped_except_stream_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        d = _convert(_two_stream_handler(tmp)).vcon_dict
    for a in d["attachments"]:
        assert "party" in a
        if a["purpose"] == "stream_provenance":
            assert a["dialog"] in (0, 1)
        else:
            assert a["session"] == 0, a["purpose"]


def test_lawful_basis_scoped_party_plus_session_and_declared():
    d = _convert().vcon_dict
    lb = [a for a in d["attachments"] if a["purpose"] == "lawful_basis"]
    assert len(lb) == 1
    assert (lb[0]["party"], lb[0]["session"]) == (0, 0)
    assert "dialog" not in lb[0] and "type" not in lb[0]
    assert "lawful_basis" in d["extensions"]


def test_lawful_basis_not_invented_when_unconfigured():
    d = _convert(converter=VConConverter()).vcon_dict
    assert not any(a["purpose"] == "lawful_basis" for a in d["attachments"])


def test_full_output_validates_against_appendix_c():
    with tempfile.TemporaryDirectory() as tmp:
        d = _convert(_two_stream_handler(tmp)).vcon_dict
    jsonschema.validate(instance=d, schema=SCHEMA)


def test_vendored_schema_is_core05_appendix_c():
    assert "recording_set" not in json.dumps(SCHEMA)
    assert "Session" in SCHEMA["definitions"]


def test_originator_from_real_netsapiens_metadata():
    from tests.test_netsapiens_metadata import CALL_11
    parser = SIPRECParser()
    vendor = parser.parse_vendor_extension(CALL_11)
    assert find_calling_participant_id(vendor) == "PID1"
