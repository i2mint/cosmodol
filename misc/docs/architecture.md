# cosmodol — Architecture

`cosmodol` exposes **Azure Cosmos DB** (NoSQL/Core API) as `dol`-style `Mapping` /
`MutableMapping` interfaces.

This document is the single source of truth for the package's layering. It is read by
contributors and by AI agents (see `cosmodol/.claude/skills/cosmodol/SKILL.md`, which is
generated from this doc and from `design_decisions.md`).

For the underlying SDK / service reference, see [cosmos_db_reference.md](cosmos_db_reference.md).
For the *why* behind every defaulted choice, see [design_decisions.md](design_decisions.md).

---

## Goals

1. **Pythonic.** `store[key]`, `store[key] = value`, `del store[key]`, `key in store`, `for key in store:` — that is the surface. Anything richer (queries, batches, ETag conditions) is reachable via explicit methods, not by overloading `__getitem__`.
2. **Cheap by default.** Every Mapping op on a single item is one **point read / write / delete** (≈1–10 RU). No method silently triggers a cross-partition scan; methods that *could* require it (`__iter__`, `__len__`) are scoped, opt-in, or unimplemented.
3. **Faithful to Cosmos.** Partition keys are non-negotiable in Cosmos and we surface them in three idiomatic shapes (see Layer B). ETag-based optimistic concurrency, RU observability, and SQL queries are first-class accessors.
4. **Composable with `dol`.** Once you have a close-to-metal store of `dict` values, layering JSON/pickle/binary codecs, key transforms, and caching is one line of `wrap_kvs`.
5. **Testable without a cloud.** Tests run against the Linux vNext emulator (`mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview`), with a `dict`-backed fake `ContainerProxy` for unit-level tests.

## Hard constraints that shape the design

These come from Cosmos itself and force design choices that look unusual against `mongodol` or `chromadol`:

- **Partition key is mandatory** on every `read_item`/`replace_item`/`delete_item`/`patch_item`/`execute_item_batch`. The SDK rejects calls without it.
- **`id` is always a string**, ≤ 255 chars, must avoid `/ \ ? #`. Cosmos may accept writes with forbidden chars but the item then becomes unreachable.
- **Item uniqueness is per logical partition**, not per container. `(pk, id)` is the actual primary key.
- **Cross-partition scans cost RU proportional to data**, not result size. `__iter__` and `__len__` on a large container are economic land mines.
- **Partition-key values are immutable.** No "move".
- **`__len__` via `SELECT VALUE COUNT(1)`** scales with cardinality and is cross-partition by default. We don't enable it without an explicit opt-in.

## The three layers

```
┌──────────────────────────────────────────────────────────────┐
│  Layer C — convenience wrappers (cosmodol.recipes)           │
│  Codec layers, cosmos_store(...) one-call factory,           │
│  CosmosJsonStore (default), CosmosPickleStore, …             │
├──────────────────────────────────────────────────────────────┤
│  Layer B — Mapping stores (cosmodol.stores, cosmodol.trees)  │
│    Item-level:  CosmosItems, CosmosPartitionedItems          │
│    Tree-level:  CosmosDatabase (containers),                 │
│                 CosmosAccount  (databases)                   │
├──────────────────────────────────────────────────────────────┤
│  Layer A — close-to-metal CRUD facade (cosmodol.base)        │
│  get/upsert/replace/delete/point_contains/query helpers      │
│  RU header surfacing, error translation, etag handling       │
├──────────────────────────────────────────────────────────────┤
│  Layer 0 — connection (cosmodol.connection)                  │
│  Credential resolution, CosmosClient cached_property         │
├──────────────────────────────────────────────────────────────┤
│       azure-cosmos SDK (CosmosClient → Database → Container) │
└──────────────────────────────────────────────────────────────┘
```

Note the extra connection layer ("Layer 0") below the metal CRUD layer — same pattern as
in `azuredol`, kept separate because `CosmosClient` is the expensive resource.

### Layer 0 — Connection (`cosmodol.connection`)

Owns the **expensive resource**: the `CosmosClient`. Holds:

- A resolved credential (one of: AAD `DefaultAzureCredential`, account key, resource token, connection string).
- A `CosmosClient` exposed via `cached_property` (lazy).
- Default options: `consistency_level` (defaults to None → inherit account default), retry policy, RU-observation callback.

**Credential resolution order** (`resolve_credential(...)`, first hit wins):

