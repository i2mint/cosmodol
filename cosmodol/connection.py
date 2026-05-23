"""Connection-layer for cosmodol.

Owns the expensive resource (``CosmosClient``) and the credential cascade.
See ``misc/docs/architecture.md`` Layer 0 and ``misc/docs/design_decisions.md`` §14.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Optional, Union

from azure.cosmos import CosmosClient


CredentialLike = Union[str, dict, Any, None]


# Env vars consulted by ``resolve_credential``.
_ENV_CONNECTION_STRING = "AZURE_COSMOS_CONNECTION_STRING"
_ENV_ENDPOINT = "AZURE_COSMOS_ENDPOINT"
_ENV_KEY = "AZURE_COSMOS_KEY"


_CONN_STR_ENDPOINT_RE = re.compile(r"AccountEndpoint=([^;]+)", re.IGNORECASE)
_CONN_STR_KEY_RE = re.compile(r"AccountKey=([^;]+)", re.IGNORECASE)


def _looks_like_connection_string(s: str) -> bool:
    return "AccountEndpoint=" in s and "AccountKey=" in s


def _parse_connection_string(cs: str) -> tuple[str, str]:
    """Parse a Cosmos connection string into ``(endpoint, key)``."""
    ep = _CONN_STR_ENDPOINT_RE.search(cs)
    k = _CONN_STR_KEY_RE.search(cs)
    if not (ep and k):
        raise ValueError(
            "Cosmos connection string must contain AccountEndpoint=... and AccountKey=..."
        )
    return ep.group(1), k.group(1)


def resolve_credential(
    *,
    credential: CredentialLike = None,
    connection_string: Optional[str] = None,
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
) -> dict:
    """Resolve a credential into a normalized form that can build a ``CosmosClient``.

    Cascade (first hit wins):

    1. Explicit ``credential=`` (with ``endpoint=`` for the URL)
    2. Explicit ``connection_string=``
    3. Explicit ``endpoint=`` + ``key=`` (or just ``endpoint=`` + AAD)
    4. Env var ``AZURE_COSMOS_CONNECTION_STRING``
    5. Env vars ``AZURE_COSMOS_ENDPOINT`` + ``AZURE_COSMOS_KEY``
    6. Env var ``AZURE_COSMOS_ENDPOINT`` alone + ``DefaultAzureCredential``

    Returns:
        A dict with: ``{"url": "...", "credential": <obj>}``

    Raises:
        ValueError: if no source resolves.
    """
    # 1. Explicit credential
    if credential is not None:
        if isinstance(credential, str) and _looks_like_connection_string(credential):
            ep, k = _parse_connection_string(credential)
            return {"url": ep, "credential": k}
        ep = endpoint or os.environ.get(_ENV_ENDPOINT)
        if ep is None:
            raise ValueError(
                "credential=... was provided but no endpoint. Pass endpoint=... or set "
                f"the {_ENV_ENDPOINT} env var."
            )
        return {"url": ep, "credential": credential}

    # 2. Explicit connection string
    if connection_string is not None:
        ep, k = _parse_connection_string(connection_string)
        return {"url": ep, "credential": k}

    # 3. Explicit endpoint + key (or endpoint alone)
    if endpoint is not None and key is not None:
        return {"url": endpoint, "credential": key}
    if endpoint is not None:
        return {"url": endpoint, "credential": _default_aad_credential()}

    # 4. Env: connection string
    env_cs = os.environ.get(_ENV_CONNECTION_STRING)
    if env_cs:
        ep, k = _parse_connection_string(env_cs)
        return {"url": ep, "credential": k}

    # 5. Env: endpoint + key
    env_ep = os.environ.get(_ENV_ENDPOINT)
    env_k = os.environ.get(_ENV_KEY)
    if env_ep and env_k:
        return {"url": env_ep, "credential": env_k}

    # 6. Env: endpoint alone + AAD
    if env_ep:
        return {"url": env_ep, "credential": _default_aad_credential()}

    raise ValueError(
        "Could not resolve Cosmos DB credentials. Provide one of:\n"
        "  - credential=<obj or connection string>\n"
        "  - connection_string=<str>\n"
        "  - endpoint=<url> + key=<key>\n"
        "  - env var AZURE_COSMOS_CONNECTION_STRING\n"
        "  - env vars AZURE_COSMOS_ENDPOINT + AZURE_COSMOS_KEY\n"
        "  - env var AZURE_COSMOS_ENDPOINT alone (uses DefaultAzureCredential)\n"
    )


def _default_aad_credential():
    """Lazy import to keep ``azure-identity`` an optional dependency."""
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as e:
        raise ImportError(
            "azure-identity is required for AAD credentials. Install with: "
            "`pip install azure-identity`"
        ) from e
    return DefaultAzureCredential()


@dataclass
class CosmosConnection:
    """Holds a resolved credential and a lazy ``CosmosClient``.

    This is the dependency-injection seam for the package. Tests construct one pointing
    at the emulator without touching any store class.

    Args:
        credential: explicit credential object or account-key string.
        connection_string: full ``AccountEndpoint=...;AccountKey=...`` string.
        endpoint: Cosmos endpoint URL (with or without credential).
        key: account master key.
        consistency_level: optional override; defaults to inheriting the account default.
        client_kwargs: extra kwargs forwarded to ``CosmosClient``.
    """

    credential: CredentialLike = None
    connection_string: Optional[str] = None
    endpoint: Optional[str] = None
    key: Optional[str] = None
    consistency_level: Optional[str] = None
    client_kwargs: dict = field(default_factory=dict)

    @cached_property
    def _resolved(self) -> dict:
        return resolve_credential(
            credential=self.credential,
            connection_string=self.connection_string,
            endpoint=self.endpoint,
            key=self.key,
        )

    @cached_property
    def client(self) -> CosmosClient:
        """The lazily-constructed ``CosmosClient``. Cached for the connection's lifetime."""
        r = self._resolved
        kw = dict(self.client_kwargs)
        if self.consistency_level is not None:
            kw.setdefault("consistency_level", self.consistency_level)
        return CosmosClient(url=r["url"], credential=r["credential"], **kw)

    def database(self, name: str):
        return self.client.get_database_client(name)

    def container(self, database: str, container: str):
        return self.client.get_database_client(database).get_container_client(container)

    @classmethod
    def from_anything(cls, source) -> "CosmosConnection":
        """Convenience: build a ``CosmosConnection`` from a thing-or-spec.

        Accepts:
            - ``CosmosConnection`` (returned as-is)
            - ``CosmosClient`` (wrapped without further resolution)
            - ``str`` (connection string)
            - ``dict`` (passed as kwargs)
            - ``None`` (defer to env / AAD)
        """
        if isinstance(source, cls):
            return source
        if isinstance(source, CosmosClient):
            inst = cls.__new__(cls)
            inst.credential = None
            inst.connection_string = None
            inst.endpoint = None
            inst.key = None
            inst.consistency_level = None
            inst.client_kwargs = {}
            inst.__dict__["client"] = source
            return inst
        if isinstance(source, str):
            return cls(connection_string=source)
        if isinstance(source, dict):
            return cls(**source)
        if source is None:
            return cls()
        raise TypeError(
            f"Cannot build CosmosConnection from {type(source).__name__}: {source!r}"
        )
