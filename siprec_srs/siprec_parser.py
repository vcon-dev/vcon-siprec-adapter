"""
SIPREC INVITE parsing: SDP media description + rs-metadata (RFC 7865).

Operates on the raw INVITE body (a `multipart/mixed` of `application/sdp` and
`application/rs-metadata+xml`), which is where SIPREC actually carries its
data. This replaces the earlier pjsua2-CallInfo approach, which could not
reach the body at all.
"""

import logging
import re
import uuid as _uuid
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Fixed namespace for deriving a stable Party `uuid` from a provider's own
# subscriber id (NetSapiens `uid`). Any constant UUID works as a uuid5
# namespace; this one is arbitrary and permanent, so the same uid always maps
# to the same UUID across calls and across vCons -- which is the whole point of
# the core Party `uuid` field (cross-vCon identity).
_PARTY_UUID_NS = _uuid.UUID("a7c9e5d2-1b3f-4e6a-8c0d-9f2e4b6a8c1d")

# RTP static payload type -> codec name (RFC 3551), for labelling.
_STATIC_PT = {0: "PCMU", 8: "PCMA", 9: "G722"}


def split_multipart(body: bytes, content_type: str) -> Dict[str, bytes]:
    """Split a multipart/mixed body into {subtype: part_body}.

    Keys are the lowercased MIME subtype without params, e.g. "sdp",
    "rs-metadata+xml". A non-multipart body is returned under a best-effort
    key derived from `content_type`.
    """
    m = re.search(r'boundary="?([^";]+)"?', content_type, re.IGNORECASE)
    if not m:
        subtype = _subtype(content_type)
        return {subtype: body} if body else {}

    boundary = ("--" + m.group(1)).encode()
    parts: Dict[str, bytes] = {}
    for chunk in body.split(boundary):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        # Split part headers from part body on the first blank line.
        if b"\r\n\r\n" in chunk:
            head, part_body = chunk.split(b"\r\n\r\n", 1)
        elif b"\n\n" in chunk:
            head, part_body = chunk.split(b"\n\n", 1)
        else:
            continue
        ctype = ""
        for line in head.decode("utf-8", "replace").splitlines():
            if line.lower().startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
                break
        parts[_subtype(ctype)] = part_body.strip(b"\r\n")
    return parts


def _subtype(content_type: str) -> str:
    """`application/rs-metadata+xml; charset=..` -> `rs-metadata+xml`."""
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    return ct.split("/", 1)[1] if "/" in ct else ct


def _localname(tag: str) -> str:
    """Strip an XML namespace: `{urn:...}participant` -> `participant`."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _is_phone_number(s: str) -> bool:
    """True for something dialable outside the PBX, not an extension.

    `+15085551234` and `8587645225` qualify; `1003` does not. Seven digits
    is the shortest real subscriber number (NANP local), so anything under
    that is treated as an internal extension.
    """
    if not s:
        return False
    digits = re.sub(r"[^\d]", "", s)
    if not re.fullmatch(r"\+?[\d\-().\s]+", s):
        return False
    return s.startswith("+") or len(digits) >= 7


def _to_e164(number: str) -> str:
    """Best-effort E.164 for a NANP number; pass anything else through.

    `8587641002` -> `+18587641002`, `18587641002` -> `+18587641002`, an
    already-`+` number is left alone. A value that is neither a clean 10/11-digit
    NANP number nor `+`-prefixed is returned unchanged rather than guessed at.
    """
    if not number:
        return ""
    s = number.strip()
    if s.startswith("+"):
        return s
    digits = re.sub(r"[^\d]", "", s)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return s


def enrich_participants_from_vendor(
    participants: List[Dict[str, Any]], vendor: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Fold NetSapiens per-party extension fields onto the RFC 7865 parties.

    Closes the gap David Wang raised (2026-09-06): the standard `<participant>`
    block carries only name + AOR, so the real dialable number, the cross-domain
    subscriber id, and a formal home for the AOR never reach the Party object.
    NetSapiens 1.1 adds `calledParty` / `callingParty` elements keyed by the same
    `participant_id`, carrying:

      * `number`     -> `tel`, normalised to E.164 (only if no tel yet)
      * nameID `uid` -> `did` and a derived, stable core `uuid`
      * nameID `aor` -> `uri` (mapped to the core `sip` field downstream)

    1.0 payloads carry flat `calledPartyNumber` with no participant_id
    association, so nothing is folded and tel stays empty rather than being
    attributed to the wrong party. Mutates and returns the participant list.
    """
    index: Dict[str, Dict[str, str]] = {}
    _collect_vendor_parties(vendor, index)
    for p in participants:
        v = index.get(p.get("id"))
        if not v:
            continue
        if v.get("number") and not p.get("tel"):
            p["tel"] = _to_e164(v["number"])
        if v.get("uid"):
            p["did"] = v["uid"]
            p.setdefault("uuid", str(_uuid.uuid5(_PARTY_UUID_NS, v["uid"])))
        if v.get("aor") and not p.get("uri"):
            p["uri"] = v["aor"]
    return participants


