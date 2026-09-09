"""rs-metadata parsing against real NetSapiens payloads.

The XML below is copied verbatim from SIPREC INVITEs sent by David Wang's
NetSapiens SiPBx v45-0-0x618 during the 2026-07 interop (calls on 07-20 and
07-22), minus the Proofpoint link-rewriting the mail gateway added.

These are `netsapiensExtension version="1.0"`. NetSapiens upgraded that test
instance to 1.1 on 2026-07-25 (extra fields for complex call scenarios, and
per David "some slight different in the participant ID"), and we have not
captured a 1.1 payload yet. The point of `test_unknown_version_survives` is
that we do not have to: nothing here enumerates a 1.0 field list.
"""

import unittest
import uuid as _uuid

from siprec_srs.siprec_parser import (
    SIPRECParser,
    enrich_participants_from_vendor,
    _to_e164,
    _PARTY_UUID_NS,
)

# NetSapiens 1.1, reconstructed from David Wang's 2026-09-06 email. 1.1 adds
# per-party `calledParty` / `callingParty` blocks keyed by the same
# participant_id as the standard `<participant>`, carrying the real dialable
# `number`, a cross-domain `uid`, and the device `aor`.
CALL_11 = """<?xml version="1.0" encoding="UTF-8"?><recording xmlns="urn:ietf:params:xml:ns:recording:1"><datamode>complete</datamode><participant participant_id="PID1"><nameID aor="sip:1001@vbox.netsapiens.com"><name>Din Djarin</name></nameID></participant><participant participant_id="20260825223001002433-0018491486ec5db64acd5aca455acfe8"><nameID aor="sip:1002@vbox.netsapiens.com"><name>Boba Fett</name></nameID></participant><netsapiensExtension xmlns="http://schema.netsapiens.com/netsapiensSipRec" version="1.1"><groupSeq>0</groupSeq><callingParty participant_id="PID1"><nameID aor="sip:1001@vbox.netsapiens.com"><uid>1001@vbox.netsapiens.com</uid><name>Din Djarin</name></nameID><number>8587641001</number></callingParty><calledParty participant_id="20260825223001002433-0018491486ec5db64acd5aca455acfe8"><nameID aor="sip:1002@vbox.netsapiens.com"><uid>1002@vbox.netsapiens.com</uid><name>Boba Fett</name></nameID><number>8587641002</number></calledParty><byAction>Recording</byAction></netsapiensExtension></recording>"""

# Two-party call, 07-20. Note `sip:1001w@...` — the stray "w" is in the
# original and is exactly the shape that used to be mistaken for an email.
CALL_0720 = """<?xml version="1.0" encoding="UTF-8"?><recording xmlns="urn:ietf:params:xml:ns:recording:1"><datamode>complete</datamode><group group_id="d9836b259df931287df49822bd3a5e16"><associate-time>2026-07-20T17:29:39Z</associate-time></group><session session_id="20260720172939050790"><group-ref>d9836b259df931287df49822bd3a5e16</group-ref></session><participant participant_id="1003@dwang.netsapiens.com"><nameID aor="sip:1003@dwang.netsapiens.com"><name>Luke Skywalker</name></nameID></participant><participant participant_id="1001@dwang.netsapiens.com"><nameID aor="sip:1001w@dwang.netsapiens.com"><name>Din Djarin</name></nameID></participant><stream stream_id="SkvmlWgijIQuKcHP00654B" session_id="20260720172939050790"><label>1</label></stream><stream stream_id="tym50Xsy4eco8tK800654D" session_id="20260720172939050790"><label>2</label></stream><netsapiensExtension xmlns="http://schema.netsapiens.com/netsapiensSipRec" version="1.0"><groupSeq>0</groupSeq><serviceProviderID>tenant_1_23584</serviceProviderID><user userID="1003@dwang.netsapiens.com"><resellerID>NetSapiens</resellerID><site>San Diego</site><department>Engineering</department><callType>origCall</callType></user><callingPartyNumber>8587645225</callingPartyNumber><calledPartyNumber>8587645200</calledPartyNumber><byAction>ForwardSRing</byAction><byUserID>1001@dwang.netsapiens.com</byUserID></netsapiensExtension></recording>"""

