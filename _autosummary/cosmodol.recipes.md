# cosmodol.recipes

Convenience wrappers and factories for cosmodol.

Layer C of the architecture. Built only by composition over Layer B
(`cosmodol.stores` / `cosmodol.trees`). See `misc/docs/architecture.md` Layer C.

### Functions

| [`cosmos_store`](#cosmodol.recipes.cosmos_store)(\*, database, container[, ...])   | Build a ready-to-use Cosmos store (`CosmosItems` or `CosmosPartitionedItems`).   |
|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`strip_system_fields`](#cosmodol.recipes.strip_system_fields)(item)                      | Return `item` with Cosmos system fields removed.                                 |

### cosmodol.recipes.cosmos_store(, database, container, connection=None, credential=None, connection_string=None, endpoint=None, key=None, partition_key_value=None, partition_key_path=None, value_codec=None, strip_system_fields=True)

Build a ready-to-use Cosmos store (`CosmosItems` or `CosmosPartitionedItems`).

The store flavor is chosen based on which partition-key kwarg is passed:

- `partition_key_value=...`  → `CosmosItems` (keys = id strings, fixed partition).
- `partition_key_path=...`   → `CosmosPartitionedItems` (keys = (pk, id) tuples).
- Neither given                → raises `ValueError`.

* **Parameters:**
  * **database** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Database name.
  * **container** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Container name.
  * **connection** (`Union`[[`CosmosConnection`](cosmodol.connection.md#cosmodol.connection.CosmosConnection), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`None`](https://docs.python.org/3/builtins/constants.html#None)]) – A `CosmosConnection` (or anything `from_anything` accepts) to
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

### cosmodol.recipes.strip_system_fields(item)

Return `item` with Cosmos system fields removed.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
