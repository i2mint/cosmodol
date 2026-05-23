"""Mapping stores over a Cosmos DB container.

Two store flavors — pick one based on whether you have a fixed partition key value or
want to span the whole container:

- ``CosmosItems`` — fixed partition; keys are ``id`` strings; the simplest dict surface.
- ``CosmosPartitionedItems`` — whole container; keys are ``(pk_value, id)`` tuples.

See ``misc/docs/architecture.md`` Layer B and ``misc/docs/design_decisions.md`` §3.
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Iterator, Optional, Union

from azure.cosmos import ContainerProxy
from dol import KvPersister

from cosmodol.base import (
    ResponseHeaders,
    point_contains,
    point_delete,
    point_get,
    point_replace,
    point_upsert,
    query as _query,
    batch as _batch,
)
from cosmodol.connection import CosmosConnection
from cosmodol.errors import (
    KeyMismatchError,
    validate_cosmos_id,
)


# Cosmos system fields stripped from returned items when ``strip_system_fields=True``.
SYSTEM_FIELDS = ("_etag", "_ts", "_rid", "_self", "_attachments", "_lsn")


def _strip_system(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in SYSTEM_FIELDS}


def _resolve_container(
    container: Union[ContainerProxy, dict, tuple, Any],
    connection: Union[CosmosConnection, Any, None],
):
    """Polymorphic container resolution. Accepts:

    - A dict ``{"database": "...", "container": "..."}``.
    - A tuple ``("database", "container")``.
    - Anything else (including ``ContainerProxy`` and duck-typed test fakes like
      ``FakeContainerProxy``) is treated as an already-resolved container and used as-is.

    Uses ``connection`` (or a default ``CosmosConnection()``) to resolve when a dict or
    tuple is given.
    """
    if isinstance(container, dict):
        conn = CosmosConnection.from_anything(connection)
        return conn.container(container["database"], container["container"])
    if isinstance(container, tuple) and len(container) == 2:
        conn = CosmosConnection.from_anything(connection)
        return conn.container(*container)
    # Trust duck-typing for anything else (covers ContainerProxy and test fakes).
    return container


# ---------------------------------------------------------------------------
# CosmosItems — fixed partition
# ---------------------------------------------------------------------------


class CosmosItems(KvPersister):
    """``MutableMapping[str, dict]`` over one fixed partition of a Cosmos container.

    Keys are item ``id`` strings; values are JSON-dict items. The store auto-injects
    ``id`` and the partition-key property on writes.

    Args:
        container: An already-built ``ContainerProxy`` or a ``(database, container)``
            tuple / ``{"database": ..., "container": ...}`` dict to resolve via
            ``connection``.
        partition_key_value: The single partition-key value this store is scoped to.
        partition_key_path: Path of the partition-key property in items (e.g. ``"/_pk"``,
            ``"/id"``). Read from the container if absent.
        connection: ``CosmosConnection`` (or anything ``from_anything`` accepts).
            Ignored if ``container`` is a ``ContainerProxy``.
        inject_id: If True, auto-inject ``"id"`` into bodies on writes.
        inject_partition_key: If True, auto-inject the partition-key property into bodies.
        strict_keys: Validate ``id`` chars + length on writes.
        strip_system_fields: Strip ``_etag/_ts/_rid/_self/_attachments`` from returned items.
        record_ru: Optional callback ``(op_name, ru) -> None`` invoked after each
            metal-layer op. Useful for Prometheus / logging.
    """

    def __init__(
        self,
        container: Union[ContainerProxy, dict, tuple],
        *,
        partition_key_value: Any,
        partition_key_path: Optional[str] = None,
        connection: Any = None,
        inject_id: bool = True,
        inject_partition_key: bool = True,
        strict_keys: bool = True,
        strip_system_fields: bool = True,
        record_ru: Optional[Callable[[str, float], None]] = None,
    ):
        self.container = _resolve_container(container, connection)
        self.partition_key_value = partition_key_value
        self.partition_key_path = partition_key_path or _infer_pk_path(self.container)
        # Strip leading slash for property-name use (Cosmos uses "/pk" notation).
        self._pk_prop = self.partition_key_path.lstrip("/")
        self.inject_id = inject_id
        self.inject_partition_key = inject_partition_key
        self.strict_keys = strict_keys
        self.strip_system_fields = strip_system_fields
        self.record_ru = record_ru
        self.last_response_headers: Optional[ResponseHeaders] = None

    # ---- internal: bookkeeping ----

    def _observe(self, op: str, hdrs: ResponseHeaders) -> None:
        self.last_response_headers = hdrs
        if self.record_ru and hdrs.request_charge is not None:
            self.record_ru(op, hdrs.request_charge)

    @property
    def last_request_charge(self) -> Optional[float]:
        if self.last_response_headers is None:
            return None
        return self.last_response_headers.request_charge

    # ---- body preparation ----

    def _prepare_body(self, k: str, v) -> dict:
        if self.strict_keys:
            validate_cosmos_id(k)
        if not isinstance(v, dict):
            raise TypeError(
                f"CosmosItems values must be dicts; got {type(v).__name__}. "
                "Wrap with `dol.wrap_kvs(value_codec=...)` if you have a different value type."
            )
        body = dict(v)
        # id injection / validation
        if self.inject_id:
            existing = body.get("id")
            if existing is not None and existing != k:
                raise KeyMismatchError(
                    f"Body 'id'={existing!r} disagrees with key {k!r}. "
                    "Either omit 'id' from the body or pass inject_id=False."
                )
            body["id"] = k
        # partition_key injection / validation
        if self.inject_partition_key:
            existing = body.get(self._pk_prop)
            if existing is not None and existing != self.partition_key_value:
                raise KeyMismatchError(
                    f"Body {self._pk_prop!r}={existing!r} disagrees with store's "
                    f"partition_key_value={self.partition_key_value!r}. Either omit "
                    f"from the body or pass inject_partition_key=False."
                )
            body[self._pk_prop] = self.partition_key_value
        return body

    def _maybe_strip(self, item: dict) -> dict:
        return _strip_system(item) if self.strip_system_fields else item

    # ---- Mapping interface ----

    def __getitem__(self, k: str) -> dict:
        item, hdrs = point_get(self.container, k, self.partition_key_value)
        self._observe("get", hdrs)
        return self._maybe_strip(item)

    def __setitem__(self, k: str, v) -> None:
        body = self._prepare_body(k, v)
        _item, hdrs = point_upsert(self.container, body)
        self._observe("set", hdrs)

    def __delitem__(self, k: str) -> None:
        if self.strict_keys:
            validate_cosmos_id(k)
        hdrs = point_delete(self.container, k, self.partition_key_value)
        self._observe("del", hdrs)

    def __contains__(self, k: str) -> bool:
        if not isinstance(k, str):
            return False
        present, hdrs = point_contains(self.container, k, self.partition_key_value)
        self._observe("contains", hdrs)
        return present

    def __iter__(self) -> Iterator[str]:
        sql = "SELECT VALUE c.id FROM c"
        for row in _query(self.container, sql, partition_key=self.partition_key_value):
            # SELECT VALUE c.id yields raw strings, but the iter wraps with dict(...);
            # be defensive against both shapes.
            if isinstance(row, dict):
                yield row.get("id") or row.get("$1") or next(iter(row.values()))
            else:
                yield row

    def __len__(self) -> int:
        """Single-partition COUNT. Bounded by partition size (20 GB → tens of millions)."""
        sql = "SELECT VALUE COUNT(1) FROM c"
        for row in _query(self.container, sql, partition_key=self.partition_key_value):
            return (
                int(row) if not isinstance(row, dict) else int(next(iter(row.values())))
            )
        return 0

    def __repr__(self) -> str:
        return (
            f"CosmosItems(container={self.container.container_link!r}, "
            f"partition_key_value={self.partition_key_value!r})"
        )

    # ---- explicit methods (not Mapping-shaped) ----

    def replace(
        self,
        k: str,
        v: dict,
        *,
        etag: Optional[str] = None,
    ) -> dict:
        """Full replace with optional ETag-conditional write."""
        body = self._prepare_body(k, v)
        item, hdrs = point_replace(
            self.container, k, body, self.partition_key_value, etag=etag
        )
        self._observe("replace", hdrs)
        return self._maybe_strip(item)

    def query(
        self,
        sql: str,
        *,
        parameters: Optional[list[dict]] = None,
    ) -> Iterator[dict]:
        """Run a SQL query scoped to this partition. Yields raw item dicts (no stripping)."""
        return _query(
            self.container,
            sql,
            parameters=parameters,
            partition_key=self.partition_key_value,
        )

    def batch(self, operations: list[tuple]) -> list[dict]:
        """Transactional batch within this partition. See ``base.batch``."""
        return _batch(self.container, operations, self.partition_key_value)


def _infer_pk_path(container: ContainerProxy) -> str:
    """Read the container definition and return its partition_key path.

    Falls back to ``"/id"`` if the SDK can't resolve (e.g. on the emulator's container
    metadata when offline). Logs a warning when falling back.
    """
    try:
        props = container.read()
        paths = props.get("partitionKey", {}).get("paths", [])
        if paths:
            return paths[0]
    except Exception as e:
        warnings.warn(
            f"Could not read partition_key path from container ({type(e).__name__}: {e}); "
            "defaulting to '/id'. Pass partition_key_path=... explicitly to silence."
        )
    return "/id"


# ---------------------------------------------------------------------------
# CosmosPartitionedItems — whole container
# ---------------------------------------------------------------------------


class CosmosPartitionedItems(KvPersister):
    """``MutableMapping[tuple[str, str], dict]`` over all partitions of a container.

    Keys are ``(partition_key_value, id)`` tuples. Use ``CosmosItems`` instead if you
    have a fixed partition.

    Iteration is cross-partition; the first call emits a ``UserWarning`` (silence with
    ``silent_full_scan=True``). ``__len__`` is **not** implemented by default; opt-in via
    ``len_via_query=True``. See ``misc/docs/design_decisions.md`` §§4, 6.

    Args:
        container: As in ``CosmosItems``.
        partition_key_path: Path of the partition-key property in items. Read from the
            container if absent.
        connection: As in ``CosmosItems``.
        inject_id, inject_partition_key, strict_keys, strip_system_fields, record_ru:
            As in ``CosmosItems``.
        len_via_query: If True, ``__len__`` runs a (cross-partition) COUNT query.
        silent_full_scan: If True, ``__iter__`` does not emit the cross-partition warning.
    """

    def __init__(
        self,
        container: Union[ContainerProxy, dict, tuple],
        *,
        partition_key_path: Optional[str] = None,
        connection: Any = None,
        inject_id: bool = True,
        inject_partition_key: bool = True,
        strict_keys: bool = True,
        strip_system_fields: bool = True,
        record_ru: Optional[Callable[[str, float], None]] = None,
        len_via_query: bool = False,
        silent_full_scan: bool = False,
    ):
        self.container = _resolve_container(container, connection)
        self.partition_key_path = partition_key_path or _infer_pk_path(self.container)
        self._pk_prop = self.partition_key_path.lstrip("/")
        self.inject_id = inject_id
        self.inject_partition_key = inject_partition_key
        self.strict_keys = strict_keys
        self.strip_system_fields = strip_system_fields
        self.record_ru = record_ru
        self.len_via_query = len_via_query
        self.silent_full_scan = silent_full_scan
        self._warned = False
        self.last_response_headers: Optional[ResponseHeaders] = None

    # ---- internal ----

    def _observe(self, op: str, hdrs: ResponseHeaders) -> None:
        self.last_response_headers = hdrs
        if self.record_ru and hdrs.request_charge is not None:
            self.record_ru(op, hdrs.request_charge)

    @property
    def last_request_charge(self) -> Optional[float]:
        if self.last_response_headers is None:
            return None
        return self.last_response_headers.request_charge

    @staticmethod
    def _unpack_key(key) -> tuple[Any, str]:
        if not (isinstance(key, tuple) and len(key) == 2):
            raise TypeError(
                f"CosmosPartitionedItems keys must be (pk_value, id) tuples; got {key!r}"
            )
        return key[0], key[1]

    def _prepare_body(self, pk_value, id_, v) -> dict:
        if self.strict_keys:
            validate_cosmos_id(id_)
        if not isinstance(v, dict):
            raise TypeError(
                f"CosmosPartitionedItems values must be dicts; got {type(v).__name__}."
            )
        body = dict(v)
        if self.inject_id:
            existing = body.get("id")
            if existing is not None and existing != id_:
                raise KeyMismatchError(
                    f"Body 'id'={existing!r} disagrees with key id {id_!r}."
                )
            body["id"] = id_
        if self.inject_partition_key:
            existing = body.get(self._pk_prop)
            if existing is not None and existing != pk_value:
                raise KeyMismatchError(
                    f"Body {self._pk_prop!r}={existing!r} disagrees with key pk {pk_value!r}."
                )
            body[self._pk_prop] = pk_value
        return body

    def _maybe_strip(self, item: dict) -> dict:
        return _strip_system(item) if self.strip_system_fields else item

    # ---- Mapping interface ----

    def __getitem__(self, key):
        pk, id_ = self._unpack_key(key)
        item, hdrs = point_get(self.container, id_, pk)
        self._observe("get", hdrs)
        return self._maybe_strip(item)

    def __setitem__(self, key, v) -> None:
        pk, id_ = self._unpack_key(key)
        body = self._prepare_body(pk, id_, v)
        _item, hdrs = point_upsert(self.container, body)
        self._observe("set", hdrs)

    def __delitem__(self, key) -> None:
        pk, id_ = self._unpack_key(key)
        if self.strict_keys:
            validate_cosmos_id(id_)
        hdrs = point_delete(self.container, id_, pk)
        self._observe("del", hdrs)

    def __contains__(self, key) -> bool:
        if not (isinstance(key, tuple) and len(key) == 2):
            return False
        pk, id_ = key
        present, hdrs = point_contains(self.container, id_, pk)
        self._observe("contains", hdrs)
        return present

    def __iter__(self):
        if not self._warned and not self.silent_full_scan:
            warnings.warn(
                "Iterating a CosmosPartitionedItems is a cross-partition scan; RU cost "
                "scales with container size. Use .partition(pk_value) or .with_filter(...) "
                "to narrow, or pass `silent_full_scan=True` to suppress this warning.",
                UserWarning,
                stacklevel=2,
            )
            self._warned = True
        sql = f"SELECT c.id, c.{self._pk_prop} FROM c"
        for row in _query(self.container, sql, cross_partition=True):
            yield (row.get(self._pk_prop), row.get("id"))

    def __len__(self) -> int:
        if not self.len_via_query:
            # TypeError (not NotImplementedError) so ``list(self)`` and other length-hint
            # consumers fall back gracefully via ``operator.length_hint``. The error
            # message still directs the user to the opt-in flag.
            raise TypeError(
                "CosmosPartitionedItems.__len__ is disabled by default (cross-partition "
                "COUNT cost scales with cardinality). Pass `len_via_query=True` to opt in."
            )
        for row in _query(
            self.container, "SELECT VALUE COUNT(1) FROM c", cross_partition=True
        ):
            return (
                int(row) if not isinstance(row, dict) else int(next(iter(row.values())))
            )
        return 0

    def __repr__(self) -> str:
        return (
            f"CosmosPartitionedItems(container={self.container.container_link!r}, "
            f"partition_key_path={self.partition_key_path!r})"
        )

    # ---- narrowing helpers (sub-stores) ----

    def partition(self, pk_value) -> CosmosItems:
        """Narrow to a single partition; zero round-trips. Returns ``CosmosItems``."""
        return CosmosItems(
            self.container,
            partition_key_value=pk_value,
            partition_key_path=self.partition_key_path,
            inject_id=self.inject_id,
            inject_partition_key=self.inject_partition_key,
            strict_keys=self.strict_keys,
            strip_system_fields=self.strip_system_fields,
            record_ru=self.record_ru,
        )

    def query(
        self,
        sql: str,
        *,
        parameters: Optional[list[dict]] = None,
        partition_key: Any = None,
        cross_partition: bool = False,
    ) -> Iterator[dict]:
        """Pass-through to ``base.query``."""
        return _query(
            self.container,
            sql,
            parameters=parameters,
            partition_key=partition_key,
            cross_partition=cross_partition,
        )
