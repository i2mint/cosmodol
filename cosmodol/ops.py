"""Key-taking operations, as free functions over the caller's (possibly wrapped) store.

Why free functions and not methods: ``dol`` wraps stores by delegation, so a *method* of a
wrapped store runs bound to the inner leaf and receives the caller's **outer**, unmapped key
(i2mint/cosmodol#1, i2mint/dol#83). Under a key codec that silently replaced, batch-wrote,
cascade-deleted or created the wrong resource. A free function is handed the outer store
itself, so it can resolve the key through every wrapping layer before touching Cosmos:

>>> from dol import KeyCodecs
>>> from cosmodol import CosmosItems, replace_item
>>> from cosmodol.testing import FakeContainerProxy
>>> c = FakeContainerProxy(partition_key_path="/_pk")
>>> view = KeyCodecs.prefixed("app_")(
...     CosmosItems(c, partition_key_value="t", partition_key_path="/_pk")
... )
>>> view["doc1"] = {"v": 1}
>>> _ = c.upsert_item({"id": "doc1", "_pk": "t", "v": "someone else's"})
>>> _ = replace_item(view, "doc1", {"v": 2})
>>> sorted((i, d["v"]) for (_pk, i), d in c._data.items())
[('app_doc1', 2), ('doc1', "someone else's")]

The same design is s3dol's ADR-0011 (keyed capabilities leave the store's method surface;
destructive ones become free functions). The removed methods now raise an
``AttributeError`` that names their replacement here.

Caveats, stated rather than hidden (ADR-0011 D3a):

- Keys are resolved with ``dol.inner_most_key``, i.e. each wrapping layer's
  ``_id_of_key``. Every ``dol``-shipped wrapper (``wrap_kvs``, ``KeyCodecs``, ``Pipe``,
  ``filt_iter``, ``cached_keys``) does this right; a hand-rolled ``__getattr__``
  passthrough layer can make it apply a transform twice.
- Values (``replace_item``, ``batch_items``) go through each layer's ``_data_of_obj``,
  which is what ``wrap_kvs(data_of_obj=...)`` and ``ValueCodecs`` install. A key-aware
  ``preset=`` write hook is **not** followed — such a hook only runs inside
  ``__setitem__``.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from azure.cosmos import PartitionKey
from dol import inner_most_key
from dol.dig import inner_most

from cosmodol.base import batch as _batch, point_replace
from cosmodol.errors import (
    ContainerNotFoundError,
    DatabaseNotFoundError,
    validate_cosmos_id,
)
from cosmodol.stores import CosmosItems, CosmosPartitionedItems
from cosmodol.trees import CosmosAccount, CosmosDatabase


# ---------------------------------------------------------------------------
# Resolution: outer store -> leaf, outer key -> leaf key, outer value -> leaf value
# ---------------------------------------------------------------------------


def _leaf_of(store, leaf_type, *, fn: str):
    """The cosmodol leaf at the bottom of ``store``'s ``.store`` chain.

    Validates the first argument (a free function accepts any object, where a method
    could not be called on the wrong one).
    """
    leaf = store
    while not isinstance(leaf, leaf_type) and hasattr(leaf, "store"):
        leaf = leaf.store
    if not isinstance(leaf, leaf_type):
        raise TypeError(
            f"{fn}() needs a {leaf_type.__name__} (or a dol wrapper around one); "
            f"got {type(store).__name__}."
        )
    return leaf


def _leaf_key(store, leaf, k):
    """``k`` as the leaf sees it: mapped through every wrapping layer of ``store``.

    Replaces, never composes with, any leaf-level key mapping (``inner_most_key`` walks
    the whole chain).
    """
    if store is leaf:
        return k
    return inner_most_key(store, k)


def _leaf_value(store, leaf, v):
    if store is leaf:
        return v
    return inner_most(store, v, "_data_of_obj", default=v)


# ---------------------------------------------------------------------------
# Item-level operations (CosmosItems)
# ---------------------------------------------------------------------------


def replace_item(store, k, v, *, etag: Optional[str] = None) -> dict:
    """Full replace of the existing item at key ``k``, optionally ETag-conditional.

    Unlike ``store[k] = v`` (an upsert), this fails with ``ItemNotFoundError`` if the item
    does not exist, and with a precondition error if ``etag`` no longer matches.

    Args:
        store: A ``CosmosItems``, or any ``dol`` wrapper around one. ``k`` and ``v`` are in
            the *caller's* key and value space; they are mapped down to the leaf.
        k: The key, as ``store[k]`` would take it.
        v: The value, as ``store[k] = v`` would take it.
        etag: If given, only replace when the stored item's ``_etag`` still equals it.

    Returns:
        The stored document as Cosmos returned it (leaf form; system fields stripped if the
        leaf strips them).
    """
    leaf = _leaf_of(store, CosmosItems, fn="replace_item")
    k = _leaf_key(store, leaf, k)
    body = leaf._prepare_body(k, _leaf_value(store, leaf, v))
    item, hdrs = point_replace(
        leaf.container, k, body, leaf.partition_key_value, etag=etag
    )
    leaf._observe("replace", hdrs)
    return leaf._maybe_strip(item)


#: Operations ``batch_items`` accepts, and whether each carries a value.
BATCH_OPS = {
    "create": True,
    "upsert": True,
    "replace": True,
    "patch": True,  # the "value" is the list of patch operations, passed through as-is
    "read": False,
    "delete": False,
}


def batch_items(store, operations: Iterable[tuple]) -> list[dict]:
    """Transactional batch within the store's partition, in the caller's key space.

    Each operation is ``(op, key)`` or ``(op, key, value)``, with ``op`` one of
    ``BATCH_OPS``::

        [("upsert", "a", {"v": 1}), ("replace", "b", {"v": 2}), ("delete", "c")]

    Keys (and values, except ``patch`` operation lists) are mapped down to the leaf exactly
    as ``replace_item`` maps them, and bodies get the leaf's usual ``id`` /
    partition-key injection. The SDK's raw ``(op, args, kwargs)`` triples are not accepted
    here, because the ids inside them are opaque to any key mapping; use the metal-layer
    ``cosmodol.batch(container, triples, partition_key)`` for those.
    """
    leaf = _leaf_of(store, CosmosItems, fn="batch_items")
    triples = []
    for operation in operations:
        op, k, *rest = operation
        if op not in BATCH_OPS:
            raise ValueError(
                f"Unknown batch op {op!r}; expected one of {list(BATCH_OPS)}."
            )
        if len(rest) != int(BATCH_OPS[op]):
            shape = "(op, key, value)" if BATCH_OPS[op] else "(op, key)"
            raise ValueError(f"Batch op {op!r} takes {shape}; got {operation!r}.")
        k = _leaf_key(store, leaf, k)
        if op in ("create", "upsert", "replace"):
            body = leaf._prepare_body(k, _leaf_value(store, leaf, rest[0]))
            args = (body,) if op != "replace" else (k, body)
        elif op == "patch":
            if leaf.strict_keys:
                validate_cosmos_id(k)
            args = (k, rest[0])
        else:
            if leaf.strict_keys:
                validate_cosmos_id(k)
            args = (k,)
        triples.append((op, args, {}))
    return _batch(leaf.container, triples, leaf.partition_key_value)


def partition(store, pk_value, *, wrap: Optional[Callable] = None) -> Any:
    """Narrow a ``CosmosPartitionedItems`` to one partition; zero round-trips.

    On an unwrapped store this returns a ``CosmosItems`` sharing the parent's settings.

    A wrapped parent's transforms cannot be carried onto the child automatically (the
    parent's keys are ``(pk, id)`` tuples, the child's are ids), so a wrapped store is
    refused unless you say how to wrap the child::

        sub = partition(redacted_store, "tenant-1", wrap=my_value_codec)

    Args:
        store: A ``CosmosPartitionedItems``, or a ``dol`` wrapper around one.
        pk_value: The partition-key value, as stored (not passed through any key codec).
        wrap: Applied to the child store before it is returned. Required when ``store``
            is wrapped; ``wrap=lambda s: s`` explicitly asks for the bare child.
    """
    leaf = _leaf_of(store, CosmosPartitionedItems, fn="partition")
    if store is not leaf and wrap is None:
        raise TypeError(
            f"partition() refused: {type(store).__name__} wraps a CosmosPartitionedItems, "
            "and the child partition would silently lose those wrappers (key and value "
            "codecs alike). Pass wrap=<the same wrapper, for the child's id keys> — or "
            "wrap=lambda s: s to explicitly take the bare child."
        )
    child = CosmosItems(
        leaf.container,
        partition_key_value=pk_value,
        partition_key_path=leaf.partition_key_path,
        inject_id=leaf.inject_id,
        inject_partition_key=leaf.inject_partition_key,
        strict_keys=leaf.strict_keys,
        strip_system_fields=leaf.strip_system_fields,
        record_ru=leaf.record_ru,
    )
    return child if wrap is None else wrap(child)


# ---------------------------------------------------------------------------
# Tree-level operations (CosmosDatabase, CosmosAccount)
# ---------------------------------------------------------------------------


def add_container(
    db_store,
    name: str,
    *,
    partition_key_path: str = "/id",
    throughput: Optional[int] = None,
    indexing_policy: Optional[dict] = None,
    default_ttl: Optional[int] = None,
    unique_key_policy: Optional[dict] = None,
    conflict_resolution_policy: Optional[dict] = None,
    **extra,
):
    """Create a container named ``name`` (in the caller's key space) in ``db_store``.

    Args:
        db_store: A ``CosmosDatabase``, or a ``dol`` wrapper around one.
        name: Container name, as ``db_store[name]`` would take it.
        partition_key_path: Partition-key path (e.g. ``"/id"``, ``"/tenantId"``).
            Defaults to ``"/id"`` per ``misc/docs/design_decisions.md`` §3.
        throughput: Provisioned RU/s (e.g. 400). ``None`` uses the database's
            shared throughput if any, otherwise the account default.
        indexing_policy, default_ttl, unique_key_policy, conflict_resolution_policy:
            Standard Cosmos container kwargs.
        **extra: Forwarded to ``DatabaseProxy.create_container``.

    Returns:
        The SDK's ``ContainerProxy``.
    """
    leaf = _leaf_of(db_store, CosmosDatabase, fn="add_container")
    return leaf.database.create_container(
        id=_leaf_key(db_store, leaf, name),
        partition_key=PartitionKey(path=partition_key_path),
        offer_throughput=throughput,
        indexing_policy=indexing_policy,
        default_ttl=default_ttl,
        unique_key_policy=unique_key_policy,
        conflict_resolution_policy=conflict_resolution_policy,
        **extra,
    )


def _partition_key_expr(container) -> Optional[str]:
    """Cosmos SQL expression for the container's partition key, e.g. ``c["tenant"]``."""
    paths = (container.read().get("partitionKey") or {}).get("paths") or []
    if not paths:
        return None
    return "c" + "".join(f'["{p}"]' for p in paths[0].strip("/").split("/"))


def delete_container(db_store, name: str, *, force: bool = False) -> None:
    """Drop container ``name`` (in the caller's key space); ``force`` empties it first.

    Without ``force`` this is ``del db_store[name]``, which refuses a non-empty container.
    With ``force=True`` every item is deleted, one point delete each, and then the
    container is dropped. Not recoverable without a Cosmos backup.
    """
    leaf = _leaf_of(db_store, CosmosDatabase, fn="delete_container")
    if not force:
        del db_store[name]
        return
    k = _leaf_key(db_store, leaf, name)
    try:
        c = leaf.database.get_container_client(k)
        pk_expr = _partition_key_expr(c)
    except Exception as e:
        raise ContainerNotFoundError(k) from e
    select = (
        "SELECT c.id FROM c"
        if pk_expr is None
        else (f"SELECT c.id, {pk_expr} AS pk FROM c")
    )
    for item in c.query_items(query=select, enable_cross_partition_query=True):
        c.delete_item(item=item["id"], partition_key=item.get("pk"))
    leaf.database.delete_container(k)


def add_database(
    account_store, name: str, *, throughput: Optional[int] = None, **extra
):
    """Create database ``name`` (in the caller's key space); return it as the account's
    values are returned (``account_store[name]``)."""
    leaf = _leaf_of(account_store, CosmosAccount, fn="add_database")
    leaf.client.create_database(
        id=_leaf_key(account_store, leaf, name), offer_throughput=throughput, **extra
    )
    return account_store[name]


def delete_database(account_store, name: str, *, force: bool = False) -> None:
    """Drop database ``name`` (in the caller's key space); ``force`` drops its containers.

    Without ``force`` this is ``del account_store[name]``, which refuses a non-empty
    database. Not recoverable without a Cosmos backup.
    """
    leaf = _leaf_of(account_store, CosmosAccount, fn="delete_database")
    if not force:
        del account_store[name]
        return
    k = _leaf_key(account_store, leaf, name)
    try:
        db = leaf.client.get_database_client(k)
        db.read()
    except Exception as e:
        raise DatabaseNotFoundError(k) from e
    for c in db.list_containers():
        db.delete_container(c["id"])
    leaf.client.delete_database(k)


__all__ = [
    "replace_item",
    "batch_items",
    "BATCH_OPS",
    "partition",
    "add_container",
    "delete_container",
    "add_database",
    "delete_database",
]
