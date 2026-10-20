"""Verify administrator-pinned Ed25519 Host identity, audience and exact arguments."""

import base64
import time
from cryptography.exceptions import InvalidSignature
from .common import TaskError, canonical, digest, require
from .service import Context


class ProofVerifier:
    def __init__(self, keyset, audience):
        self.keyset, self.audience = keyset, audience

    def verify(self, proof, tool, arguments, *, write=False, now=None):
        now = time.time() if now is None else now
        require(
            isinstance(proof, dict) and proof.get("algorithm") == "Ed25519",
            "UNAUTHENTICATED",
        )
        key = self.keyset.get(proof.get("key_id"))
        require(key is not None, "UNAUTHENTICATED")
        claims = proof.get("claims", {})
        try:
            key["public_key"].verify(
                base64.b64decode(proof["signature"], validate=True),
                canonical(claims).encode(),
            )
        except (InvalidSignature, ValueError, KeyError, TypeError):
            raise TaskError("UNAUTHENTICATED", "Invalid Host signature") from None
        require(
            claims.get("version") == "codex-host-call/v1"
            and claims.get("host_id") == key["host_id"]
            and claims.get("principal_id") == key["principal_id"],
            "UNAUTHENTICATED",
        )
        require(
            claims.get("audience") == self.audience
            and claims.get("tool") == tool
            and claims.get("arguments_digest") == digest(arguments),
            "FORBIDDEN",
        )
        require(
            isinstance(claims.get("issued_at"), (int, float))
            and isinstance(claims.get("expires_at"), (int, float))
            and claims["issued_at"] <= now + 5
            and now < claims["expires_at"] <= claims["issued_at"] + 120,
            "UNAUTHENTICATED",
            "Expired Host proof",
        )
        interaction = claims.get("interaction", {})
        confirmed = (
            interaction.get("decision") == "confirmed"
            and bool(interaction.get("id"))
            and interaction.get("arguments_digest") == digest(arguments)
        )
        if write:
            require(confirmed, "AUTHORIZATION_REQUIRED")
        for field in (
            "session_id",
            "thread_id",
            "workspace_ref",
            "app_instance_id",
            "call_id",
            "nonce",
        ):
            require(
                isinstance(claims.get(field), str) and claims[field], "UNAUTHENTICATED"
            )
        return Context(
            claims["principal_id"],
            claims["host_id"],
            claims["session_id"],
            claims["thread_id"],
            claims["workspace_ref"],
            role="app",
            call_id=claims["call_id"],
            confirmed=confirmed,
            interaction_id=interaction.get("id", ""),
        )
