"""CON-708 step 1: retain the keys that let a transfer's vCons be stitched.

NetSapiens closes a SIPREC session and opens a new one whenever the parties
change, so an attended transfer arrives as three separate dialogs and becomes
three separate vCons. The only thing relating them is rs-metadata: `group_id`,
the vendor `groupSeq`, and the transfer references.

`group_id` lives in the RFC 7865 `<group>` element, outside the vendor
extension, so before this change it was parsed by nothing and the sibling
relationship was unrecoverable from the output.

This does not decide whether the three should later be assembled into one
vCon. It makes that decidable, and makes tomorrow's live evidence complete.

Fixtures are David Wang's real 2026-07-29 attended transfer.
"""
import json

from siprec_srs.siprec_parser import SIPRECParser
from siprec_srs.vcon_converter import VConConverter

from tests.fixtures.netsapiens_attended_transfer_11 import (
    INITIAL, CONSULTATION, POST_TRANSFER, ORIGINAL_GROUP, CONSULT_GROUP)
from tests.test_vcon_converter import (
    _make_wav, _rtp_handler_with_audio, _session_data)


def _keys(xml):
    return SIPRECParser().parse_session_keys(xml)


def test_group_and_session_keys_parsed():
    initial, consult, post = _keys(INITIAL), _keys(CONSULTATION), _keys(POST_TRANSFER)

    assert initial["group_id"] == ORIGINAL_GROUP
    assert initial["session_id"] == "20260729201721000631"
    assert initial["group_ref"] == ORIGINAL_GROUP
    assert initial["associate_time"] == "2026-07-29T20:17:21Z"

    # The consultation is its own group, and its group_id is a SIP Call-ID
    # rather than a hex digest. Treated as an opaque string.
    assert consult["group_id"] == CONSULT_GROUP
    assert "@" in consult["group_id"]

    # The post-transfer session rejoins the ORIGINAL group. This is the whole
    # point: without group_id there is nothing linking it to the initial call.
    assert post["group_id"] == ORIGINAL_GROUP
    assert post["session_id"] == "20260729201748000671"
    assert post["group_id"] == initial["group_id"] != consult["group_id"]


def test_group_sequence_orders_sessions_within_a_group():
    """groupSeq, not wall clock, is the ordering key inside a group."""
    p = SIPRECParser()
    assert p.parse_vendor_extension(INITIAL)["groupSeq"] == "0"
    assert p.parse_vendor_extension(POST_TRANSFER)["groupSeq"] == "1"
    # The consultation restarts at 0 because it is a different group.
    assert p.parse_vendor_extension(CONSULTATION)["groupSeq"] == "0"


def test_transfer_reference_points_at_the_consultation_group():
    ext = SIPRECParser().parse_vendor_extension(POST_TRANSFER)
    assert ext["byAction"] == "XferSup"
    assert ext["byUserID"] == "1002@dwang.netsapiens.com"
    assert ext["byAor"] == "sip:1002@dwang.netsapiens.com"
    assert ext["xferredGroupID"] == CONSULT_GROUP
    # NOTE: xferredSessionID carries the same value as xferredGroupID, i.e. the
    # consultation's GROUP id, not its session_id ("20260729201741000664").
    # Pinned as observed behavior, not endorsed. Raised with David 2026-07-29.
    assert ext["xferredSessionID"] == ext["xferredGroupID"]


def test_src_stream_ids_are_reused_across_the_transfer():
    """The SRC's stream_id is stable per media leg, so it correlates audio.

    Our own stream_id is per-session and cannot express this.
    """
    initial, consult, post = _keys(INITIAL), _keys(CONSULTATION), _keys(POST_TRANSFER)

    # Post-transfer label 1 is the original call's stream (1001's audio)...
    assert post["stream_ids"]["1"] == initial["stream_ids"]["1"]
    # ...and label 2 is the consultation's stream (1006's audio).
    assert post["stream_ids"]["2"] == consult["stream_ids"]["2"]


