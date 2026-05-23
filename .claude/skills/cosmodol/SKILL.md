---
name: cosmodol
description: Use when developing, reviewing, or extending the cosmodol package (Azure Cosmos DB NoSQL/Core API as dol Mapping interfaces). Triggers on edits under i/cosmodol/, on imports of `cosmodol`, when adding new Cosmos store variants, codecs, or credential paths, when reasoning about partition keys / RU costs / consistency levels, when writing tests against the Cosmos DB emulator, and when answering "how do I use Cosmos DB from Python the dol way".
---

# cosmodol — Developer & Agent Skill

`cosmodol` exposes the **NoSQL (Core/SQL) API of Azure Cosmos DB** as `dol`-style
`Mapping` / `MutableMapping` interfaces. This skill is the working memory: when modifying
or extending the package, read this first, then dive into the
[misc/docs/](../../../misc/docs/) trio that this file distills.

**Source of truth** (always defer to these):

- `misc/docs/architecture.md` — the layered design and class hierarchy.
- `misc/docs/cosmos_db_reference.md` — Cosmos service + `azure-cosmos` SDK facts.
- `misc/docs/design_decisions.md` — every defaulted choice with rationale.

---

## Mental model in one diagram

```
cosmodol.recipes        ← cosmos_store(...) factory, codec layers
cosmodol.trees          ← CosmosAccount, CosmosDatabase (mappings of children)
cosmodol.stores         ← CosmosItems (one partition), CosmosPartitionedItems (whole container)
cosmodol.base           ← free functions: point_get/upsert/replace/delete, query, batch
cosmodol.connection     ← CosmosConnection: credential cascade, lazy CosmosClient
cosmodol.errors         ← @translate_cosmos_errors decorator, custom KeyError subclasses
cosmodol.testing        ← vNext emulator fixture, FakeContainerProxy for unit tests
```

The two primary stores live in `stores.py`. Pick the right one:

| You have… | Use |
|---|---|
| A fixed partition-key value | `CosmosItems(container, partition_key_value=...)` — keys are id strings |
| The whole container | `CosmosPartitionedItems(container, partition_key_path=...)` — keys are `(pk_value, id)` tuples |

## Non-negotiable rules

1. **Scope is `azure-cosmos` (NoSQL API) only.** Cosmos-for-MongoDB accounts are not addressable through this SDK — route those users to `pymongo` + `mongodol`. The README must remain explicit about this.
2. **Class names are `Cosmos*` prefixed.** "Container" / "Database" collide with Blob's terminology and with generic Python — always say `CosmosContainer`-something or `CosmosDatabase`-something.
3. **`dol`-house base classes only.** Subclass `KvReader` / `KvPersister` from `dol.base`, never `Mapping` / `MutableMapping` directly.
4. **`__getitem__` and `__contains__` are ALWAYS point reads** (`read_item` with `partition_key=`). Never a query. ~1 RU vs ≥ 2.3 RU + result-size charge.
5. **`__len__` on `CosmosPartitionedItems` is NOT implemented by default.** Opt-in via `len_via_query=True`. `CosmosItems.__len__` (single-partition) is OK and implemented.
6. **`__iter__` on a full-container store emits a `UserWarning`** on first call (silence with `silent_full_scan=True`). Cross-partition scans are an economic land mine.
7. **All Cosmos-exception catches happen in `errors.py`.** `@translate_cosmos_errors` is the only path; auth/throttle errors never translate to "key absent".
8. **`__setitem__` auto-injects `id` (and `partition_key`) into the body.** If user-provided body already has those fields and they *disagree* with the inferred values, raise `KeyMismatchError` — never silently overwrite user dicts.
9. **Container/database creation is NEVER through `__setitem__`.** Use explicit `add_container(name, partition_key_path=..., throughput=..., ...)` and `add_database(name, throughput=...)`. Too parameter-rich for one-arg dict assignment.
10. **`del tree_store[name]` refuses non-empty children.** `tree_store.delete(name, force=True)` is the explicit purge.
11. **No silent destruction. No silent expensive ops. No silent partition crossing.** Every potentially expensive call has either a fixed-cost path (point read) or an explicit cost-aware opt-in flag.
12. **`id` is validated on writes** — string, ≤255 chars, no `/ \ ? #`. Cosmos may accept invalid ids but the item then becomes unreachable from the SDK.

## RU observability — required, not optional

Every Layer A function returns `(value, ResponseHeaders)` where `ResponseHeaders` carries
`request_charge` (RU) and `etag`. Every Layer B store exposes `store.last_request_charge`
(an `Optional[float]`) after each op and accepts a `record_ru` callback:

```python
items = CosmosItems(container, partition_key_value="t",
                    record_ru=lambda op, ru: prom.observe(op, ru))
items["k1"]
items.last_request_charge   # → float (RU consumed)
```

If you add a new operation, **route it through the metal-layer free functions** so it
collects RU automatically. Don't call `container.read_item(...)` directly from the store.

## Partition-key playbook

The defining design fact in Cosmos. cosmodol exposes three idiomatic shapes; pick the
right one in your example/test:

