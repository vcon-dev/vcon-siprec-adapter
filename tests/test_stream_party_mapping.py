"""CON-705: participant/stream correlation must follow rs-metadata, not order.

RFC 7865 correlates a media stream to its participant explicitly:

    a=label:N -> <stream><label>N</label> -> stream_id
              -> <participantstreamassoc><send> -> participant_id

Mapping by list position instead happens to agree with NetSapiens, whose
streams come in participant order. The fixtures here deliberately do not,
so a positional implementation fails them and swaps the two parties' audio.

Metadata below is the NetSapiens extension 1.1 payload from the 2026-07-29
interop, trimmed to the correlation elements.
"""
import tempfile
from pathlib import Path

from siprec_srs.siprec_parser import SIPRECParser
from siprec_srs.vcon_converter import VConConverter

from tests.test_vcon_converter import (
    _make_wav, _rtp_handler_with_audio, _session_data)

DIN = "b11bd6bad68bb9e3dd2e2d727d39526e"
BOBA = "20260729164412058133-0018491486ec5db64acd5aca455acfe8"
STREAM_A = "yxaNiMQ8i2FwM3IL009291"   # label 1, sent by Din
STREAM_B = "lbsSnPDOa4Rqinsp009292"   # label 2, sent by Boba


def _metadata(participants_first_din=True, associations=True):
    """Real 1.1 correlation block; participant order is the variable."""
    din = (f'<participant participant_id="{DIN}">'
           f'<nameID aor="sip:1001@dwang.netsapiens.com"><name>Din Djarin</name>'
           f'</nameID></participant>')
    boba = (f'<participant participant_id="{BOBA}">'
            f'<nameID aor="sip:1002@dwang.netsapiens.com"><name>Boba Fett</name>'
            f'</nameID></participant>')
    order = (din + boba) if participants_first_din else (boba + din)
    assoc = (
        f'<participantstreamassoc participant_id="{DIN}">'
        f'<send>{STREAM_A}</send><recv>{STREAM_B}</recv></participantstreamassoc>'
        f'<participantstreamassoc participant_id="{BOBA}">'
        f'<send>{STREAM_B}</send><recv>{STREAM_A}</recv></participantstreamassoc>'
    ) if associations else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<recording xmlns="urn:ietf:params:xml:ns:recording:1">'
        '<datamode>complete</datamode>'
        '<session session_id="20260729164413058137"/>'
        + order +
        f'<stream stream_id="{STREAM_A}" session_id="20260729164413058137">'
        f'<label>1</label></stream>'
        f'<stream stream_id="{STREAM_B}" session_id="20260729164413058137">'
        f'<label>2</label></stream>'
        + assoc +
        '</recording>'
    )


def test_parse_stream_labels_real_metadata():
    labels = SIPRECParser().parse_stream_labels(_metadata())
    assert labels == {"1": DIN, "2": BOBA}


def test_no_associations_returns_empty():
    assert SIPRECParser().parse_stream_labels(_metadata(associations=False)) == {}


def test_contradictory_associations_refuse_to_guess():
    """Both participants sending the same stream is not a mapping."""
    xml = _metadata().replace(f"<send>{STREAM_B}</send>", f"<send>{STREAM_A}</send>")
    assert SIPRECParser().parse_stream_labels(xml) == {}


def _mapped_parties(participants_first_din):
    """Run the converter and return {capture stream_id: dialog parties}."""
    labels = SIPRECParser().parse_stream_labels(
        _metadata(participants_first_din=participants_first_din))
    # parties[] order follows the <participant> order in the metadata.
    din = {"id": DIN, "name": "Din Djarin", "tel": "8587645200"}
    boba = {"id": BOBA, "name": "Boba Fett", "tel": "8587645201"}
    participants = [din, boba] if participants_first_din else [boba, din]

    with tempfile.TemporaryDirectory() as tmp:
        # Capture stream ids sort in SDP m-line order: _stream_0 carries
        # a=label:1, _stream_1 carries a=label:2.
        files = {}
        for idx in (0, 1):
            path = Path(tmp) / f"s_stream_{idx}.wav"
            _make_wav(path, 320)
            files[f"s_stream_{idx}"] = str(path)

        session = _session_data(
            participants=participants,
            stream_labels=labels,
            media_streams=[
                {"index": 0, "stream_id": "s_stream_0", "label": "1",
                 "type": "audio", "codec": "PCMU"},
                {"index": 1, "stream_id": "s_stream_1", "label": "2",
                 "type": "audio", "codec": "PCMU"},
            ],
        )
        vcon = VConConverter().convert_session_to_vcon(
            session, _rtp_handler_with_audio(files))

    assert vcon is not None
    names = [p.get("name") for p in vcon.parties]
    recordings = [d for d in vcon.dialog if d.get("type") == "recording"]
    assert len(recordings) == 2
    return names, [d["parties"] for d in recordings]


def test_stream_order_matching_participant_order():
    names, parties = _mapped_parties(participants_first_din=True)
    assert names == ["Din Djarin", "Boba Fett"]
    # label 1 is Din's audio, at party index 0.
    assert parties == [[0], [1]]


def test_participant_order_reversed_relative_to_streams():
    """The case positional mapping gets wrong.

    Streams still arrive label 1 then label 2, but Boba is listed first, so
    Din is party 1. Stream 0 (label 1) is Din's audio and must map to party
    1, not to party 0 by position.
    """
    names, parties = _mapped_parties(participants_first_din=False)
    assert names == ["Boba Fett", "Din Djarin"]
    assert parties == [[1], [0]], (
        "stream label 1 belongs to Din (party 1); positional mapping would "
        "attribute it to Boba (party 0)")


if __name__ == "__main__":
    test_parse_stream_labels_real_metadata()
    test_no_associations_returns_empty()
    test_contradictory_associations_refuse_to_guess()
    test_stream_order_matching_participant_order()
    test_participant_order_reversed_relative_to_streams()
    print("ok")