# 07-22 call: two <user> elements, which is the repeated-element case.
CALL_0722 = """<?xml version="1.0" encoding="UTF-8"?><recording xmlns="urn:ietf:params:xml:ns:recording:1"><datamode>complete</datamode><session session_id="20260722210808064141"></session><participant participant_id="1003@dwang.netsapiens.com"><nameID aor="sip:1003w@dwang.netsapiens.com"><name>Luke Skywalker</name></nameID></participant><participant participant_id="1002@dwang.netsapiens.com"><nameID aor="sip:1002@dwang.netsapiens.com"><name>Boba Fett</name></nameID></participant><netsapiensExtension xmlns="http://schema.netsapiens.com/netsapiensSipRec" version="1.0"><groupSeq>0</groupSeq><serviceProviderID>tenant_1_23584</serviceProviderID><user userID="1002@dwang.netsapiens.com"><resellerID>NetSapiens</resellerID><site>San Diego</site><department>Engineering</department><callType>termCall</callType></user><user userID="1003@dwang.netsapiens.com"><resellerID>NetSapiens</resellerID><site>San Diego</site><department>Engineering</department><callType>origCall</callType></user><callingPartyNumber>8587645225</callingPartyNumber><calledPartyNumber>8587645200</calledPartyNumber><byAction>ForwardSRing</byAction><byUserID>1002@dwang.netsapiens.com</byUserID></netsapiensExtension></recording>"""


class TestParticipantTyping(unittest.TestCase):
    """A sip: AOR must never produce a mailto, and an extension is not a tel."""

    def setUp(self):
        self.parser = SIPRECParser()

    def test_sip_aor_never_becomes_mailto(self):
        # Regression: sip:1001w@dwang.netsapiens.com was landing in the vCon
        # as mailto="1001w@dwang.netsapiens.com", inventing an email address
        # for a party that never had one.
        parties = self.parser.parse_rs_metadata(CALL_0720)
        for p in parties:
            self.assertEqual(p["mailto"], "", f"sip: AOR produced a mailto: {p}")

    def test_pbx_extension_is_not_a_telephone_number(self):
        # sip:1003@... is a PBX extension, undialable outside the tenant.
        parties = self.parser.parse_rs_metadata(CALL_0720)
        luke = next(p for p in parties if p["name"] == "Luke Skywalker")
        self.assertEqual(luke["tel"], "")
        self.assertEqual(luke["uri"], "sip:1003@dwang.netsapiens.com")

    def test_names_and_ids_survive(self):
        parties = self.parser.parse_rs_metadata(CALL_0720)
        self.assertEqual([p["name"] for p in parties],
                         ["Luke Skywalker", "Din Djarin"])
        self.assertEqual(parties[0]["id"], "1003@dwang.netsapiens.com")

    def test_real_phone_numbers_still_classify_as_tel(self):
        xml = CALL_0720.replace("sip:1003@dwang", "sip:8587645225@dwang")
        parties = self.parser.parse_rs_metadata(xml)
        self.assertEqual(parties[0]["tel"], "8587645225")

    def test_tel_and_mailto_schemes_are_honored(self):
        for aor, field, want in (
            ("tel:+15085551234", "tel", "+15085551234"),
            ("mailto:someone@example.com", "mailto", "someone@example.com"),
        ):
            xml = CALL_0720.replace("sip:1003@dwang.netsapiens.com", aor)
            got = self.parser.parse_rs_metadata(xml)[0]
            self.assertEqual(got[field], want)


class TestVendorExtension(unittest.TestCase):
    """The extension block carries what RFC 7865 cannot."""

    def setUp(self):
        self.parser = SIPRECParser()

    def test_real_phone_numbers_are_captured(self):
        # These are the only real E.164 numbers in the payload, and they live
        # nowhere in the RFC 7865 participant elements.
        ext = self.parser.parse_vendor_extension(CALL_0720)
        self.assertEqual(ext["callingPartyNumber"], "8587645225")
        self.assertEqual(ext["calledPartyNumber"], "8587645200")

    def test_recording_reason_is_captured(self):
        ext = self.parser.parse_vendor_extension(CALL_0720)
        self.assertEqual(ext["byAction"], "ForwardSRing")
        self.assertEqual(ext["byUserID"], "1001@dwang.netsapiens.com")

    def test_version_and_namespace_recorded(self):
        ext = self.parser.parse_vendor_extension(CALL_0720)
        self.assertEqual(ext["version"], "1.0")
        self.assertEqual(ext["_element"], "netsapiensExtension")
        self.assertEqual(ext["_namespace"],
                         "http://schema.netsapiens.com/netsapiensSipRec")

    def test_nested_user_element(self):
        ext = self.parser.parse_vendor_extension(CALL_0720)
        self.assertEqual(ext["user"]["userID"], "1003@dwang.netsapiens.com")
        self.assertEqual(ext["user"]["department"], "Engineering")
        self.assertEqual(ext["user"]["callType"], "origCall")

    def test_repeated_user_elements_collect_into_a_list(self):
        ext = self.parser.parse_vendor_extension(CALL_0722)
        self.assertEqual(len(ext["user"]), 2)
        self.assertEqual([u["callType"] for u in ext["user"]],
                         ["termCall", "origCall"])

    def test_unknown_version_survives(self):
        """The 1.1 readiness check: unseen fields must not be dropped.

        Stand-in for the real 1.1 payload we have not captured. If this ever
        starts failing because someone enumerated known field names, that is
        the bug, not this test.
        """
        xml = CALL_0720.replace('version="1.0"', 'version="1.1"').replace(
            "<byAction>ForwardSRing</byAction>",
            "<byAction>ForwardSRing</byAction>"
            "<someNewFieldFrom11>whatever</someNewFieldFrom11>"
            "<nestedNewThing><inner>deep</inner></nestedNewThing>",
        )
        ext = self.parser.parse_vendor_extension(xml)
        self.assertEqual(ext["version"], "1.1")
        self.assertEqual(ext["someNewFieldFrom11"], "whatever")
        self.assertEqual(ext["nestedNewThing"]["inner"], "deep")
        self.assertEqual(ext["callingPartyNumber"], "8587645225")

    def test_absent_extension_is_empty_not_an_error(self):
        plain = """<?xml version="1.0"?><recording xmlns="urn:ietf:params:xml:ns:recording:1"><participant participant_id="a"><nameID aor="sip:a@b.com"><name>A</name></nameID></participant></recording>"""
        self.assertEqual(self.parser.parse_vendor_extension(plain), {})

    def test_malformed_xml_is_empty_not_an_error(self):
        self.assertEqual(self.parser.parse_vendor_extension("<not xml"), {})