| User has… | Recommended cosmodol class | Notes |
|---|---|---|
| One logical bucket of items (single tenant, single session) | `CosmosItems(container, partition_key_value="tenant-X")` | Simplest dict surface. Keys = `id`. |
| A multi-tenant container, each item is its own partition | `CosmosPartitionedItems(container, partition_key_path="/id")` | **Recommended default** for new containers. Best write distribution. Cross-partition queries needed for non-id WHERE clauses. |
| An existing container with a custom partition scheme | `CosmosPartitionedItems(container, partition_key_path="/whatever")` | Keys = `(pk_value, id)` tuples. |
| A view scoped to one partition of an existing partitioned container | `partitioned.partition(pk_value)` → `CosmosItems` | Zero round-trip narrowing. |

**Never** invent a 4th shape with a separator-joined string key (`"tenant42::user99"`) —
escaping the separator is more trouble than the tuple is worth. Users who want flat
strings compose `wrap_kvs` on top.

## Adding a new value codec / key transform

Layer C only. Never subclass `CosmosItems` to add a codec — use `dol`'s composition tools:

```python
from dol import wrap_kvs, ValueCodecs
from cosmodol.stores import CosmosItems

# Right way: compose
CosmosBytesStore = wrap_kvs(
    CosmosItems,
    value_codec=ValueCodecs.pickle_b64_in_property('_blob'),
)

# Wrong way: subclass
class CosmosBytesStore(CosmosItems):  # don't do this
    def __getitem__(self, k):
        return base64.b64decode(super().__getitem__(k)['_blob'])
```

When the codec wraps the user's value into a Cosmos-shaped envelope `{"_blob": "..."}`,
make sure your `data_of_obj` does NOT overwrite `id` or the partition-key property —
those are injected by the store layer.

## Adding a new connection path / credential type

`cosmodol.connection.resolve_credential(...)` is the single place. Extend its cascade,
update `design_decisions.md` §14, and add a test in `tests/test_connection.py` that
exercises the new branch.

Document order of precedence in `architecture.md` Layer 0.

## Adding to the metal layer (when a new SDK feature lands)

Add a free function in `base.py` (not a method on a store). Wrap it with
`@translate_cosmos_errors` for any path that touches an item. Return
`(value, ResponseHeaders)` so RU charges are observable. Then surface it at Layer B if it's
genuinely Mapping-shaped, or as a top-level method on the relevant store otherwise.

## Testing protocol

1. **Unit tests** use `FakeContainerProxy` from `cosmodol.testing` — a dict-of-dicts-backed mock implementing the subset of `ContainerProxy` methods the metal layer uses. Fast, no Docker.
2. **Integration tests** use the Linux vNext emulator via the `cosmos_emulator` fixture (Docker-backed). Skipped with a clear message when Docker unavailable.
3. **Skip RU-charge assertions** when running against the emulator — the emulator does NOT populate `x-ms-request-charge`. The fixture sets a marker flag the tests can check.
4. **Doctests** in module docstrings stay runnable (`NORMALIZE_WHITESPACE` + `ELLIPSIS` enabled).
5. **Live-Azure tests** gated by env var `AZURE_COSMOS_LIVE_TEST_ENDPOINT`; never run in CI by default.

## Spinning up the Cosmos emulator locally (Mac/ARM friendly)

```bash
docker pull mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview
docker run --detach \
  --publish 8081:8081 --publish 8080:8080 --publish 1234:1234 \
  mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview
```

Well-known credentials (same on every emulator instance):

```
Endpoint: https://localhost:8081
Key:      C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==
```

Use `--protocol http` if the HTTPS cert dance is painful.

Emulator **does not** support: stored procs/triggers/UDFs, parallel cross-partition
queries, offers/permissions/users, custom indexing policies (accepted but no-op), RU
accounting (header missing). Don't write tests that depend on those features.

## Common operations cheatsheet

| Want to... | Use |
|---|---|
| Dict over one partition | `CosmosItems(container, partition_key_value="X")` |
| Dict over the whole container, tuple keys | `CosmosPartitionedItems(container, partition_key_path="/_pk")` |
| Narrow a partitioned store to one partition | `partitioned.partition("X")` → `CosmosItems` |
| Narrow with a WHERE clause | `items.with_filter("c.status = 'active'")` |
| List databases | `for name in CosmosAccount(connection): ...` |
| List containers | `for name in CosmosDatabase(connection, "mydb"): ...` |
| Create a new container | `db_store.add_container("mycoll", partition_key_path="/id")` |
| Run a SQL query | `items.query("SELECT * FROM c WHERE c.age > @a", parameters=[{"name":"@a","value":18}])` |
| Transactional batch (one partition) | `items.batch([("upsert", {"id":"k","v":1}), ("delete", "k2"), ...])` |
| Conditional replace via ETag | `items.replace("k", new_body, etag=items.last_response_headers.etag)` |
| One-call factory | `recipes.cosmos_store(connection_string=..., database=..., container=..., partition_key_value=...)` |

## When in doubt

- Defaults always lean cost-visible (no auto-len, no implicit cross-partition).
- Defaults always lean Mapping-faithful (KeyError on missing, point reads for contains/get).
- Surface always leans `dol`-composable (wrap_kvs over subclasses, codecs over methods).

When changing a default, update `design_decisions.md` in the **same** PR. The doc IS the
contract.
