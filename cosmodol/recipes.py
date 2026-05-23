"""Convenience wrappers and factories for cosmodol.

Layer C of the architecture. Built only by composition over Layer B
(``cosmodol.stores`` / ``cosmodol.trees``). See ``misc/docs/architecture.md`` Layer C.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Union

from cosmodol.connection import CosmosConnection
from cosmodol.stores import (
    CosmosItems,
    CosmosPartitionedItems,
    SYSTEM_FIELDS,
    _strip_system,
)


def strip_system_fields(item: dict) -> dict:
    """Return ``item`` with Cosmos system fields removed."""
    return _strip_system(item)


def cosmos_store(
    *,
    database: str,
    container: str,
    connection: Union[CosmosConnection, Any, None] = None,
    credential: Any = None,
    connection_string: Optional[str] = None,
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
    partition_key_value: Any = None,
    partition_key_path: Optional[str] = None,
    value_codec: Optional[Callable] = None,
    strip_system_fields: bool = True,
):
    """Build a ready-to-use Cosmos store (``CosmosItems`` or ``CosmosPartitionedItems``).

    The store flavor is chosen based on which partition-key kwarg is passed:

    - ``partition_key_value=...``  → ``CosmosItems`` (keys = id strings, fixed partition).
    - ``partition_key_path=...``   → ``CosmosPartitionedItems`` (keys = (pk, id) tuples).
    - Neither given                → raises ``ValueError``.

    Args:
        database: Database name.
        container: Container name.
        connection: A ``CosmosConnection`` (or anything ``from_anything`` accepts) to
            reuse a client. Mutually exclusive with explicit credential kwargs.
        credential, connection_string, endpoint, key: Forwarded to ``CosmosConnection``
            when ``connection`` is None.
        partition_key_value: Fixed partition key value → returns ``CosmosItems``.
        partition_key_path: Partition key property path → returns ``CosmosPartitionedItems``.
        value_codec: A decorator that, given a class, returns a wrapped class. Typically
            a partially-applied ``dol.wrap_kvs(...)``.
        strip_system_fields: Strip Cosmos system fields from returned items.

    Returns:
        A ``CosmosItems`` or ``CosmosPartitionedItems`` instance, possibly codec-wrapped.
    """
    if connection is not None and (
        credential is not None
        or connection_string is not None
        or endpoint is not None
        or key is not None
    ):
        raise ValueError(
            "Pass either `connection=...` or the explicit "
            "(credential|connection_string|endpoint|key) kwargs — not both."
        )

    conn = (
        CosmosConnection.from_anything(connection)
        if connection is not None
        else CosmosConnection(
            credential=credential,
            connection_string=connection_string,
            endpoint=endpoint,
            key=key,
        )
    )

    container_proxy = conn.container(database, container)

    if partition_key_value is not None and partition_key_path is None:
        cls = CosmosItems
        store_kwargs = dict(
            partition_key_value=partition_key_value,
            strip_system_fields=strip_system_fields,
        )
    elif partition_key_path is not None and partition_key_value is None:
        cls = CosmosPartitionedItems
        store_kwargs = dict(
            partition_key_path=partition_key_path,
            strip_system_fields=strip_system_fields,
        )
    elif partition_key_value is not None and partition_key_path is not None:
        cls = CosmosItems
        store_kwargs = dict(
            partition_key_value=partition_key_value,
            partition_key_path=partition_key_path,
            strip_system_fields=strip_system_fields,
        )
    else:
        raise ValueError(
            "Pass either `partition_key_value=...` (for CosmosItems) or "
            "`partition_key_path=...` (for CosmosPartitionedItems)."
        )

    if value_codec is not None:
        cls = value_codec(cls)

    return cls(container_proxy, **store_kwargs)


__all__ = [
    "cosmos_store",
    "strip_system_fields",
    "SYSTEM_FIELDS",
]
