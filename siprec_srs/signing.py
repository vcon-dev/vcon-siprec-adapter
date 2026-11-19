"""JWS signing for emitted vCons.

Wraps the `vcon` library's `Vcon.sign()` API. Signing mutates the vCon
in place — after `Signer.sign(vcon)` returns, the vcon_dict is in JWS
(JSON Web Signature) form with `signatures` and `payload` fields, and
the original top-level fields are no longer present at the root.

Therefore:
  * Signing MUST happen AFTER all extension attachments are added.
  * Webhook delivery and storage of the signed form preserve the JWS.
  * Verification uses the corresponding public key.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from vcon import Vcon

logger = logging.getLogger(__name__)


class SigningError(Exception):
    """Raised when signing config is invalid or the operation fails."""


#: JWS algorithms this signer may use with an RSA key. core-05 section 6.2.2:
#: "The alg parameter MUST NOT have the value none".
RSA_JWS_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512")


class Signer:
    """Loads an RSA private key once and signs vCons in place."""

    def __init__(self, private_key: RSAPrivateKey, alg: str = "RS256"):
        self._check_alg(alg)
        self._private_key = private_key
        self._alg = alg

    @staticmethod
    def _check_alg(alg) -> None:
        """Reject `none` (any case or padding) and anything not an RSA JWS alg."""
        if not isinstance(alg, str) or alg.strip().lower() == "none":
            raise SigningError('JWS alg "none" is not permitted (core-05 section 6.2.2)')
        if alg not in RSA_JWS_ALGORITHMS:
            raise SigningError(
                f"JWS alg {alg!r} is not supported; use one of {RSA_JWS_ALGORITHMS}"
            )

    @classmethod
    def from_pem_file(
        cls,
        pem_path: str,
        password: Optional[bytes] = None,
        alg: str = "RS256",
    ) -> "Signer":
        """Load a PEM-encoded RSA private key from `pem_path`."""
        path = Path(pem_path)
        if not path.exists():
            raise SigningError(f"Signing key not found: {pem_path}")
        try:
            data = path.read_bytes()
            key = serialization.load_pem_private_key(data, password=password)
        except Exception as e:
            raise SigningError(f"Failed to load signing key from {pem_path}: {e}") from e
        if not isinstance(key, RSAPrivateKey):
            raise SigningError(
                f"Signing key at {pem_path} is not an RSA private key "
                f"(got {type(key).__name__}); JWS RS256 requires RSA"
            )
        return cls(key, alg=alg)

    def sign(self, vcon: Vcon) -> Vcon:
        """Sign the vCon in place; returns the same Vcon for chaining."""
        try:
            vcon.sign(self._private_key, alg=self._alg)
            logger.info(f"Signed vCon (uuid={vcon.uuid})")
            return vcon
        except Exception as e:
            raise SigningError(f"Failed to sign vCon {vcon.uuid}: {e}") from e

    @staticmethod
    def is_signed(vcon: Vcon) -> bool:
        """Return True iff `vcon.vcon_dict` is in JWS-wrapped form."""
        d = vcon.vcon_dict
        return "signatures" in d and "payload" in d
