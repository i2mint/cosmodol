# cosmodol

Access Azure Cosmos DB (NoSQL/Core API) through a Mapping interface.

`cosmodol` exposes Azure Cosmos DB as `dol`-style `Mapping` /
`MutableMapping` interfaces, layered over the official `azure-cosmos` SDK.

Quick start:

```default
from cosmodol import cosmos_store

store = cosmos_store(
    connection_string="AccountEndpoint=https://localhost:8081/;AccountKey=...",
    database="mydb",
    container="mycontainer",
    partition_key_value="tenant-X",
)

store["k1"] = {"name": "Alice", "age": 30}
store["k1"]              # → {"id": "k1", "<pk>": "tenant-X", "name": "Alice", "age": 30}
"k1" in store            # → True
del store["k1"]
```

See `misc/docs/architecture.md` for the layered design.

If your Cosmos account was provisioned with the **MongoDB API**, use `pymongo` +
`mongodol` directly — this package only targets the NoSQL/Core API.

### Functions

| [`resolve_credential`](#cosmodol.resolve_credential)(\*[, credential, ...])         | Resolve a credential into a normalized form that can build a `CosmosClient`.    |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`batch`](#cosmodol.batch)(container, operations, partition_key)       | Transactional batch within one logical partition.                               |
| [`point_contains`](#cosmodol.point_contains)(container, id, partition_key)      | Existence check via point read.                                                 |
| [`point_delete`](#cosmodol.point_delete)(container, id, partition_key, \*)    | Point delete.                                                                   |
| [`point_get`](#cosmodol.point_get)(container, id, partition_key)           | Point read of one item.                                                         |
| [`point_replace`](#cosmodol.point_replace)(container, id, body, ...[, etag])   | Full replace of an item.                                                        |
| [`point_upsert`](#cosmodol.point_upsert)(container, body, \*[, etag])         | Insert-or-replace an item.                                                      |
| [`query`](#cosmodol.query)(container, sql, \*[, parameters, ...])      | Run a SQL query.                                                                |
| [`translate_cosmos_errors`](#cosmodol.translate_cosmos_errors)(\*[, key_arg, ...])       | Decorator: translate Cosmos SDK exceptions into `KeyError` subclasses.          |
| [`validate_cosmos_id`](#cosmodol.validate_cosmos_id)(k)                             | Raise `ValueError` if `k` is not a valid Cosmos item `id`.                      |
| [`add_container`](#cosmodol.add_container)(db_store, name, \*[, ...])          | Create a container named `name` (in the caller's key space) in `db_store`.      |
| [`add_database`](#cosmodol.add_database)(account_store, name, \*[, ...])      | Create database `name` (in the caller's key space).                             |
| [`batch_items`](#cosmodol.batch_items)(store, operations, \*[, ...])         | Transactional batch within the store's partition, in the caller's key space.    |
| [`delete_container`](#cosmodol.delete_container)(db_store, name, \*[, force])     | Drop container `name` (in the caller's key space); `force` empties it first.    |
| [`delete_database`](#cosmodol.delete_database)(account_store, name, \*[, force]) | Drop database `name` (in the caller's key space); `force` drops its containers. |
| [`partition`](#cosmodol.partition)(store, pk_value, \*[, wrap])            | Narrow a `CosmosPartitionedItems` to one partition; zero round-trips.           |
| [`replace_item`](#cosmodol.replace_item)(store, k, v, \*[, etag])             | Full replace of the existing item at key `k`, optionally ETag-conditional.      |
| [`cosmos_store`](#cosmodol.cosmos_store)(\*, database, container[, ...])      | Build a ready-to-use Cosmos store (`CosmosItems` or `CosmosPartitionedItems`).  |
| [`strip_system_fields`](#cosmodol.strip_system_fields)(item)                         | Return `item` with Cosmos system fields removed.                                |

### Classes

| [`CosmosConnection`](#cosmodol.CosmosConnection)([credential, ...])             | Holds a resolved credential and a lazy `CosmosClient`.                      |
|--------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| [`ResponseHeaders`](#cosmodol.ResponseHeaders)(request_charge, etag)           | Subset of Cosmos response headers we surface for observability.             |
| [`CosmosItems`](#cosmodol.CosmosItems)(container, \*, partition_key_value) | `MutableMapping[str, dict]` over one fixed partition of a Cosmos container. |
| [`CosmosPartitionedItems`](#cosmodol.CosmosPartitionedItems)(container, \*[, ...])    | `MutableMapping[tuple[str, str], dict]` over all partitions of a container. |
| [`CosmosAccount`](#cosmodol.CosmosAccount)([connection, database_factory])   | Mapping of database name → `CosmosDatabase`.                                |
| [`CosmosDatabase`](#cosmodol.CosmosDatabase)(database, \*[, connection, ...]) | Mapping of container name → `ContainerProxy` (or store-wrapped form).       |

### Exceptions

| [`ContainerNotEmptyError`](#cosmodol.ContainerNotEmptyError)   | Raised on `del db_store[name]` when the container has items.                                                      |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| [`ContainerNotFoundError`](#cosmodol.ContainerNotFoundError)   | Raised when a Cosmos container does not exist.                                                                    |
| [`CosmosThrottleError`](#cosmodol.CosmosThrottleError)      | Wraps `CosmosHttpResponseError` with HTTP 429 (RU exhaustion).                                                    |
| [`DatabaseNotEmptyError`](#cosmodol.DatabaseNotEmptyError)    | Raised on `del account_store[name]` when the database has containers.                                             |
| [`DatabaseNotFoundError`](#cosmodol.DatabaseNotFoundError)    | Raised when a Cosmos database does not exist.                                                                     |
| [`ItemAlreadyExistsError`](#cosmodol.ItemAlreadyExistsError)   | Raised on strict-create attempts when the item already exists.                                                    |
| [`ItemNotFoundError`](#cosmodol.ItemNotFoundError)        | Raised when an item does not exist for a (partition_key, id).                                                     |
| [`KeyMismatchError`](#cosmodol.KeyMismatchError)         | Raised when a written body's `id` (or partition-key value) disagrees with the inferred value (from the dict key). |

### *exception* cosmodol.ContainerNotEmptyError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Raised on `del db_store[name]` when the container has items. See
`misc/docs/design_decisions.md` §8.

### *exception* cosmodol.ContainerNotFoundError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised when a Cosmos container does not exist.

### *class* cosmodol.CosmosAccount(connection=None, , database_factory=None)

Bases: `KvReader`

Mapping of database name → `CosmosDatabase`.

`__setitem__` is disabled — use `cosmodol.add_database(account_store, name, ...)`.
`__delitem__` refuses non-empty databases; use
`cosmodol.delete_database(account_store, name, force=True)`.

### *class* cosmodol.CosmosConnection(credential=None, connection_string=None, endpoint=None, key=None, consistency_level=None, client_kwargs=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Holds a resolved credential and a lazy `CosmosClient`.

This is the dependency-injection seam for the package. Tests construct one pointing
at the emulator without touching any store class.

* **Parameters:**
  * **credential** (`Union`[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`None`](https://docs.python.org/3/builtins/constants.html#None)]) – explicit credential object or account-key string.
  * **connection_string** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – full `AccountEndpoint=...;AccountKey=...` string.
  * **endpoint** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Cosmos endpoint URL (with or without credential).
  * **key** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – account master key.
  * **consistency_level** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – optional override; defaults to inheriting the account default.
  * **client_kwargs** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)) – extra kwargs forwarded to `CosmosClient`.

#### *property* client *: CosmosClient*

The lazily-constructed `CosmosClient`. Cached for the connection’s lifetime.

#### *classmethod* from_anything(source)

Convenience: build a `CosmosConnection` from a thing-or-spec.

Accepts:

> - `CosmosConnection` (returned as-is)
> - `CosmosClient` (wrapped without further resolution)
> - `str` (connection string)
> - `dict` (passed as kwargs)
> - `None` (defer to env / AAD)
* **Return type:**
  [`CosmosConnection`](cosmodol.connection.html.md#cosmodol.connection.CosmosConnection)

### *class* cosmodol.CosmosDatabase(database, , connection=None, store_factory=None)

Bases: `KvReader`

Mapping of container name → `ContainerProxy` (or store-wrapped form).

`__setitem__` is disabled — use `cosmodol.add_container(db_store, name, ...)`.
`__delitem__` refuses non-empty containers; use
`cosmodol.delete_container(db_store, name, force=True)`.

* **Parameters:**
  * **database** (`Union`[`DatabaseProxy`, [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Either an existing `DatabaseProxy`, a database name string (resolved
    via `connection`), or `None` (use `connection.database_name`).
  * **connection** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – `CosmosConnection` (or anything `from_anything` accepts).
  * **store_factory** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)]) – Optional callable `(ContainerProxy) -> Mapping` used to wrap
    the value returned by `__getitem__`. Defaults to passing the
    `ContainerProxy` through.

### *class* cosmodol.CosmosItems(container, , partition_key_value, partition_key_path=None, connection=None, inject_id=True, inject_partition_key=True, strict_keys=True, strip_system_fields=True, record_ru=None)

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

### *class* cosmodol.CosmosPartitionedItems(container, , partition_key_path=None, connection=None, inject_id=True, inject_partition_key=True, strict_keys=True, strip_system_fields=True, record_ru=None, len_via_query=False, silent_full_scan=False)

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

### *exception* cosmodol.CosmosThrottleError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Wraps `CosmosHttpResponseError` with HTTP 429 (RU exhaustion).

### *exception* cosmodol.DatabaseNotEmptyError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Raised on `del account_store[name]` when the database has containers. See
`misc/docs/design_decisions.md` §8.

### *exception* cosmodol.DatabaseNotFoundError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised when a Cosmos database does not exist.

### *exception* cosmodol.ItemAlreadyExistsError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised on strict-create attempts when the item already exists.

### *exception* cosmodol.ItemNotFoundError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised when an item does not exist for a (partition_key, id).

### *exception* cosmodol.KeyMismatchError

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

Raised when a written body’s `id` (or partition-key value) disagrees with the
inferred value (from the dict key). See `misc/docs/design_decisions.md` §9.

### *class* cosmodol.ResponseHeaders(request_charge, etag)

Bases: [`NamedTuple`](https://docs.python.org/3/library/typing.html#typing.NamedTuple)

Subset of Cosmos response headers we surface for observability.

#### request_charge

RU consumed by the operation. `None` when the emulator (which
does not populate this header) was the backend.

#### etag

ETag of the (created / read / replaced) item, when applicable.

#### etag *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Alias for field number 1

#### request_charge *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Alias for field number 0

### cosmodol.add_container(db_store, name, , partition_key_path='/id', throughput=None, indexing_policy=None, default_ttl=None, unique_key_policy=None, conflict_resolution_policy=None, \*\*extra)

Create a container named `name` (in the caller’s key space) in `db_store`.

Goes through `db_store[name] = <the settings>`, so every layer maps, scopes or
refuses the name as usual; the leaf creates the container from the arguments given
here (the value a layer passes down is ignored).

* **Parameters:**
  * **db_store** – A `CosmosDatabase`, or a `dol` wrapper around one.
  * **name** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Container name, as `db_store[name]` would take it.
  * **partition_key_path** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Partition-key path (e.g. `"/id"`, `"/tenantId"`).
    Defaults to `"/id"` per `misc/docs/design_decisions.md` §3.
  * **throughput** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Provisioned RU/s (e.g. 400). `None` uses the database’s
    shared throughput if any, otherwise the account default.
  * **indexing_policy** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]) – Standard Cosmos container kwargs.
  * **default_ttl** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Standard Cosmos container kwargs.
  * **unique_key_policy** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]) – Standard Cosmos container kwargs.
  * **conflict_resolution_policy** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]) – Standard Cosmos container kwargs.
  * **\*\*extra** – Forwarded to `DatabaseProxy.create_container`.
* **Returns:**
  The SDK’s `ContainerProxy`.

### cosmodol.add_database(account_store, name, , throughput=None, \*\*extra)

Create database `name` (in the caller’s key space).

Goes through `account_store[name] = <the settings>` (see `add_container`).
Returns the leaf’s value for it (its `database_factory` output, a
`CosmosDatabase` by default), not `account_store[name]`: a wrapper that does not
expose the new name would otherwise raise after the database was created.

### cosmodol.batch(container, operations, partition_key)

Transactional batch within one logical partition.

* **Parameters:**
  * **operations** ([`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)]) – List of `(op_name, args_tuple, kwargs_dict)` triples. Cosmos op
    names: `"create"`, `"upsert"`, `"replace"`, `"patch"`, `"read"`,
    `"delete"`. ≤ 100 ops, ≤ 1.2 MB total.
  * **partition_key** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – All ops must share this partition-key value.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]
* **Returns:**
  List of per-op result dicts as returned by the SDK.

### cosmodol.batch_items(store, operations, , unguarded_patch=False)

Transactional batch within the store’s partition, in the caller’s key space.

Each operation is `(op, key)` or `(op, key, value)` (`op` one of
`BATCH_OPS`), optionally followed by a dict of per-operation SDK keyword arguments
such as `{"if_match_etag": etag}`:

```default
[("upsert", "a", {"v": 1}), ("replace", "b", {"v": 2}), ("delete", "c")]
```

Each `create`/`upsert`/`replace` is collected through `store[k] = v` and each
`delete` through `del store[k]`, so every layer maps, checks or refuses it as
usual; nothing is sent until all operations are collected, then they commit as one
transaction. Bodies get the leaf’s usual `id` / partition-key injection. On a
wrapped store, every key a `read`, `replace`, `patch` or `delete` targets must
be in the store (one point read each), unless an earlier `create`/`upsert` of the
same batch makes it. `read` and `patch` keys are mapped with each layer’s
`_id_of_key`; `patch` operations are refused on a wrapped or subclassed store
unless `unguarded_patch=True` (no layer’s write logic can run for a patch). The SDK’s raw
`(op, args, kwargs)` triples are not accepted here, because the ids inside them are
opaque to any key mapping; use `cosmodol.batch(container, triples, partition_key)`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### cosmodol.cosmos_store(, database, container, connection=None, credential=None, connection_string=None, endpoint=None, key=None, partition_key_value=None, partition_key_path=None, value_codec=None, strip_system_fields=True)

Build a ready-to-use Cosmos store (`CosmosItems` or `CosmosPartitionedItems`).

The store flavor is chosen based on which partition-key kwarg is passed:

- `partition_key_value=...`  → `CosmosItems` (keys = id strings, fixed partition).
- `partition_key_path=...`   → `CosmosPartitionedItems` (keys = (pk, id) tuples).
- Neither given                → raises `ValueError`.

* **Parameters:**
  * **database** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Database name.
  * **container** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Container name.
  * **connection** (`Union`[[`CosmosConnection`](cosmodol.connection.html.md#cosmodol.connection.CosmosConnection), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`None`](https://docs.python.org/3/builtins/constants.html#None)]) – A `CosmosConnection` (or anything `from_anything` accepts) to
    reuse a client. Mutually exclusive with explicit credential kwargs.
  * **credential** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – Forwarded to `CosmosConnection`
    when `connection` is None.
  * **connection_string** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Forwarded to `CosmosConnection`
    when `connection` is None.
  * **endpoint** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Forwarded to `CosmosConnection`
    when `connection` is None.
  * **key** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Forwarded to `CosmosConnection`
    when `connection` is None.
  * **partition_key_value** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – Fixed partition key value → returns `CosmosItems`.
  * **partition_key_path** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Partition key property path → returns `CosmosPartitionedItems`.
  * **value_codec** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)]) – A decorator that, given a class, returns a wrapped class. Typically
    a partially-applied `dol.wrap_kvs(...)`.
  * **strip_system_fields** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – Strip Cosmos system fields from returned items.
* **Returns:**
  A `CosmosItems` or `CosmosPartitionedItems` instance, possibly codec-wrapped.

### cosmodol.delete_container(db_store, name, , force=False)

Drop container `name` (in the caller’s key space); `force` empties it first.

Without `force` this is `del db_store[name]`, which refuses a non-empty container.
With `force=True` every item is deleted, one point delete each, and then the
container is dropped, through `del db_store[name]` so every layer maps, scopes or
refuses it as usual. Not recoverable without a Cosmos backup.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### cosmodol.delete_database(account_store, name, , force=False)

Drop database `name` (in the caller’s key space); `force` drops its containers.

Without `force` this is `del account_store[name]`, which refuses a non-empty
database. Not recoverable without a Cosmos backup.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### cosmodol.partition(store, pk_value, , wrap=None)

Narrow a `CosmosPartitionedItems` to one partition; zero round-trips.

On an unwrapped store this returns a `CosmosItems` sharing the parent’s settings.

A wrapped parent’s transforms cannot be carried onto the child automatically (the
parent’s keys are `(pk, id)` tuples, the child’s are ids), so a wrapped store is
refused unless you say how to wrap the child:

```default
sub = partition(redacted_store, "tenant-1", wrap=my_value_codec)
```

* **Parameters:**
  * **store** – A `CosmosPartitionedItems`, or a `dol` wrapper around one.
  * **pk_value** – The partition-key value, as stored (not passed through any key codec).
  * **wrap** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)]) – Applied to the child store before it is returned. Required when `store`
    is wrapped; `wrap=lambda s: s` explicitly asks for the bare child.
* **Return type:**
  [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)

### cosmodol.point_contains(container, id, partition_key)

Existence check via point read. `True/False`. Never raises `KeyError`.

Cheaper than a `SELECT VALUE COUNT(1)` query — ~1 RU vs ≥ 2.3 RU.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`bool`](https://docs.python.org/3/builtins/functions.html#bool), [`ResponseHeaders`](cosmodol.base.html.md#cosmodol.base.ResponseHeaders)]

### cosmodol.point_delete(container, id, partition_key, , etag=None)

Point delete. Raises `ItemNotFoundError` on missing.

* **Return type:**
  [`ResponseHeaders`](cosmodol.base.html.md#cosmodol.base.ResponseHeaders)

### cosmodol.point_get(container, id, partition_key)

Point read of one item. ~1 RU/KB. Raises `ItemNotFoundError` on missing.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`ResponseHeaders`](cosmodol.base.html.md#cosmodol.base.ResponseHeaders)]

### cosmodol.point_replace(container, id, body, partition_key, , etag=None)

Full replace of an item. With `etag`, performs an If-Match conditional write.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`ResponseHeaders`](cosmodol.base.html.md#cosmodol.base.ResponseHeaders)]

### cosmodol.point_upsert(container, body, , etag=None)

Insert-or-replace an item. `id` and the partition-key value are extracted from
`body`.

#### NOTE
`etag` parameter accepted for symmetry but Cosmos does not honor it on
`upsert_item`; use `point_replace` for ETag-conditional writes.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`ResponseHeaders`](cosmodol.base.html.md#cosmodol.base.ResponseHeaders)]

### cosmodol.query(container, sql, , parameters=None, partition_key=None, cross_partition=False, max_item_count=None)

Run a SQL query. Yields dicts.

Either pass `partition_key=` (cheap, single-partition) or
`cross_partition=True` (RU scales with data). One of the two is required by Cosmos.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### cosmodol.replace_item(store, k, v, , etag=None)

Full replace of the existing item at key `k`, optionally ETag-conditional.

Unlike `store[k] = v` (an upsert), this fails with `ItemNotFoundError` if the item
does not exist, and with a precondition error if `etag` no longer matches.

* **Parameters:**
  * **store** – A `CosmosItems`, or any `dol` wrapper around one. The write goes through
    `store[k] = v`, so every layer maps, checks or refuses it as usual; only the
    leaf’s upsert becomes a replace.
  * **k** – The key, as `store[k]` would take it.
  * **v** – The value, as `store[k] = v` would take it.
  * **etag** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – If given, only replace when the stored item’s `_etag` still equals it.
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
* **Returns:**
  The stored document as Cosmos returned it (leaf form; system fields stripped if the
  leaf strips them).

### cosmodol.resolve_credential(, credential=None, connection_string=None, endpoint=None, key=None)

Resolve a credential into a normalized form that can build a `CosmosClient`.

Cascade (first hit wins):

1. Explicit `credential=` (with `endpoint=` for the URL)
2. Explicit `connection_string=`
3. Explicit `endpoint=` + `key=` (or just `endpoint=` + AAD)
4. Env var `AZURE_COSMOS_CONNECTION_STRING`
5. Env vars `AZURE_COSMOS_ENDPOINT` + `AZURE_COSMOS_KEY`
6. Env var `AZURE_COSMOS_ENDPOINT` alone + `DefaultAzureCredential`

* **Returns:**
  `{"url": "...", "credential": <obj>}`
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – if no source resolves.

### cosmodol.strip_system_fields(item)

Return `item` with Cosmos system fields removed.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### cosmodol.translate_cosmos_errors(\*, key_arg=0, not_found_cls=<class 'cosmodol.errors.ItemNotFoundError'>, exists_cls=<class 'cosmodol.errors.ItemAlreadyExistsError'>)

Decorator: translate Cosmos SDK exceptions into `KeyError` subclasses.

Auth errors and any other Cosmos errors propagate untouched. HTTP 429 throttling is
wrapped in `CosmosThrottleError` so callers can distinguish it. See
`misc/docs/design_decisions.md` §11.

* **Parameters:**
  * **key_arg** ([`int`](https://docs.python.org/3/builtins/functions.html#int) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Position (int) or name (str) of the key argument in the wrapped
    method’s signature. Used to populate `KeyError(key)`. Default 0; for
    methods the user-facing key is typically at index 1 (`self` at 0).
  * **not_found_cls** ([`type`](https://docs.python.org/3/builtins/functions.html#type)[[`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)]) – Exception class to raise on `CosmosResourceNotFoundError`.
  * **exists_cls** ([`type`](https://docs.python.org/3/builtins/functions.html#type)[[`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)]) – Exception class to raise on `CosmosResourceExistsError`.
* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)

### cosmodol.validate_cosmos_id(k)

Raise `ValueError` if `k` is not a valid Cosmos item `id`.

Cosmos may accept invalid ids on write but the item then becomes unreachable from
the SDK. We fail loudly at write time. See `misc/docs/cosmos_db_reference.md`
§”id rules”.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### Modules

| [`base`](cosmodol.base.html.md#module-cosmodol.base)             | Close-to-metal CRUD facade for Cosmos DB.                                            |
|----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| [`connection`](cosmodol.connection.html.md#module-cosmodol.connection) | Connection-layer for cosmodol.                                                       |
| [`errors`](cosmodol.errors.html.md#module-cosmodol.errors)         | Custom exceptions and error-translation decorator for cosmodol.                      |
| [`ops`](cosmodol.ops.html.md#module-cosmodol.ops)               | Key-taking operations, as free functions over the caller's (possibly wrapped) store. |
| [`recipes`](cosmodol.recipes.html.md#module-cosmodol.recipes)       | Convenience wrappers and factories for cosmodol.                                     |
| [`routing`](cosmodol.routing.html.md#module-cosmodol.routing)       | Route an operation through a wrapped store's own `__setitem__`/`__delitem__`.        |
| [`stores`](cosmodol.stores.html.md#module-cosmodol.stores)         | Mapping stores over a Cosmos DB container.                                           |
| [`testing`](cosmodol.testing.html.md#module-cosmodol.testing)       | Testing helpers for cosmodol — emulator fixture and a minimal in-memory fake.        |
| [`trees`](cosmodol.trees.html.md#module-cosmodol.trees)           | Tree-shaped stores: mapping-of-databases and mapping-of-containers.                  |
