"""Feature flags via the official Flagsmith Python SDK.

- Running app: remote evaluation against FLAGSMITH_ENVIRONMENT_KEY (+ FLAGSMITH_SDK_API_URL).
- Tests / DoNotMix: offline mode with a local environment document
  (DONOTMIX_ENV_DOCUMENT or MIXMART_ENV_DOCUMENT), evaluated by the SDK's own engine,
  so segment overrides behave exactly as in Flagsmith.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

from flagsmith import Flagsmith
from flagsmith.offline_handlers import LocalFileHandler

FLAG_NAMES = (
    "new_checkout", "dynamic_pricing", "discount_engine", "guest_checkout", "express_shipping",
    "loyalty_program", "new_payment", "recommendation", "ai_search", "dark_mode",
)


def client(document_path: str | None = None) -> Flagsmith:
    path = document_path or os.environ.get("DONOTMIX_ENV_DOCUMENT") or os.environ.get("MIXMART_ENV_DOCUMENT")
    return _client(path, os.environ.get("FLAGSMITH_ENVIRONMENT_KEY"))


@lru_cache(maxsize=8)
def _client(path: str | None, environment_key: str | None) -> Flagsmith:
    # cache keyed by the document actually used, so a long-lived process never serves stale flags
    if path:
        return Flagsmith(offline_mode=True, offline_handler=LocalFileHandler(path))
    return Flagsmith(
        environment_key=environment_key,
        api_url=os.environ.get("FLAGSMITH_SDK_API_URL", "http://localhost:8000/api/v1/"),
    )


@dataclass(frozen=True)
class Features:
    """Resolved flags for one request context (identity + traits)."""

    enabled_flags: frozenset

    def on(self, name: str) -> bool:
        return name in self.enabled_flags


def features_for(identifier: str | None = None, traits: dict | None = None, document_path: str | None = None) -> Features:
    fs = client(document_path)
    flags = fs.get_identity_flags(identifier, traits=traits or {}) if identifier else fs.get_environment_flags()
    return Features(frozenset(f.feature_name for f in flags.all_flags() if f.enabled))


def features_from_env() -> Features:
    """Context used by the test-suite: DONOTMIX_IDENTITY / DONOTMIX_TRAITS select the persona."""
    identifier = os.environ.get("DONOTMIX_IDENTITY", "shopper-1")
    traits = json.loads(os.environ.get("DONOTMIX_TRAITS") or "{}")
    return features_for(identifier, traits)
