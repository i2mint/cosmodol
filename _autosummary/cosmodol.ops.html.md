# cosmodol.ops

Key-taking operations, as free functions over the caller’s (possibly wrapped) store.

Why free functions and not methods: `dol` wraps stores by delegation, so a *method* of a
wrapped store runs bound to the inner leaf and receives the caller’s **outer**, unmapped key
(i2mint/cosmodol#1, i2mint/dol#83). Under a key codec that silently replaced, batch-wrote,
cascade-deleted or created the wrong resource. A free function is handed the outer store
itself, so it can resolve the key through every wrapping layer before touching Cosmos:

```pycon
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
```

The same design is s3dol’s ADR-0011 (keyed capabilities leave the store’s method surface;
destructive ones become free functions). The removed methods now raise an
`AttributeError` that names their replacement here.

How a function honours the wrapper (i2mint/cosmodol#3): every write goes through the
caller’s store itself. A function performs the plain mapping write (`store[k] = v` or
`del store[k]`) on the outer store, after telling the cosmodol leaf, through
`cosmodol.routing`, how to execute the one write that reaches it (a conditional replace,
a batch entry, a cascade, a create). So each layer runs exactly as it does for that
mapping write:

- **Key and value mapping**, `preset=` hooks, `ValueCodecs`: applied by the layers
  themselves, class- and instance-wraps alike.
- **Write guards**: a layer whose `__setitem__`/`__delitem__` refuses (a read-only
  wrapper, `dol.disable_setitem`) refuses the operation too.
- **Scope**: a `filt_iter` scope refuses out-of-scope keys in its `__setitem__` /
  `__delitem__`, now including keys an operation *creates* (batch `create`/`upsert`,
  `add_container`, `add_database`). Operations on *existing* resources also check
  `k in store` through the outer store first (one point read), for wrappers that scope
  only by `__contains__`.
- **Write-through caches** see the value they would see for `store[k] = v`. A cache
  that only fills on reads (`dol.cache_vals`) is stale after any write, mapping or not.
- **A layer that writes another key first** (an index, an audit record), \*\*writes twice,
  or swallows the write\*\* makes the function raise `cosmodol.routing.RedirectError`;
  the leaf refuses any write but the expected one, before acting. An error a layer
  catches is re-raised to the caller.

What remains outside a function’s reach, stated rather than hidden (s3dol ADR-0011 D3a):

- A layer that performs the write in another thread or later (a write-behind buffer)
  escapes the redirect: that write is the leaf’s ordinary upsert or delete, without the
  operation’s semantics (no ETag check, outside a batch’s transaction). The function
  raises `RedirectError`, but cannot undo it. A write of the *same key* from another
  thread or task while the operation is pending is refused (`ConcurrentWriteError`, a
  `RedirectError`), so a layer that writes from a thread the operation waits for cannot
  slip past it. What escapes: a write that lands after the operation has finished, and a
  write through a different leaf object on the same container (`partition()` child, a
  second `CosmosItems`).
- `batch_items` collects its writes through the layers first and commits them as one
  transaction afterwards: a layer that acts after the inner write returns (a write-through
  cache, an audit log) has acted even if the transaction then fails. The error that reaches
  the caller carries a note (`__notes__`) naming the keys the layers had acted on.
- `batch_items`’s `patch` operation has no mapping-write equivalent, so no layer’s
  write logic can run for it; on a wrapped (or subclassed) store it is refused unless
  `unguarded_patch=True`. Its key is still mapped and scope-checked.
- Returned documents (`replace_item`, batch results) are in stored (leaf) form.

### Module Attributes

| [`BATCH_OPS`](#cosmodol.ops.BATCH_OPS)   | Operations `batch_items` accepts, and whether each carries a value.   |
|--------------------------------------------------------------|-----------------------------------------------------------------------|

### Functions

| [`replace_item`](#cosmodol.ops.replace_item)(store, k, v, \*[, etag])             | Full replace of the existing item at key `k`, optionally ETag-conditional.      |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`batch_items`](#cosmodol.ops.batch_items)(store, operations, \*[, ...])         | Transactional batch within the store's partition, in the caller's key space.    |
| [`partition`](#cosmodol.ops.partition)(store, pk_value, \*[, wrap])            | Narrow a `CosmosPartitionedItems` to one partition; zero round-trips.           |
| [`add_container`](#cosmodol.ops.add_container)(db_store, name, \*[, ...])          | Create a container named `name` (in the caller's key space) in `db_store`.      |
| [`delete_container`](#cosmodol.ops.delete_container)(db_store, name, \*[, force])     | Drop container `name` (in the caller's key space); `force` empties it first.    |
| [`add_database`](#cosmodol.ops.add_database)(account_store, name, \*[, ...])      | Create database `name` (in the caller's key space).                             |
| [`delete_database`](#cosmodol.ops.delete_database)(account_store, name, \*[, force]) | Drop database `name` (in the caller's key space); `force` drops its containers. |

### cosmodol.ops.BATCH_OPS *= {'create': True, 'delete': False, 'patch': True, 'read': False, 'replace': True, 'upsert': True}*

Operations `batch_items` accepts, and whether each carries a value.

### cosmodol.ops.add_container(db_store, name, , partition_key_path='/id', throughput=None, indexing_policy=None, default_ttl=None, unique_key_policy=None, conflict_resolution_policy=None, \*\*extra)

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

### cosmodol.ops.add_database(account_store, name, , throughput=None, \*\*extra)

Create database `name` (in the caller’s key space).

Goes through `account_store[name] = <the settings>` (see `add_container`).
Returns the leaf’s value for it (its `database_factory` output, a
`CosmosDatabase` by default), not `account_store[name]`: a wrapper that does not
expose the new name would otherwise raise after the database was created.

### cosmodol.ops.batch_items(store, operations, , unguarded_patch=False)

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

### cosmodol.ops.delete_container(db_store, name, , force=False)

Drop container `name` (in the caller’s key space); `force` empties it first.

Without `force` this is `del db_store[name]`, which refuses a non-empty container.
With `force=True` every item is deleted, one point delete each, and then the
container is dropped, through `del db_store[name]` so every layer maps, scopes or
refuses it as usual. Not recoverable without a Cosmos backup.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### cosmodol.ops.delete_database(account_store, name, , force=False)

Drop database `name` (in the caller’s key space); `force` drops its containers.

Without `force` this is `del account_store[name]`, which refuses a non-empty
database. Not recoverable without a Cosmos backup.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### cosmodol.ops.partition(store, pk_value, , wrap=None)

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

### cosmodol.ops.replace_item(store, k, v, , etag=None)

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