1. Explicit `credential=` kwarg.
2. Explicit `connection_string=` kwarg.
3. Env var `AZURE_COSMOS_CONNECTION_STRING`.
4. Env vars `AZURE_COSMOS_ENDPOINT` + `AZURE_COSMOS_KEY`.
5. Env var `AZURE_COSMOS_ENDPOINT` alone + `DefaultAzureCredential()`.
6. Raise an actionable error listing all five sources.

### Layer A — Close-to-metal CRUD facade (`cosmodol.base`)

A **functional** facade (free functions, not classes) over `ContainerProxy`. Each function
takes the container plus normalized args and returns `(value, response_headers)` so callers
can see RU charges and ETags:

```python
def point_get(
    container, id, partition_key, *, consistency_level=None
) -> tuple[dict, ResponseHeaders]: ...
def point_upsert(container, body, *, etag=None) -> tuple[dict, ResponseHeaders]: ...
def point_replace(
    container, id, body, partition_key, *, etag=None
) -> tuple[dict, ResponseHeaders]: ...
def point_delete(container, id, partition_key, *, etag=None) -> ResponseHeaders: ...
def point_contains(container, id, partition_key) -> bool: ...
def query(
    container,
    sql,
    *,
    parameters=None,
    partition_key=None,
    cross_partition=False,
    max_item_count=None,
) -> Iterator[dict]: ...
def batch(container, operations, partition_key) -> list[dict]: ...
```

This layer is where the **error translation** happens — see `cosmodol.errors`. No Mapping
semantics, no codecs, no `id` injection — that's Layer B's job.

### Layer B — Mapping stores

#### `cosmodol.stores`

**`CosmosItems`** — `MutableMapping[str, dict]` over **one fixed partition**. The simplest dict-like surface; keys are item `id` strings; values are JSON dicts. Use this when you want a key-value namespace scoped to one tenant / one bucket / one logical partition.

```python
items = CosmosItems(
    container,
    partition_key_value="tenant-42",
    *,
    value_codec=None,        # default: pass dicts through
    inject_id=True,          # auto-inject {"id": k} on writes
    strict_keys=True,        # validate id chars + length
    cross_partition=False,
)
items["k1"] = {"v": 1}       # stored as {"id":"k1", "<pk-path>":"tenant-42", "v":1}
items["k1"]                  # → {"id":"k1", "<pk-path>":"tenant-42", "v":1}
"k1" in items                # → point read, ~1 RU
del items["k1"]              # → point delete
list(items)                  # → SELECT c.id FROM c WHERE <pk>=@p     (single-partition)
```

**`CosmosPartitionedItems`** — `MutableMapping[tuple[str, str], dict]` over **all partitions** in a container. Keys are `(partition_key_value, id)` tuples. Use this when you want a store that spans the full container.

```python
items = CosmosPartitionedItems(
    container,
    partition_key_path="/_pk",
    *,
    value_codec=None,
    inject_id=True,
    inject_partition_key=True,
    len_via_query=False,    # opt-in expensive __len__
)
items[("tenant-42", "k1")] = {"v": 1}
items[("tenant-42", "k1")]               # point read
list(items)                              # cross-partition SELECT c.id, c.<pk> FROM c — warned by default
items.partition("tenant-42")             # → CosmosItems scoped to that partition (no round-trip)
```

When `partition_key_path == "/id"` (the recommended default for new containers),
`CosmosPartitionedItems` collapses to per-id partitioning and is implementation-equivalent
to `CosmosItems(partition_key_value=k)` for every `k` — see [design_decisions.md](design_decisions.md) §3.

#### `cosmodol.trees`

**`CosmosDatabase`** — `Mapping[str, ContainerProxy]` (or a wrapped store; see below) — keys are container names, values are containers. `__setitem__` is **disabled** by default (use `add_container(name, partition_key_path=..., throughput=...)`); container creation is too parameter-rich to fit one-arg dict assignment.

**`CosmosAccount`** — `Mapping[str, CosmosDatabase]` — keys are database names. Same opt-out
on `__setitem__`; explicit `add_database(name, throughput=...)`.

Both tree-level stores accept a `store_factory` callable that decides how to wrap each child
container — e.g. `store_factory=lambda c: CosmosItems(c, partition_key_value="default")` —
which is how recipes plug in JSON codecs uniformly. Same pattern as
`mongodol.MongoDbReader(mk_collection_store=...)`.

### Layer C — Convenience (`cosmodol.recipes`)

Built by composition only. Standard recipes:

