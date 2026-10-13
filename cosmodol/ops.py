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

How a function honours the wrapper (i2mint/cosmodol#3): every write goes through the
caller's store itself. A function performs the plain mapping write (``store[k] = v`` or
``del store[k]``) on the outer store, after telling the cosmodol leaf, through
``cosmodol.routing``, how to execute the one write that reaches it (a conditional replace,
a batch entry, a cascade, a create). So each layer runs exactly as it does for that
mapping write:

- **Key and value mapping**, ``preset=`` hooks, ``ValueCodecs``: applied by the layers
  themselves, class- and instance-wraps alike.
- **Write guards**: a layer whose ``__setitem__``/``__delitem__`` refuses (a read-only
  wrapper, ``dol.disable_setitem``) refuses the operation too.
- **Scope**: a ``filt_iter`` scope refuses out-of-scope keys in its ``__setitem__`` /
  ``__delitem__``, now including keys an operation *creates* (batch ``create``/``upsert``,
  ``add_container``, ``add_database``). Operations on *existing* resources also check
  ``k in store`` through the outer store first (one point read), for wrappers that scope
  only by ``__contains__``.
- **Write-through caches** see the value they would see for ``store[k] = v``. A cache
  that only fills on reads (``dol.cache_vals``) is stale after any write, mapping or not.
- **A layer that writes another key first** (an index, an audit record), **writes twice,
  or swallows the write** makes the function raise ``cosmodol.routing.RedirectError``;
  the leaf refuses any write but the expected one, before acting. An error a layer
  catches is re-raised to the caller.

What remains outside a function's reach, stated rather than hidden (s3dol ADR-0011 D3a):

- A layer that performs the write in another thread or later (a write-behind buffer)
  escapes the redirect: that write is the leaf's ordinary upsert or delete, without the
  operation's semantics (no ETag check, outside a batch's transaction). The function
  raises ``RedirectError``, but cannot undo it. A write of the *same key* from another
  thread or task while the operation is pending is refused (``ConcurrentWriteError``, a
  ``RedirectError``), so a layer that writes from a thread the operation waits for cannot
  slip past it; only a write that lands after the operation has finished escapes.
- ``batch_items`` collects its writes through the layers first and commits them as one
  transaction afterwards: a layer that acts after the inner write returns (a write-through
  cache, an audit log) has acted even if the transaction then fails. The error that reaches
  the caller carries a note (``__notes__``) naming the keys the layers had acted on.
- ``batch_items``'s ``patch`` operation has no mapping-write equivalent, so no layer's
  write logic can run for it; on a wrapped (or subclassed) store it is refused unless
  ``unguarded_patch=True``. Its key is still mapped and scope-checked.
