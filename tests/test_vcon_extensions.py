"""Unit tests for siprec_srs.vcon_extensions.

These tests exercise the helpers that emit extension attachments directly
on a `vcon_dict` (a plain dict). They do NOT require the `vcon` library
to be installed, so they catch spec-shape bugs even when the lib is absent.
"""

import json

import pytest

from siprec_srs.vcon_extensions import (
    LAWFUL_BASIS_EXTENSION,
    SIP_SIGNALING_EXTENSION,
    VALID_LAWFUL_BASES,
    add_lawful_basis_attachment,
    add_sip_message_trace,
    annotate_dialog_with_sip,
    declare_extension,
)


class TestDeclareExtension:
    def test_adds_extension(self):
        d = {}
        declare_extension(d, "sip-signaling")
        assert d["extensions"] == ["sip-signaling"]

    def test_idempotent(self):
        d = {"extensions": ["sip-signaling"]}
        declare_extension(d, "sip-signaling")
        assert d["extensions"] == ["sip-signaling"]

    def test_appends_distinct_extensions(self):
        d = {}
        declare_extension(d, "sip-signaling")
        declare_extension(d, "lawful_basis")
        assert d["extensions"] == ["sip-signaling", "lawful_basis"]


class TestSipMessageTrace:
    def _build(self):
        d = {}
        add_sip_message_trace(
            d,
            call_id="abc@example.com",
            recording_session_id="rec-1",
            remote_uri="sip:alice@example.com",
            local_uri="sip:srs@example.com",
            media_streams=[{"type": "audio", "codec": "PCMU"}],
            start="2026-05-08T12:00:00+00:00",
        )
        return d

    def test_declares_extension(self):
        d = self._build()
        assert SIP_SIGNALING_EXTENSION in d["extensions"]

    def test_attachment_shape(self):
        d = self._build()
        atts = d["attachments"]
        assert len(atts) == 1
        a = atts[0]
        # core attachment fields
        assert a["purpose"] == "sip-message-trace"
        assert a["party"] == 0
        assert a["dialog"] == 0
        assert a["mediatype"] == "application/json"
        assert a["encoding"] == "json"
        # body must be JSON-encoded string per core spec
        assert isinstance(a["body"], str)
        body = json.loads(a["body"])
        assert body["call_id"] == "abc@example.com"
        assert body["recording_session_id"] == "rec-1"
        assert body["media_streams"][0]["codec"] == "PCMU"

    def test_no_attachment_uses_purpose_field_called_type(self):
        """Regression: sip-signaling attachments use `purpose`, not `type`."""
        d = self._build()
        a = d["attachments"][0]
        assert "type" not in a, "sip-signaling attachments must use `purpose`"
        assert "purpose" in a


class TestAnnotateDialogWithSip:
    def test_sets_sip_call_id(self):
        dialog = {"type": "recording"}
        annotate_dialog_with_sip(dialog, sip_call_id="abc@example.com")
        assert dialog["sip_call_id"] == "abc@example.com"

    def test_omits_unset_fields(self):
        dialog = {"type": "recording"}
        annotate_dialog_with_sip(dialog, sip_call_id="abc@example.com")
        assert "sip_from_tag" not in dialog
        assert "sip_to_tag" not in dialog
        assert "sip_cseq" not in dialog


class TestLawfulBasisAttachment:
    def _build(self, **overrides):
        d = {}
        kwargs = dict(
            lawful_basis="legitimate_interests",
            purposes=["recording", "transcription"],
            expiration=None,
            granted_at="2026-05-08T12:00:00+00:00",
            justification="test",
        )
        kwargs.update(overrides)
        add_lawful_basis_attachment(d, **kwargs)
        return d

    def test_declares_extension(self):
        d = self._build()
        assert LAWFUL_BASIS_EXTENSION in d["extensions"]

    def test_uses_type_not_purpose(self):
        """lawful_basis is the documented exception that uses `type:`."""
        d = self._build()
        a = d["attachments"][0]
        assert a["type"] == "lawful_basis"
        assert "purpose" not in a

    def test_attachment_shape(self):
        d = self._build()
        a = d["attachments"][0]
        assert a["party"] == 0
        assert a["dialog"] == 0
        assert a["encoding"] == "json"
        assert isinstance(a["body"], str)

    def test_body_required_fields(self):
        d = self._build()
        body = json.loads(d["attachments"][0]["body"])
        assert body["lawful_basis"] == "legitimate_interests"
        assert body["expiration"] is None  # explicit null permitted
        assert isinstance(body["purpose_grants"], list)
        assert len(body["purpose_grants"]) == 2
        for grant in body["purpose_grants"]:
            assert grant["granted"] is True
            assert grant["granted_at"] == "2026-05-08T12:00:00+00:00"
            assert grant["purpose"] in ("recording", "transcription")

    def test_invalid_lawful_basis_rejected(self):
        with pytest.raises(ValueError):
            add_lawful_basis_attachment(
                {},
                lawful_basis="bogus_basis",
                purposes=["recording"],
            )

    def test_all_six_lawful_bases_accepted(self):
        for basis in VALID_LAWFUL_BASES:
            d = self._build(lawful_basis=basis)
            assert json.loads(d["attachments"][0]["body"])["lawful_basis"] == basis