```python
from dol import wrap_kvs, ValueCodecs

# Default: JSON dicts in/out (Cosmos already speaks JSON natively, this just normalises)
CosmosJsonStore = CosmosItems  # values are dicts, no codec needed

# Pickle objects keyed by string id — values stored as base64 in a {"_blob": "..."} property
CosmosPickleStore = wrap_kvs(
    CosmosItems, value_codec=ValueCodecs.pickle_b64_in_property("_blob")
)

# Strip Cosmos system fields (_etag, _ts, _rid, _self, _attachments) on read
CosmosCleanStore = wrap_kvs(CosmosItems, obj_of_data=strip_system_fields)
```

Plus one **top-level factory** for the easy path:

```python
cosmos_store(
    *,
    connection_string: str = None,
    endpoint: str = None,
    credential: ... = None,
    database: str,
    container: str,
    partition_key_value: str = None,         # → CosmosItems if given
    partition_key_path: str = None,          # → CosmosPartitionedItems if given (and pk_value is None)
    create_if_missing: bool = False,
    throughput: int = 400,                   # only used with create_if_missing
    value_codec=None,
    strip_system_fields: bool = True,
) -> MutableMapping
```

## Contracts the metal layer enforces

| Operation | Cost | Contract |
|---|---|---|
| `__getitem__(k)` | 1 point read (~1 RU/KB) | Returns the item `dict`. Raises `KeyError(k)` on missing. Re-raises auth/throttle errors untouched. |
| `__setitem__(k, v)` | 1 upsert (~10 RU/KB) | Accepts a JSON-serialisable mapping. Injects `id` (and partition-key value if `CosmosItems`). Overwrites. |
| `__delitem__(k)` | 1 point delete (~5 RU/KB) | Raises `KeyError(k)` on missing. |
| `__contains__(k)` | 1 point read | Returns `True/False`. Catches only `CosmosResourceNotFoundError`. |
| `__iter__()` | single-partition scan when pk fixed; **cross-partition + UserWarning** when not | Yields keys (id strings or `(pk, id)` tuples); paginated. |
| `__len__()` | **not implemented by default** | Opt-in via `len_via_query=True`; runs `SELECT VALUE COUNT(1)`. Documented cost. |
| `__repr__` | 0 | Includes container path + partition-key configuration. |

## RU observability

Every metal-layer function returns a `ResponseHeaders` namedtuple including
`request_charge` and `etag`. Stores expose `store.last_request_charge` (an
`Optional[float]`) after each operation and accept a `record_ru: Callable[[str, float], None]`
constructor hook for plugging metrics:

```python
items = CosmosItems(
    container, partition_key_value="t", record_ru=lambda op, ru: prom.observe(op, ru)
)
```

## Sub-stores

- `CosmosPartitionedItems.partition(pk_value) → CosmosItems` — zero round-trip narrowing.
- `CosmosItems.with_filter(sql_predicate) → CosmosItems` — narrows iteration with an extra `WHERE` clause; `__getitem__` is unaffected (point reads always hit the underlying item).

We do **not** overload `s[prefix]` for narrowing — Cosmos keys are flat strings and the
prefix convention has no native server-side support.

## What we explicitly do NOT do

- **Speak the MongoDB-API mode of Cosmos.** That's `pymongo` + `mongodol` territory; the README points users there.
- **Implement `__len__` by default.** See [design_decisions.md](design_decisions.md) §4.
- **Overload `__getitem__` to dispatch on type** (e.g. return a Cursor for queries, an item for ids). That's `mongodol`'s biggest UX wart; we don't repeat it.
- **Container/database creation through `__setitem__`.** Too parameter-rich; explicit `add_*` methods only.
- **Async support in v1.** `cosmodol.aio` will mirror Layer A and Layer B in v2.
- **Change feed, stored procs, triggers, UDFs.** Not Mapping-shaped.

## Module layout

```
cosmodol/
  __init__.py          # public API re-exports + module docstring
  connection.py        # CosmosConnection, resolve_credential
  base.py              # point_get/upsert/replace/delete/contains, query, batch
  errors.py            # translate_cosmos_errors, custom exceptions
  stores.py            # CosmosItems, CosmosPartitionedItems
  trees.py             # CosmosDatabase, CosmosAccount
  recipes.py           # cosmos_store(...), CosmosPickleStore, strip_system_fields
  testing.py           # Linux vNext emulator fixture, FakeContainerProxy
  tests/
    test_base.py
    test_stores.py
    test_trees.py
    test_recipes.py
```

## Backward compatibility

There are no users (the package is being created in this commit). Every public name is
free to evolve until we cut v0.1.0.