def _vcon_for(xml, group_hint):
    """Convert one leg of the transfer to a vCon, as the live path would."""
    p = SIPRECParser()
    rs_keys = p.parse_session_keys(xml)
    participants = p.parse_rs_metadata(xml)
    labels = p.parse_stream_labels(xml)
    vendor = p.parse_vendor_extension(xml)

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        streams = []
        for idx, label in enumerate(("1", "2")):
            sid = f"{group_hint}_stream_{idx}"
            path = Path(tmp) / f"{sid}.wav"
            _make_wav(path, 320)
            files[sid] = str(path)
            streams.append({"index": idx, "stream_id": sid, "label": label,
                            "type": "audio", "codec": "PCMU"})
        session = _session_data(
            participants=participants, stream_labels=labels,
            media_streams=streams, rs_keys=rs_keys, vendor_extension=vendor,
            recording_session_id=rs_keys.get("session_id", ""))
        return VConConverter().convert_session_to_vcon(
            session, _rtp_handler_with_audio(files))


def _attachment(vcon, purpose):
    for a in vcon.vcon_dict.get("attachments", []):
        if a.get("purpose") == purpose:
            return json.loads(a["body"])
    raise AssertionError(f"no {purpose} attachment")


def test_three_vcons_carry_everything_needed_to_stitch_them():
    a = _vcon_for(INITIAL, "a")
    b = _vcon_for(CONSULTATION, "b")
    c = _vcon_for(POST_TRANSFER, "c")

    tags = [_attachment(v, "tags") for v in (a, b, c)]

    # Siblings are findable: A and C share a group, B does not.
    assert tags[0]["rs_group_id"] == tags[2]["rs_group_id"] == ORIGINAL_GROUP
    assert tags[1]["rs_group_id"] == CONSULT_GROUP

    # Order within the group is recoverable.
    assert tags[0]["group_seq"] == "0"
    assert tags[2]["group_seq"] == "1"

    # The transfer itself is recoverable, including which group was merged in.
    assert tags[2]["by_action"] == "XferSup"
    assert tags[2]["xferred_group_id"] == CONSULT_GROUP
    # And that names B's group, so the consultation record is reachable.
    assert tags[2]["xferred_group_id"] == tags[1]["rs_group_id"]

    # A leg with no transfer does not carry an empty xferred key.
    assert "xferred_group_id" not in tags[0]

    # Their session id, not our Call-ID, is the quotable correlation key.
    meta_c = _attachment(c, "session_metadata")
    assert meta_c["rs_metadata_keys"]["session_id"] == "20260729201748000671"
    assert meta_c["recording_session_id"] == "20260729201748000671"


def test_dialog_provenance_carries_the_src_stream_id():
    """Audio continuity across the transfer, per dialog."""
    a = _vcon_for(INITIAL, "a")
    c = _vcon_for(POST_TRANSFER, "c")

    def provenances(vcon):
        return [json.loads(x["body"])
                for x in vcon.vcon_dict["attachments"]
                if x.get("purpose") == "stream_provenance"]

    pa, pc = provenances(a), provenances(c)
    assert len(pa) == 2 and len(pc) == 2

    by_label_a = {x["label"]: x["rs_stream_id"] for x in pa}
    by_label_c = {x["label"]: x["rs_stream_id"] for x in pc}

    # 1001's audio is the same media leg before and after the transfer.
    assert by_label_a["1"] == by_label_c["1"]
    # 1002's leg does not survive; label 2 post-transfer is a different stream.
    assert by_label_a["2"] != by_label_c["2"]


if __name__ == "__main__":
    test_group_and_session_keys_parsed()
    test_group_sequence_orders_sessions_within_a_group()
    test_transfer_reference_points_at_the_consultation_group()
    test_src_stream_ids_are_reused_across_the_transfer()
    test_three_vcons_carry_everything_needed_to_stitch_them()
    test_dialog_provenance_carries_the_src_stream_id()
    print("ok")
