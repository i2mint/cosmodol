# cosmodol.base

Close-to-metal CRUD facade for Cosmos DB.

Functional layer over `ContainerProxy`. Each function takes the container plus
normalized args and returns `(value, ResponseHeaders)` so callers can observe RU
charges and ETags. The Mapping-shaped stores in `cosmodol.stores` route all their
work through these.

See `misc/docs/architecture.md` Layer A.

### Functions

| [`batch`](#cosmodol.base.batch)(container, operations, partition_key)     | Transactional batch within one logical partition.   |
|--------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| [`point_contains`](#cosmodol.base.point_contains)(container, id, partition_key)    | Existence check via point read.                     |
| [`point_delete`](#cosmodol.base.point_delete)(container, id, partition_key, \*)  | Point delete.                                       |
| [`point_get`](#cosmodol.base.point_get)(container, id, partition_key)         | Point read of one item.                             |
| [`point_replace`](#cosmodol.base.point_replace)(container, id, body, ...[, etag]) | Full replace of an item.                            |
| [`point_upsert`](#cosmodol.base.point_upsert)(container, body, \*[, etag])       | Insert-or-replace an item.                          |
| [`query`](#cosmodol.base.query)(container, sql, \*[, parameters, ...])    | Run a SQL query.                                    |

### Classes

| [`ResponseHeaders`](#cosmodol.base.ResponseHeaders)(request_charge, etag)   | Subset of Cosmos response headers we surface for observability.   |
|------------------------------------------------------------------------------------------|-------------------------------------------------------------------|

### *class* cosmodol.base.ResponseHeaders(request_charge, etag)

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

### cosmodol.base.batch(container, operations, partition_key)

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

### cosmodol.base.point_contains(container, id, partition_key)

Existence check via point read. `True/False`. Never raises `KeyError`.

Cheaper than a `SELECT VALUE COUNT(1)` query — ~1 RU vs ≥ 2.3 RU.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`bool`](https://docs.python.org/3/builtins/functions.html#bool), [`ResponseHeaders`](#cosmodol.base.ResponseHeaders)]

### cosmodol.base.point_delete(container, id, partition_key, , etag=None)

Point delete. Raises `ItemNotFoundError` on missing.

* **Return type:**
  [`ResponseHeaders`](#cosmodol.base.ResponseHeaders)

### cosmodol.base.point_get(container, id, partition_key)

Point read of one item. ~1 RU/KB. Raises `ItemNotFoundError` on missing.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`ResponseHeaders`](#cosmodol.base.ResponseHeaders)]

### cosmodol.base.point_replace(container, id, body, partition_key, , etag=None)

Full replace of an item. With `etag`, performs an If-Match conditional write.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`ResponseHeaders`](#cosmodol.base.ResponseHeaders)]

### cosmodol.base.point_upsert(container, body, , etag=None)

Insert-or-replace an item. `id` and the partition-key value are extracted from
`body`.

#### NOTE
`etag` parameter accepted for symmetry but Cosmos does not honor it on
`upsert_item`; use `point_replace` for ETag-conditional writes.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`ResponseHeaders`](#cosmodol.base.ResponseHeaders)]

### cosmodol.base.query(container, sql, , parameters=None, partition_key=None, cross_partition=False, max_item_count=None)

Run a SQL query. Yields dicts.

Either pass `partition_key=` (cheap, single-partition) or
`cross_partition=True` (RU scales with data). One of the two is required by Cosmos.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]