def _collect_vendor_parties(node: Any, out: Dict[str, Dict[str, str]]) -> None:
    """Index every vendor node carrying a `participant_id` by that id.

    Schema-agnostic on purpose, matching parse_vendor_extension: NetSapiens can
    nest `calledParty` / `callingParty` wherever it likes across 1.0/1.1, so the
    shape (a dict with a participant_id) is matched, not a fixed path. Collects
    `number` and the nameID's `uid` / `aor`.
    """
    if isinstance(node, list):
        for item in node:
            _collect_vendor_parties(item, out)
        return
    if not isinstance(node, dict):
        return
    pid = node.get("participant_id")
    if pid:
        name_id = node.get("nameID")
        if isinstance(name_id, list):
            name_id = name_id[0] if name_id else {}
        if not isinstance(name_id, dict):
            name_id = {}
        entry = out.setdefault(pid, {})
        for key, raw in (("number", node.get("number")),
                         ("uid", name_id.get("uid")),
                         ("aor", name_id.get("aor"))):
            val = raw.get("_text", "") if isinstance(raw, dict) else raw
            if val and not entry.get(key):
                entry[key] = val
    for child in node.values():
        _collect_vendor_parties(child, out)


class SIPRECParser:
    """Parse SDP and rs-metadata out of a SIPREC INVITE."""

    def parse_sdp(self, sdp: str) -> List[Dict[str, Any]]:
        """Return one dict per m=audio line: index, port, connection, codecs."""
        streams: List[Dict[str, Any]] = []
        session_conn = None
        current: Optional[Dict[str, Any]] = None

        for raw in sdp.replace("\r\n", "\n").split("\n"):
            line = raw.strip()
            if not line or "=" not in line:
                continue
            typ, val = line.split("=", 1)
            if typ == "c" and current is None:
                session_conn = self._conn_addr(val)
            elif typ == "m":
                fields = val.split()
                if len(fields) >= 4 and fields[0] == "audio":
                    current = {
                        "index": len(streams),
                        "type": "audio",
                        "remote_port": int(fields[1]) if fields[1].isdigit() else 0,
                        "profile": fields[2],  # RTP/AVP or RTP/SAVP (SRTP)
                        "connection": session_conn,
                        "payload_types": [int(p) for p in fields[3:] if p.isdigit()],
                        "rtpmap": {},
                        "label": None,
                        "crypto": [],  # RFC 4568 a=crypto offers, in order
                    }
                    streams.append(current)
                else:
                    current = None  # ignore non-audio media
            elif typ == "c" and current is not None:
                current["connection"] = self._conn_addr(val)
            elif typ == "a" and current is not None:
                rm = re.match(r"rtpmap:(\d+)\s+([^/]+)/(\d+)", val)
                if rm:
                    current["rtpmap"][int(rm.group(1))] = {
                        "name": rm.group(2), "rate": int(rm.group(3))
                    }
                elif val.startswith("label:"):
                    # RFC 7866 5.2: the label ties this m-line to a <stream>
                    # in the rs-metadata, and MUST be echoed in our answer.
                    current["label"] = val[6:].strip()
                elif val.startswith("crypto:"):
                    crypto = self._parse_crypto(val[7:])
                    if crypto:
                        current["crypto"].append(crypto)

        for s in streams:
            s["codec"] = self._primary_codec(s)
        return streams

    def _parse_crypto(self, val: str) -> Optional[Dict[str, Any]]:
        """Parse an RFC 4568 SDES crypto attribute (after "crypto:").

        `1 AES_CM_128_HMAC_SHA1_80 inline:<b64key>|2^20|1:4 [params]`
        Returns tag, suite, key (base64 of master key||salt), lifetime, mki,
        or None if the line is not an inline key we could use.
        """
        fields = val.split()
        if len(fields) < 3 or not fields[0].isdigit():
            return None
        tag, suite, key_params = int(fields[0]), fields[1], fields[2]
        if not key_params.startswith("inline:"):
            return None  # only inline keys exist in practice
        key, *extra = key_params[7:].split("|")
        lifetime = mki = None
        for part in extra:
            if ":" in part:
                idx, length = part.split(":", 1)
                if idx.isdigit() and length.isdigit():
                    mki = (int(idx), int(length))
            else:
                lifetime = part
        return {"tag": tag, "suite": suite, "key": key,
                "lifetime": lifetime, "mki": mki,
                "params": fields[3:]}

    def _conn_addr(self, val: str) -> Optional[str]:
        # c=IN IP4 1.2.3.4
        parts = val.split()
        return parts[2] if len(parts) >= 3 else None

    def _primary_codec(self, stream: Dict[str, Any]) -> str:
        for pt in stream["payload_types"]:
            if pt in stream["rtpmap"]:
                return stream["rtpmap"][pt]["name"].upper()
            if pt in _STATIC_PT:
                return _STATIC_PT[pt]
        return "PCMU"

    def parse_rs_metadata(self, xml_text: str) -> List[Dict[str, Any]]:
        """Parse RFC 7865 recording metadata into participant dicts."""
        participants: List[Dict[str, Any]] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("rs-metadata XML parse failed: %s", e)
            return participants

        for part in root.iter():
            if _localname(part.tag) != "participant":
                continue
            pid = part.get("participant_id") or part.get("id") or str(len(participants))
            name, aor = "", ""
            for child in part.iter():
                tag = _localname(child.tag)
                if tag == "nameID" and not aor:
                    aor = child.get("aor", "")
                elif tag == "name" and not name and (child.text or "").strip():
                    name = child.text.strip()
            participants.append(self._participant_from_aor(pid, name, aor))
        return participants

    def parse_stream_labels(self, xml_text: str) -> Dict[str, str]:
        """Return {sdp_label: participant_id} from RFC 7865 associations.

        The join the spec actually defines, which nothing positional can
        stand in for:

            m= line -> a=label:N -> <stream><label>N</label> -> stream_id
            -> <participantstreamassoc><send> -> participant_id

        `<send>` is the sending participant, so the stream carrying that
        label is that participant's audio. Returns {} when the metadata
        omits the associations, which the caller must treat as "fall back".
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("rs-metadata XML parse failed: %s", e)
            return {}

        label_of_stream: Dict[str, str] = {}   # rs stream_id -> sdp label
        sends: Dict[str, List[str]] = {}       # participant_id -> stream_ids
        for el in root.iter():
            tag = _localname(el.tag)
            if tag == "stream":
                sid = el.get("stream_id")
                label = next(((c.text or "").strip() for c in el
                             if _localname(c.tag) == "label"), "")
                if sid and label:
                    label_of_stream[sid] = label
            elif tag == "participantstreamassoc":
                pid = el.get("participant_id")
                if not pid:
                    continue
                sends[pid] = [(c.text or "").strip() for c in el
                              if _localname(c.tag) == "send" and (c.text or "").strip()]

        out: Dict[str, str] = {}
        for pid, stream_ids in sends.items():
            for sid in stream_ids:
                label = label_of_stream.get(sid)
                if label is None:
                    continue
                if label in out and out[label] != pid:
                    # Two participants claim the same stream: the metadata is
                    # not a function of label -> participant, so refuse to
                    # guess rather than pick by dict order.
                    logger.warning(
                        "rs-metadata: label %s sent by both %s and %s; "
                        "dropping stream mapping", label, out[label], pid)
                    return {}
                out[label] = pid
        return out

    def parse_session_keys(self, xml_text: str) -> Dict[str, Any]:
        """Correlation keys from RFC 7865 `<group>` and `<session>`.

        These sit outside the vendor extension, so `parse_vendor_extension`
        never sees them, and they are the only thing tying a sequence of
        SIPREC sessions together. NetSapiens closes a session and opens a new
        one whenever the parties change (David Wang, 2026-07-29), so an
        attended transfer arrives as three separate dialogs sharing a
        `group_id` with an incrementing `groupSeq`. Without these keys the
        three are unrelatable.

        `stream_ids` maps sdp label -> the SRC's own `stream_id`. Those are
        reused across sessions for the same media leg, which makes them the
        audio-continuity key across a transfer.

        Every value is treated as an opaque string. Observed `group_id`
        formats include both a hex digest and a SIP Call-ID with an @host, so
        nothing here validates shape.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("rs-metadata XML parse failed: %s", e)
            return {}

        keys: Dict[str, Any] = {}
        stream_ids: Dict[str, str] = {}
        for el in root.iter():
            tag = _localname(el.tag)
            if tag == "group":
                if el.get("group_id"):
                    keys["group_id"] = el.get("group_id")
                at = next(((c.text or "").strip() for c in el
                           if _localname(c.tag) == "associate-time"), "")
                if at:
                    keys["associate_time"] = at
            elif tag == "session":
                if el.get("session_id"):
                    keys["session_id"] = el.get("session_id")
                for c in el:
                    ctag = _localname(c.tag)
                    text = (c.text or "").strip()
                    if not text:
                        continue
                    if ctag == "group-ref":
                        keys["group_ref"] = text
                    elif ctag == "sipSessionID":
                        keys["sip_session_id"] = text
            elif tag == "stream":
                sid = el.get("stream_id")
                label = next(((c.text or "").strip() for c in el
                              if _localname(c.tag) == "label"), "")
                if sid and label:
                    stream_ids[label] = sid
        if stream_ids:
            keys["stream_ids"] = stream_ids
        return keys

    def parse_vendor_extension(self, xml_text: str) -> Dict[str, Any]:
        """Capture a vendor extension block from rs-metadata, verbatim.

        RFC 7865 lets an SRC hang its own namespaced element off `recording`.
        NetSapiens uses `netsapiensExtension` (schema.netsapiens.com), which
        carries what RFC 7865 has nowhere to put: the real calling/called
        numbers, the tenant, and why the call was recorded (`byAction`,
        `byUserID` for a forward).

        Deliberately schema-agnostic: every child element and attribute is
        captured by local name, whatever the declared `version`. NetSapiens
        moved 1.0 -> 1.1 on 2026-07-25 adding fields for complex call
        scenarios, and we have not seen a 1.1 payload. Enumerating known
        fields here would silently drop whatever 1.1 added, so nothing is
        enumerated. Repeated elements (e.g. `user`) collect into a list.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("rs-metadata XML parse failed: %s", e)
            return {}

        for child in root:
            tag = _localname(child.tag)
            if not tag.lower().endswith("extension"):
                continue
            ext = self._element_to_dict(child)
            ext["_element"] = tag
            ns = child.tag.split("}", 1)[0].lstrip("{") if "}" in child.tag else ""
            if ns:
                ext["_namespace"] = ns
            return ext
        return {}

    def _element_to_dict(self, el) -> Dict[str, Any]:
        """Recursively flatten an element into attrs, text and children."""
        out: Dict[str, Any] = {}
        for k, v in el.attrib.items():
            out[_localname(k)] = v
        for child in el:
            tag = _localname(child.tag)
            grand = self._element_to_dict(child)
            text = (child.text or "").strip()
            value: Any = grand if grand else text
            if grand and text:
                value = dict(grand, _text=text)
            if tag in out:
                if not isinstance(out[tag], list):
                    out[tag] = [out[tag]]
                out[tag].append(value)
            else:
                out[tag] = value
        return out

    def _participant_from_aor(self, pid: str, name: str, aor: str) -> Dict[str, Any]:
        """Map an AOR to spec-typed party fields, keyed on the URI scheme.

        The scheme is authoritative. A `sip:` AOR is never an email address,
        however much its user@host shape resembles one, and a PBX extension
        in a `sip:` AOR is not a dialable telephone number. Getting this
        wrong puts fabricated `tel`/`mailto` values into the vCon, so when
        the scheme does not prove a type, both are left empty and the full
        AOR is preserved in `uri`.
        """
        scheme = ""
        user = aor
        for s in ("sips:", "sip:", "tel:", "mailto:"):
            if user.lower().startswith(s):
                scheme, user = s.rstrip(":"), user[len(s):]
                break
        userpart = user.split("@", 1)[0].split(";", 1)[0]

        tel, mailto = "", ""
        if scheme == "tel":
            tel = userpart
        elif scheme == "mailto":
            mailto = user.split(";", 1)[0]
        elif scheme in ("sip", "sips"):
            # Only a genuine phone number, not an internal extension. Bare
            # extensions (NetSapiens sends sip:1003@domain) have no meaning
            # outside the PBX; the real E.164 numbers arrive in the vendor
            # extension instead.
            if _is_phone_number(userpart):
                tel = userpart
        elif not scheme and _is_phone_number(userpart):
            tel = userpart

        return {
            "id": pid,
            "role": "participant",
            "uri": aor,
            "name": name or userpart,
            "tel": tel,
            "mailto": mailto,
        }