- Returned documents (``replace_item``, batch results) are in stored (leaf) form.
"""

from __future__ import annotations

import json

from typing import Any, Callable, Iterable, Optional

from azure.cosmos import PartitionKey
from azure.cosmos.partition_key import NonePartitionKeyValue
from dol import inner_most_key

from cosmodol.base import batch as _batch, point_replace
from cosmodol.routing import RedirectError, redirect
from cosmodol.errors import (
    ContainerNotFoundError,
    DatabaseNotFoundError,
    ItemNotFoundError,
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


def _require_member(store, leaf, k, not_found: type[KeyError], base: type) -> None:
    """Check ``k in store`` through every wrapping layer, so a scope that a wrapper (or
    a subclass of ``base``) expresses only in ``__contains__`` is honoured. A plain
    ``base`` instance skips the round-trip (the operation itself fails on a missing
    resource)."""
    if store is leaf and type(leaf).__contains__ is base.__contains__:
        return
    if k not in store:
        raise not_found(k)


def _routed(store, leaf, method: str, handler: Callable, k, *v, what: str):
    """``store[k] = v`` (or ``del store[k]`` when no ``v``) through every wrapping layer,
    with the one resulting ``leaf.<method>`` call handed to ``handler`` (which receives the
    leaf's arguments: ``(k, v)`` or ``(k,)``, fully mapped). Returns what it returned.

    The redirect is pinned to ``k``'s leaf key, so a layer's write of any other key during
    the operation is refused rather than given this operation's semantics."""
    with redirect(leaf, method, _leaf_key(store, leaf, k), handler) as r:
        if v:
            store[k] = v[0]
        else:
            del store[k]
    if not r.claimed:
        raise RedirectError(
            f"{what}: the write of {k!r} did not reach the cosmodol store during the "
            "operation. A wrapping layer swallowed it, or performs it elsewhere (another "
            "thread, a write-behind buffer): then it may have been, or may later be, "
            "performed as an ordinary upsert or delete, without this operation's checks."
        )
    if r.error is not None:  # a layer caught it; the caller must still see it
        raise r.error
    return r.result


def _write_logic_above(store, leaf, base: type) -> bool:
    """Whether anything between the caller and ``base``'s own write methods could act
    on a write: a wrapping layer, or a subclass overriding ``__setitem__``/``__delitem__``."""
    return store is not leaf or any(
        getattr(type(leaf), m) is not getattr(base, m)
        for m in ("__setitem__", "__delitem__")
    )


# ---------------------------------------------------------------------------
# Item-level operations (CosmosItems)
# ---------------------------------------------------------------------------


def replace_item(store, k, v, *, etag: Optional[str] = None) -> dict:
    """Full replace of the existing item at key ``k``, optionally ETag-conditional.

    Unlike ``store[k] = v`` (an upsert), this fails with ``ItemNotFoundError`` if the item
    does not exist, and with a precondition error if ``etag`` no longer matches.

    Args:
        store: A ``CosmosItems``, or any ``dol`` wrapper around one. The write goes through
            ``store[k] = v``, so every layer maps, checks or refuses it as usual; only the
            leaf's upsert becomes a replace.
        k: The key, as ``store[k]`` would take it.
        v: The value, as ``store[k] = v`` would take it.
        etag: If given, only replace when the stored item's ``_etag`` still equals it.

    Returns:
        The stored document as Cosmos returned it (leaf form; system fields stripped if the
        leaf strips them).
    """
    leaf = _leaf_of(store, CosmosItems, fn="replace_item")
    _require_member(store, leaf, k, ItemNotFoundError, CosmosItems)

    def replace(leaf_k, leaf_v):
        body = leaf._prepare_body(leaf_k, leaf_v)
        item, hdrs = point_replace(
            leaf.container, leaf_k, body, leaf.partition_key_value, etag=etag
        )
        leaf._observe("replace", hdrs)
        return leaf._maybe_strip(item)

    return _routed(store, leaf, "__setitem__", replace, k, v, what="replace_item")


#: Operations ``batch_items`` accepts, and whether each carries a value.
BATCH_OPS = {
    "create": True,
    "upsert": True,
    "replace": True,
    "patch": True,  # the "value" is the list of patch operations, passed through as-is
    "read": False,
    "delete": False,
}


def batch_items(
    store, operations: Iterable[tuple], *, unguarded_patch: bool = False
) -> list[dict]:
    """Transactional batch within the store's partition, in the caller's key space.

    Each operation is ``(op, key)`` or ``(op, key, value)`` (``op`` one of
    ``BATCH_OPS``), optionally followed by a dict of per-operation SDK keyword arguments
    such as ``{"if_match_etag": etag}``::

        [("upsert", "a", {"v": 1}), ("replace", "b", {"v": 2}), ("delete", "c")]

    Each ``create``/``upsert``/``replace`` is collected through ``store[k] = v`` and each
    ``delete`` through ``del store[k]``, so every layer maps, checks or refuses it as
    usual; nothing is sent until all operations are collected, then they commit as one
    transaction. Bodies get the leaf's usual ``id`` / partition-key injection. On a
    wrapped store, every key a ``read``, ``replace``, ``patch`` or ``delete`` targets must
    be in the store (one point read each), unless an earlier ``create``/``upsert`` of the
    same batch makes it. ``read`` and ``patch`` keys are mapped with each layer's
    ``_id_of_key``; ``patch`` operations are refused on a wrapped or subclassed store
    unless ``unguarded_patch=True`` (no layer's write logic can run for a patch). The SDK's raw
    ``(op, args, kwargs)`` triples are not accepted here, because the ids inside them are
    opaque to any key mapping; use ``cosmodol.batch(container, triples, partition_key)``.
    """
    leaf = _leaf_of(store, CosmosItems, fn="batch_items")
    triples, created, acted = [], set(), []

    def collect_write(leaf_k, leaf_v):
        return leaf_k, leaf._prepare_body(leaf_k, leaf_v)

    def collect_key(leaf_k):
        if leaf.strict_keys:
            validate_cosmos_id(leaf_k)
        return leaf_k

    for operation in operations:
        op, k, *rest = operation
        if op not in BATCH_OPS:
            raise ValueError(
                f"Unknown batch op {op!r}; expected one of {list(BATCH_OPS)}."
            )
        if isinstance(k, tuple):
            raise ValueError(
                f"Batch op {operation!r} looks like an SDK (op, args, kwargs) triple. "
                "batch_items takes (op, key[, value][, kwargs]); for raw triples use "
                "cosmodol.batch(container, triples, partition_key)."
            )
        has_value = BATCH_OPS[op]
        kwargs = {}
        if len(rest) == has_value + 1 and isinstance(rest[-1], dict):
            *rest, kwargs = rest
        if len(rest) != has_value:
            shape = "(op, key, value)" if has_value else "(op, key)"
            raise ValueError(
                f"Batch op {op!r} takes {shape}[, kwargs]; got {operation!r}."
            )
        if (
            op == "patch"
            and not unguarded_patch
            and _write_logic_above(store, leaf, CosmosItems)
        ):
            raise TypeError(
                "batch_items refused a 'patch': this store is wrapped (or subclassed), "
                "and no wrapping layer's write logic (guards, hooks, caches) can run for "
                "a patch. Pass unguarded_patch=True to send it anyway."
            )
        if op in ("create", "upsert"):
            created.add(k)
        elif k not in created:
            _require_member(store, leaf, k, ItemNotFoundError, CosmosItems)
        what = f"batch_items {op!r}"
        if op in ("create", "upsert", "replace", "delete"):
            acted.append(k)
        if op in ("create", "upsert", "replace"):
            leaf_k, body = _routed(
                store, leaf, "__setitem__", collect_write, k, rest[0], what=what
            )
            args = (body,) if op != "replace" else (leaf_k, body)
        elif op == "delete":
            args = (_routed(store, leaf, "__delitem__", collect_key, k, what=what),)
        else:
            leaf_k = collect_key(_leaf_key(store, leaf, k))
            args = (leaf_k, rest[0]) if op == "patch" else (leaf_k,)
        triples.append((op, args, kwargs))
    try:
        return _batch(leaf.container, triples, leaf.partition_key_value)
    except Exception as e:
        if acted and store is not leaf and hasattr(e, "add_note"):
            e.add_note(
                "batch_items: the batch did not commit, but the wrapping layers had already "
                f"run their write logic for these keys: {acted!r}. A write-through cache, "
                "audit log or index they keep may now disagree with the container "
                "(i2mint/cosmodol#5)."
            )
        raise


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
            "partition() refused: this CosmosPartitionedItems is wrapped by a dol "
            "wrapper, and the child partition would silently lose those wrappers (key and value "
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

    Goes through ``db_store[name] = <the settings>``, so every layer maps, scopes or
    refuses the name as usual; the leaf creates the container from the arguments given
    here (the value a layer passes down is ignored).

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
    settings = dict(
        partition_key=PartitionKey(path=partition_key_path),
        offer_throughput=throughput,
        indexing_policy=indexing_policy,
        default_ttl=default_ttl,
        unique_key_policy=unique_key_policy,
        conflict_resolution_policy=conflict_resolution_policy,
        **extra,
    )

    def create(leaf_name, _settings):
        return leaf.database.create_container(id=leaf_name, **settings)

    return _routed(
        db_store, leaf, "__setitem__", create, name, settings, what="add_container"
    )


def _partition_key_expr(container) -> Optional[str]:
    """Cosmos SQL expression for the container's partition key: ``c["tenant"]``, or an
    array of them for a hierarchical (MultiHash) key, which the SDK takes as a list."""
    paths = (container.read().get("partitionKey") or {}).get("paths") or []

    def expr(path):
        return "c" + "".join(f"[{json.dumps(p)}]" for p in path.strip("/").split("/"))

    if len(paths) <= 1:
        return expr(paths[0]) if paths else None
    return "[" + ", ".join(map(expr, paths)) + "]"


def delete_container(db_store, name: str, *, force: bool = False) -> None:
    """Drop container ``name`` (in the caller's key space); ``force`` empties it first.

    Without ``force`` this is ``del db_store[name]``, which refuses a non-empty container.
    With ``force=True`` every item is deleted, one point delete each, and then the
    container is dropped, through ``del db_store[name]`` so every layer maps, scopes or
    refuses it as usual. Not recoverable without a Cosmos backup.
    """
    leaf = _leaf_of(db_store, CosmosDatabase, fn="delete_container")
    if not force:
        del db_store[name]
        return
    _require_member(db_store, leaf, name, ContainerNotFoundError, CosmosDatabase)

    def cascade(k):
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
            # A projection omits an undefined value: the item has no partition-key property.
            pk = item["pk"] if "pk" in item else NonePartitionKeyValue
            c.delete_item(item=item["id"], partition_key=pk)
        leaf.database.delete_container(k)

    _routed(
        db_store,
        leaf,
        "__delitem__",
        cascade,
        name,
        what="delete_container(force=True)",
    )


def add_database(
    account_store, name: str, *, throughput: Optional[int] = None, **extra
):
    """Create database ``name`` (in the caller's key space).

    Goes through ``account_store[name] = <the settings>`` (see ``add_container``).
    Returns the leaf's value for it (its ``database_factory`` output, a
    ``CosmosDatabase`` by default), not ``account_store[name]``: a wrapper that does not
    expose the new name would otherwise raise after the database was created.
    """
    leaf = _leaf_of(account_store, CosmosAccount, fn="add_database")
    settings = dict(offer_throughput=throughput, **extra)

    def create(leaf_name, _settings):
        return leaf.client.create_database(id=leaf_name, **settings)

    db = _routed(
        account_store, leaf, "__setitem__", create, name, settings, what="add_database"
    )
    return leaf.database_factory(db)


def delete_database(account_store, name: str, *, force: bool = False) -> None:
    """Drop database ``name`` (in the caller's key space); ``force`` drops its containers.

    Without ``force`` this is ``del account_store[name]``, which refuses a non-empty
    database. Not recoverable without a Cosmos backup.
    """
    leaf = _leaf_of(account_store, CosmosAccount, fn="delete_database")
    if not force:
        del account_store[name]
        return
    _require_member(account_store, leaf, name, DatabaseNotFoundError, CosmosAccount)

    def cascade(k):
        try:
            db = leaf.client.get_database_client(k)
            db.read()
        except Exception as e:
            raise DatabaseNotFoundError(k) from e
        for c in db.list_containers():
            db.delete_container(c["id"])
        leaf.client.delete_database(k)

    _routed(
        account_store,
        leaf,
        "__delitem__",
        cascade,
        name,
        what="delete_database(force=True)",
    )


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
