# cosmodol.stores

Mapping stores over a Cosmos DB container.

Two store flavors — pick one based on whether you have a fixed partition key value or
want to span the whole container:

- `CosmosItems` — fixed partition; keys are `id` strings; the simplest dict surface.
- `CosmosPartitionedItems` — whole container; keys are `(pk_value, id)` tuples.

See `misc/docs/architecture.md` Layer B and `misc/docs/design_decisions.md` §3.

### Classes

| [`CosmosItems`](#cosmodol.stores.CosmosItems)(container, \*, partition_key_value)   | `MutableMapping[str, dict]` over one fixed partition of a Cosmos container.   |
|----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`CosmosPartitionedItems`](#cosmodol.stores.CosmosPartitionedItems)(container, \*[, ...])      | `MutableMapping[tuple[str, str], dict]` over all partitions of a container.   |

### *class* cosmodol.stores.CosmosItems(container, , partition_key_value, partition_key_path=None, connection=None, inject_id=True, inject_partition_key=True, strict_keys=True, strip_system_fields=True, record_ru=None)

Bases: `KvPersister`

`MutableMapping[str, dict]` over one fixed partition of a Cosmos container.

Keys are item `id` strings; values are JSON-dict items. The store auto-injects
`id` and the partition-key property on writes.

* **Parameters:**
  * **container** (`Union`[`ContainerProxy`, [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)]) – An already-built `ContainerProxy` or a `(database, container)`
    tuple / `{"database": ..., "container": ...}` dict to resolve via
    `connection`.
  * **partition_key_value** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – The single partition-key value this store is scoped to.
  * **partition_key_path** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Path of the partition-key property in items (e.g. `"/_pk"`,
    `"/id"`). Read from the container if absent.
  * **connection** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – `CosmosConnection` (or anything `from_anything` accepts).
    Ignored if `container` is a `ContainerProxy`.
  * **inject_id** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – If True, auto-inject `"id"` into bodies on writes.
  * **inject_partition_key** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – If True, auto-inject the partition-key property into bodies.
  * **strict_keys** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Validate `id` chars + length on writes.
  * **strip_system_fields** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Strip `_etag/_ts/_rid/_self/_attachments` from returned items.
  * **record_ru** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`float`](https://docs.python.org/3/builtins/functions.html#float)], [`None`](https://docs.python.org/3/builtins/constants.html#None)]]) – Optional callback `(op_name, ru) -> None` invoked after each
    metal-layer op. Useful for Prometheus / logging.

#### query(sql, , parameters=None)

Run a SQL query scoped to this partition. Yields raw item dicts (no stripping).

SQL addresses the stored documents, so this speaks the leaf’s language on both
ends: ids in the results are Cosmos ids, and documents are as stored. Neither is
passed through any `dol` wrapper around this store.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### *class* cosmodol.stores.CosmosPartitionedItems(container, , partition_key_path=None, connection=None, inject_id=True, inject_partition_key=True, strict_keys=True, strip_system_fields=True, record_ru=None, len_via_query=False, silent_full_scan=False)

Bases: `KvPersister`

`MutableMapping[tuple[str, str], dict]` over all partitions of a container.

Keys are `(partition_key_value, id)` tuples. Use `CosmosItems` instead if you
have a fixed partition.

Iteration is cross-partition; the first call emits a `UserWarning` (silence with
`silent_full_scan=True`). `__len__` is **not** implemented by default; opt-in via
`len_via_query=True`. See `misc/docs/design_decisions.md` §§4, 6.

* **Parameters:**
  * **container** (`Union`[`ContainerProxy`, [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)]) – As in `CosmosItems`.
  * **partition_key_path** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Path of the partition-key property in items. Read from the
    container if absent.
  * **connection** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – As in `CosmosItems`.
  * **inject_id** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – As in `CosmosItems`.
  * **inject_partition_key** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – As in `CosmosItems`.
  * **strict_keys** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – As in `CosmosItems`.
  * **strip_system_fields** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – As in `CosmosItems`.
  * **record_ru** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`float`](https://docs.python.org/3/builtins/functions.html#float)], [`None`](https://docs.python.org/3/builtins/constants.html#None)]]) – As in `CosmosItems`.
  * **len_via_query** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – If True, `__len__` runs a (cross-partition) COUNT query.
  * **silent_full_scan** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – If True, `__iter__` does not emit the cross-partition warning.

#### query(sql, , parameters=None, partition_key=None, cross_partition=False)

Pass-through to `base.query`: raw Cosmos ids and documents, as for
`CosmosItems.query`; `partition_key` is a stored partition-key value.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]
