#!/usr/bin/env python3
"""Compare offered vs answered media streams for a captured SIPREC session.

Reads the `purpose: siprec_wire` attachment a vCon already carries (offer SDP,
answer SDP, rs-metadata) and prints one line per `m=audio` on each side plus
the rs-metadata's declared streams and participants. Use it to answer "how many
streams did the barge declare, and did we bind one recorder per stream?" without
going back to the wire.

    python3 tools_check.py vcons/01a016ff-....json
    python3 tools_check.py -    # vCon JSON on stdin
"""

import json
import re
import sys
from collections import Counter


def media_lines(sdp):
    """[(index, port, payload_types, label)] for each m=audio in an SDP."""
    out = []
    idx = -1
    for line in (sdp or "").splitlines():
        line = line.strip()
        if line.startswith("m=audio"):
            idx += 1
            parts = line.split()
            out.append({"index": idx, "port": parts[1] if len(parts) > 1 else "?",
                        "pt": " ".join(parts[3:]), "label": None})
        elif line.startswith("a=label:") and out:
            out[-1]["label"] = line.split(":", 1)[1]
    return out


def rs_streams(rs_text):
    """Stream and participant ids declared in the rs-metadata XML."""
    rs_text = rs_text or ""
    return {
        "streams": re.findall(r'<stream[^>]*\bstream_id="([^"]+)"', rs_text),
        "participants": re.findall(r'<participant[^>]*\bparticipant_id="([^"]+)"', rs_text),
        "sendonly": len(re.findall(r"<send>", rs_text)),
    }


def wire_of(vcon):
    for att in vcon.get("attachments", []):
        if att.get("purpose") == "siprec_wire":
            body = att.get("body")
            return json.loads(body) if isinstance(body, str) else (body or {})
    return {}


def main(path):
    raw = sys.stdin.read() if path == "-" else open(path).read()
    vcon = json.loads(raw)
    wire = wire_of(vcon)
    if not wire:
        sys.exit("no siprec_wire attachment on this vCon (pre-50c242c capture?)")

    offer = media_lines(wire.get("offer_sdp"))
    answer = media_lines(wire.get("answer_sdp"))
    rs = rs_streams(wire.get("rs_metadata"))

    print(f"vcon      {vcon.get('uuid')}")
    print(f"parties   {len(vcon.get('parties', []))}")
    print(f"dialogs   {len(vcon.get('dialog', []))}")
    print(f"rs-meta   {len(rs['streams'])} stream(s), "
          f"{len(rs['participants'])} participant(s)")
    print()

    print(f"OFFER  {len(offer)} m=audio")
    for m in offer:
        print(f"  [{m['index']}] port={m['port']:<6} label={m['label']!s:<12} pt={m['pt']}")
    print(f"ANSWER {len(answer)} m=audio")
    for m in answer:
        print(f"  [{m['index']}] port={m['port']:<6} label={m['label']!s:<12} pt={m['pt']}")
    print()

    problems = []
    if len(offer) != len(answer):
        problems.append(f"stream count mismatch: offered {len(offer)}, answered {len(answer)}"
                        " -- an unanswered stream has no port, so the SRC may send it"
                        " to a port we advertised for another stream")
    if rs["streams"] and len(rs["streams"]) != len(offer):
        problems.append(f"rs-metadata declares {len(rs['streams'])} stream(s) but the SDP"
                        f" offers {len(offer)} m=audio")
    dup_ports = [p for p, n in Counter(m["port"] for m in answer).items() if n > 1]
    if dup_ports:
        problems.append(f"answer advertises a port more than once: {dup_ports}"
                        " -- two streams would land in one recorder")
    dup_labels = [l for l, n in Counter(m["label"] for m in answer).items()
                  if l is not None and n > 1]
    if dup_labels:
        problems.append(f"answer repeats a=label: {dup_labels}")
    unlabelled = [m["index"] for m in offer if not m["label"]]
    if unlabelled and len(offer) > 1:
        problems.append(f"offer streams {unlabelled} carry no a=label, so party"
                        " attribution falls back to list position")

    if problems:
        print("PROBLEMS")
        for p in problems:
            print(f"  - {p}")
    else:
        print("OK: offered and answered streams line up, ports and labels unique")
    return 1 if problems else 0


def demo():
    """Self-check: the 4-party barge shape must be flagged, a clean 2-party must not."""
    clean = "m=audio 10000 RTP/AVP 0\na=label:1\nm=audio 10002 RTP/AVP 0\na=label:2\n"
    assert len(media_lines(clean)) == 2
    assert [m["label"] for m in media_lines(clean)] == ["1", "2"]
    assert media_lines(clean)[1]["port"] == "10002"

    # five offered, four answered: the shape that sent a fifth source into 10004
    offer5 = "".join(f"m=audio {9000+2*i} RTP/AVP 0\na=label:{i+1}\n" for i in range(5))
    answer4 = "".join(f"m=audio {10000+2*i} RTP/AVP 0\na=label:{i+1}\n" for i in range(4))
    assert len(media_lines(offer5)) == 5 and len(media_lines(answer4)) == 4

    dup = "m=audio 10004 RTP/AVP 0\na=label:1\nm=audio 10004 RTP/AVP 0\na=label:2\n"
    ports = [m["port"] for m in media_lines(dup)]
    assert ports == ["10004", "10004"], ports

    rs = rs_streams('<stream stream_id="a" /><stream stream_id="b" />'
                    '<participant participant_id="p1" />')
    assert rs["streams"] == ["a", "b"] and rs["participants"] == ["p1"]

    assert media_lines("")==[] and rs_streams(None)["streams"]==[]
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    elif len(sys.argv) != 2:
        sys.exit(__doc__)
    else:
        sys.exit(main(sys.argv[1]))