class TestParticipantEnrichment(unittest.TestCase):
    """NetSapiens 1.1 per-party fields fold onto the RFC 7865 participants.

    Answers David Wang's 2026-09-06 questions in code: real number -> tel,
    uid -> did + stable uuid, aor kept for the core sip field.
    """

    def setUp(self):
        self.parser = SIPRECParser()

    def _enriched(self, xml):
        parts = self.parser.parse_rs_metadata(xml)
        vendor = self.parser.parse_vendor_extension(xml)
        return enrich_participants_from_vendor(parts, vendor)

    def test_real_number_becomes_e164_tel(self):
        boba = next(p for p in self._enriched(CALL_11) if p["name"] == "Boba Fett")
        self.assertEqual(boba["tel"], "+18587641002")

    def test_uid_becomes_did_and_stable_uuid(self):
        boba = next(p for p in self._enriched(CALL_11) if p["name"] == "Boba Fett")
        self.assertEqual(boba["did"], "1002@vbox.netsapiens.com")
        # Deterministic: same uid always yields the same core Party uuid.
        self.assertEqual(
            boba["uuid"],
            str(_uuid.uuid5(_PARTY_UUID_NS, "1002@vbox.netsapiens.com")),
        )
        again = next(p for p in self._enriched(CALL_11) if p["name"] == "Boba Fett")
        self.assertEqual(boba["uuid"], again["uuid"])

    def test_aor_preserved_for_sip_field(self):
        boba = next(p for p in self._enriched(CALL_11) if p["name"] == "Boba Fett")
        self.assertEqual(boba["uri"], "sip:1002@vbox.netsapiens.com")

    def test_join_is_by_participant_id_not_order(self):
        din = next(p for p in self._enriched(CALL_11) if p["name"] == "Din Djarin")
        self.assertEqual(din["tel"], "+18587641001")
        self.assertEqual(din["did"], "1001@vbox.netsapiens.com")

    def test_10_payload_leaves_tel_empty_not_guessed(self):
        # 1.0 has flat calledPartyNumber with no participant_id association, so
        # nothing is folded: undialable PBX extensions stay tel-less.
        parts = self.parser.parse_rs_metadata(CALL_0720)
        vendor = self.parser.parse_vendor_extension(CALL_0720)
        for p in enrich_participants_from_vendor(parts, vendor):
            self.assertEqual(p["tel"], "", f"1.0 fabricated a tel: {p}")

    def test_absent_vendor_is_a_noop(self):
        parts = self.parser.parse_rs_metadata(CALL_11)
        before = [dict(p) for p in parts]
        self.assertEqual(enrich_participants_from_vendor(parts, {}), before)


class TestE164(unittest.TestCase):
    def test_nanp_forms(self):
        self.assertEqual(_to_e164("8587641002"), "+18587641002")
        self.assertEqual(_to_e164("18587641002"), "+18587641002")
        self.assertEqual(_to_e164("+18587641002"), "+18587641002")

    def test_non_nanp_passes_through(self):
        self.assertEqual(_to_e164("1002"), "1002")   # PBX extension, untouched
        self.assertEqual(_to_e164(""), "")


if __name__ == "__main__":
    unittest.main()
