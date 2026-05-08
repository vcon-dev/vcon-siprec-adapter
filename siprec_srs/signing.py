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


class Signer:
    """Loads an RSA private key once and signs vCons in place."""

    def __init__(self, private_key: RSAPrivateKey):
        self._private_key = private_key

    @classmethod
    def from_pem_file(
        cls,
        pem_path: str,
        password: Optional[bytes] = None,
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
        return cls(key)

    def sign(self, vcon: Vcon) -> Vcon:
        """Sign the vCon in place; returns the same Vcon for chaining."""
        try:
            vcon.sign(self._private_key)
            logger.info(f"Signed vCon (uuid={vcon.uuid})")
            return vcon
        except Exception as e:
            raise SigningError(f"Failed to sign vCon {vcon.uuid}: {e}") from e

    @staticmethod
    def is_signed(vcon: Vcon) -> bool:
        """Return True iff `vcon.vcon_dict` is in JWS-wrapped form."""
        d = vcon.vcon_dict
        return "signatures" in d and "payload" in d
