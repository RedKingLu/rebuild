"""OpenCode model resolver — unified mapping layer (R9-5-5 T1).

D-088⑤ / D-037 / B-ORCH-02: All model calls from OpenCode must derive their
model_id, base_url, and api_key from the platform ProviderRegistry/ModelGateway —
no hardcoded endpoint or direct env reads.

The resolver accepts project context and produces the three parameters that
opencode needs to start a session.  api_key is used only to build the subprocess
env dict and is never returned in API responses or logged.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("rebuild.opencode_model_resolver")


class ResolverError(Exception):
    """Raised when model resolution fails and the caller must not proceed."""


class OpenCodeModelResolver:
    """Resolve (model_id, base_url, api_key) for an OpenCode session.

    All three values come from the platform ModelGateway (D-037).
    If resolution fails for any reason, raises ResolverError — the caller
    must propagate the error rather than fall back to a hardcoded default
    (G2 / B-ORCH-02 invariant).
    """

    def resolve(
        self,
        *,
        project_id: Optional[str] = None,
        strategy_id: str = "system-default",
        user_override: Optional[str] = None,
    ) -> dict:
        """Return {model_id, base_url, api_key} for an OpenCode session.

        api_key is the decrypted plaintext key — callers MUST inject it only
        into subprocess env (OPENAI_API_KEY) and MUST NOT log, return, or store it.

        Raises:
            ResolverError: If no usable model/key can be resolved.
        """
        try:
            from app.dependencies import get_services
            gw = get_services().model_gateway
        except Exception as exc:
            raise ResolverError(f"ModelGateway unavailable: {exc}") from exc

        target = gw.resolve_call_target(
            user_override=user_override,
            strategy_id=strategy_id,
            project_id=project_id,
        )
        if not target:
            raise ResolverError(
                "No usable model resolved for OpenCode — "
                "platform has no configured provider with a valid key."
            )

        model_id = target.get("model")
        base_url = target.get("api_base")
        api_key = target.get("api_key")

        if not model_id:
            raise ResolverError("Resolved target has no model_id — cannot start OpenCode session.")
        if not base_url:
            raise ResolverError(
                f"Resolved model '{model_id}' has no endpoint (base_url) — cannot configure OpenCode."
            )
        if not api_key:
            raise ResolverError(
                f"Resolved model '{model_id}' has no api_key — "
                "configure a credential in the platform provider settings."
            )

        log.debug(
            "opencode_model_resolver: resolved model=%r provider=%r reason=%r",
            model_id, target.get("provider_id"), target.get("selection_reason"),
        )
        # SECURITY: api_key is only returned for immediate subprocess env injection.
        # It MUST NOT be stored, logged, or returned to API callers.
        return {
            "model_id": model_id,
            "base_url": base_url,
            "api_key": api_key,  # plaintext — subprocess env only, never log/return
        }
