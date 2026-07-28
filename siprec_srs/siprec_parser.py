"""
SIPREC INVITE parsing: SDP media description + rs-metadata (RFC 7865).

Operates on the raw INVITE body (a `multipart/mixed` of `application/sdp` and
`application/rs-metadata+xml`), which is where SIPREC actually carries its
data. This replaces the earlier pjsua2-CallInfo approach, which could not
reach the body at all.
"""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

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
                        "connection": session_conn,
                        "payload_types": [int(p) for p in fields[3:] if p.isdigit()],
                        "rtpmap": {},
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

        for s in streams:
            s["codec"] = self._primary_codec(s)
        return streams

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
