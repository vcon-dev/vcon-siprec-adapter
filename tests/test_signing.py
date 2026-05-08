"""Tests for JWS vCon signing."""

import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from vcon import Vcon

from siprec_srs.signing import Signer, SigningError


def _generate_key_pem(password: bytes = None) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    enc = (
        serialization.BestAvailableEncryption(password)
        if password else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=enc,
    )


def _build_vcon():
    v = Vcon.build_new()
    v.vcon_dict["vcon"] = "0.4.0"
    return v


class TestKeyLoading:
    def test_loads_unencrypted_pem(self):
        with tempfile.TemporaryDirectory() as tmp:
            pem = Path(tmp) / "key.pem"
            pem.write_bytes(_generate_key_pem())
            signer = Signer.from_pem_file(str(pem))
            assert signer is not None

    def test_loads_password_protected_pem(self):
        with tempfile.TemporaryDirectory() as tmp:
            pem = Path(tmp) / "key.pem"
            pem.write_bytes(_generate_key_pem(password=b"correct horse"))
            signer = Signer.from_pem_file(str(pem), password=b"correct horse")
            assert signer is not None

    def test_missing_file_raises_signing_error(self):
        with pytest.raises(SigningError):
            Signer.from_pem_file("/nonexistent/key.pem")

    def test_wrong_password_raises_signing_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            pem = Path(tmp) / "key.pem"
            pem.write_bytes(_generate_key_pem(password=b"correct"))
            with pytest.raises(SigningError):
                Signer.from_pem_file(str(pem), password=b"wrong")

    def test_non_rsa_key_raises_signing_error(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        ec_key = ec.generate_private_key(ec.SECP256R1())
        pem = ec_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ec.pem"
            path.write_bytes(pem)
            with pytest.raises(SigningError, match="not an RSA"):
                Signer.from_pem_file(str(path))


class TestSigning:
    def setup_method(self):
        self.key_pem = _generate_key_pem()
        self.tmp = tempfile.TemporaryDirectory()
        pem_path = Path(self.tmp.name) / "key.pem"
        pem_path.write_bytes(self.key_pem)
        self.signer = Signer.from_pem_file(str(pem_path))

    def teardown_method(self):
        self.tmp.cleanup()

    def test_unsigned_vcon_detected(self):
        v = _build_vcon()
        assert not Signer.is_signed(v)

    def test_signed_vcon_has_jws_fields(self):
        v = _build_vcon()
        signed = self.signer.sign(v)
        assert signed is v  # in-place
        assert Signer.is_signed(signed)
        assert "signatures" in signed.vcon_dict
        assert "payload" in signed.vcon_dict
        # The lib's JWS form replaces top-level fields with the JWS wrapper
        # plus payload; signature value must be a non-empty string.
        sigs = signed.vcon_dict["signatures"]
        assert isinstance(sigs, list) and sigs
        assert isinstance(sigs[0]["signature"], str) and sigs[0]["signature"]

    def test_signed_vcon_verifies_with_public_key(self):
        v = _build_vcon()
        self.signer.sign(v)
        # Pull the public key from the same private key.
        priv = serialization.load_pem_private_key(self.key_pem, password=None)
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        assert v.verify(pub_pem) is True
