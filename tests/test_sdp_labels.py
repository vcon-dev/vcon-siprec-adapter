"""RFC 7866 5.2: a=label must be parsed from the offer and echoed in the answer.

Regression test for the 2026-07-29 NetSapiens interop failure: without the
labels in our 200 OK, their SRS client could not correlate streams and never
sourced RTP.
"""
from siprec_srs.siprec_parser import SIPRECParser
from siprec_srs.sip_server import SIPRECServer

# Verbatim offer from the NetSapiens (metadata ext 1.1) INVITE.
OFFER = (
    "v=0\r\n"
    "o=NetSapiens_Nms 1785336857 1785336857034 IN IP4 132.226.155.215\r\n"
    "s=SIP Media Description\r\n"
    "c=IN IP4 132.226.155.215\r\n"
    "t=0 0\r\n"
    "m=audio 16958 RTP/AVP 0 8 127\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=rtpmap:8 PCMA/8000\r\n"
    "a=rtpmap:127 telephone-event/8000\r\n"
    "a=label:1\r\n"
    "m=audio 16956 RTP/AVP 0 127\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=rtpmap:127 telephone-event/8000\r\n"
    "a=label:2\r\n"
)


def test_labels_round_trip():
    streams = SIPRECParser().parse_sdp(OFFER)
    assert [s["label"] for s in streams] == ["1", "2"]

    answer = SIPRECServer._build_sdp_answer(
        None, "138.197.42.97", [(streams[0], 10000), (streams[1], 10002)])
    assert "a=label:1" in answer and "a=label:2" in answer
    # Label belongs to its own m-line, not the session block.
    first, second = answer.split("m=audio")[1:]
    assert "a=label:1" in first and "a=label:2" in second


def test_missing_label_omits_attribute():
    no_label = OFFER.replace("a=label:1\r\n", "").replace("a=label:2\r\n", "")
    streams = SIPRECParser().parse_sdp(no_label)
    answer = SIPRECServer._build_sdp_answer(None, "1.2.3.4",
                                            [(s, 10000) for s in streams])
    assert "a=label" not in answer


if __name__ == "__main__":
    test_labels_round_trip()
    test_missing_label_omits_attribute()
    print("ok")
