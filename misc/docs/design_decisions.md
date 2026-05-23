# cosmodol — Design Decisions

Every defaulted choice with a one-paragraph rationale. When in doubt about *why* the code
is the way it is, this is the doc to read. When changing a default, update this doc in the
same PR.

---

## 1. Scope is `azure-cosmos` (NoSQL/Core API) only

Cosmos DB exposes five wire APIs (NoSQL, MongoDB, Cassandra, Gremlin, Table). The
`azure-cosmos` PyPI package only speaks the NoSQL API; the others are addressable through
their respective native clients (`pymongo`, DataStax driver, `gremlinpython`,
`azure-data-tables`).

We **explicitly do not** try to be the one-stop Cosmos package. Cosmos-for-MongoDB users
get `mongodol`; cosmodol's README routes them there. This keeps the dependency surface
tight (one SDK), the error model coherent (one set of exceptions), and the documentation
honest.

## 2. Naming convention — every class is prefixed with `Cosmos`

`Container`/`Database`/`Item` collide hard with:

- `azure-storage-blob` ContainerClient (a user with `azuredol` open in the same project will be confused).
- Docker / OCI containers.
- Generic Python "container" types.

So everything is `Cosmos*`: `CosmosItems`, `CosmosPartitionedItems`, `CosmosDatabase`,
`CosmosAccount`. Layer A's free functions live in `cosmodol.base` and are namespaced
by module path (`cosmodol.base.point_get`).

## 3. Two store flavors at Layer B, not one

There are two genuinely-different cosmos store shapes a Pythonic user wants:

- **`CosmosItems(container, partition_key_value)`** — keys are `id` strings, pinned to one partition. Simplest dict-like surface; great for "one user's data", "one tenant's logs", "one bucket".
- **`CosmosPartitionedItems(container, partition_key_path)`** — keys are `(pk_value, id)` tuples. Spans the whole container. The faithful representation of Cosmos's actual addressing.

Both are first-class. The convenience top-level factory `cosmos_store(...)` picks one
based on which kwarg the user passes (`partition_key_value=` → `CosmosItems`,
`partition_key_path=` → `CosmosPartitionedItems`).

We considered a third "derived-string key with separator" variant (`"tenant42::user99"`)
and rejected it: defensive escaping (the separator could legally appear inside a
partition-key value) makes it more trouble than the tuple form is worth. Users who want
string-flattened keys compose `wrap_kvs` on top.

### Recommended default partition-key strategy: `/id`

For *new* containers cosmodol provisions, the default `partition_key_path` is `/id` —
each item is its own logical partition. This:

- Gives best write/read distribution out of the box (random ids ⇒ near-uniform hash distribution).
- Makes point reads always know their partition key (it equals the id) — no extra plumbing for the common case.
- Means `CosmosPartitionedItems(container, partition_key_path="/id")` collapses to per-id partitioning, and `CosmosItems(container, partition_key_value=k)` for every `k` is the equivalent partition-restricted view.

The downside — queries that filter on properties other than `id` go cross-partition — is
the documented trade-off, and matches Microsoft's recommended pattern for point-access
workloads.

## 4. `__len__` is opt-in, not implemented by default

`SELECT VALUE COUNT(1) FROM c` is cross-partition by default and scales with cardinality.
A user who writes `len(store)` on a 100M-item container will be very surprised by the RU
bill.

`CosmosItems.__len__` (single-partition) is OK and is implemented — single-partition
`COUNT(1)` is bounded by partition size (20 GB → ~tens of millions of items).

`CosmosPartitionedItems.__len__` raises `TypeError` by default (so that `list(store)`
and other `operator.length_hint`-using consumers fall back gracefully); opt-in via
`len_via_query=True` constructor flag, which is loudly documented.

## 5. `__contains__` and `__getitem__` are always point reads

`SELECT * FROM c WHERE c.id = @id` would work but costs strictly more RU than `read_item(id, partition_key=pk)` — the query engine vs the point-read path. We never use queries
for existence checks or single-item fetches in the metal layer.

## 6. `__iter__` emits a `UserWarning` for cross-partition scans

When `CosmosPartitionedItems.__iter__` would have to scan the whole container (i.e. no
partition key fixed and no narrowing filter), it issues a single `UserWarning` on first
call:

```
UserWarning: Iterating a CosmosPartitionedItems is a cross-partition scan; RU cost
scales with container size. Use .partition(pk_value) or .with_filter(...) to narrow,
or pass `silent_full_scan=True` to suppress this warning.
```

This is the "noisy by default, easily silenced" middle ground between rejecting the
operation outright and letting users discover the cost on their next bill.

## 7. Container/database creation does NOT go through `__setitem__`

Cosmos container creation has many parameters (partition key path, indexing policy,
throughput offer mode, default TTL, unique key policy, conflict resolution policy). Squeezing them into a one-arg `account_store["mydb"] = db` would either drop everything or accept implicit kwargs that nobody can audit later.

So:

- `CosmosAccount.__setitem__` / `CosmosDatabase.__setitem__` are **disabled** (raise `TypeError` with a directive message).
- `add_database(name, throughput=None)` and `add_container(name, partition_key_path, throughput=None, indexing_policy=None, default_ttl=None, ...)` are the explicit creators.

This mirrors how `chromadol` disables `clear()` for safety — the surface owes the user
clarity over conciseness here.

## 8. `__delitem__` on tree-level stores never auto-empties

`del account_store['mydb']` deletes only an *empty* database. If it has containers,
raises with a directive message. `account_store.delete('mydb', force=True)` is the explicit
"yes, drop everything below" escape hatch.

Same on `CosmosDatabase.__delitem__` for non-empty containers.

## 9. Auto-inject `id` (and partition key) on writes

Users pass the "pure" value dict:

```python
items["k1"] = {"name": "Alice", "age": 30}
# Stored as: {"id": "k1", "<pk-path>": "<pk-value>", "name": "Alice", "age": 30}
```

`inject_id=True` and `inject_partition_key=True` are constructor defaults. Users with
pre-formed Cosmos items (e.g. migrating data) can pass `inject_*=False` and supply
complete bodies themselves.

If the body already has an `id`/partition-key field that **disagrees** with the inferred
value, that's an error (`KeyMismatchError`) rather than a silent overwrite. We never
silently mutate user-provided dicts.

## 10. Strip Cosmos system fields by default

`_etag`, `_ts`, `_rid`, `_self`, `_attachments` are stripped from the returned dict by
default (`strip_system_fields=True` on the top-level `cosmos_store(...)` factory). They
remain accessible via `store.last_response_headers` for users who need them.

Users who do need system fields in the value (e.g. for ETag-conditional writes) pass
`strip_system_fields=False`.

## 11. Single error-translation decorator

Same pattern as azuredol: `@translate_cosmos_errors(key_arg=...)` is the **only** place
catching `azure.cosmos.exceptions`. Mapping:

| Cosmos exception | Behavior |
|---|---|
| `CosmosResourceNotFoundError` (404) | → `KeyError(k)` for `__getitem__`/`__delitem__`; → `False` for `__contains__`. |
| `CosmosResourceExistsError` (409) | → `ItemAlreadyExistsError(k)` (a `KeyError` subclass). |
| `CosmosAccessConditionFailedError` (412) | re-raised (ETag mismatch is the caller's problem). |
| `CosmosClientTimeoutError` | re-raised. |
| `CosmosHttpResponseError` with `.status_code == 429` | wrap in `CosmosThrottleError`; re-raise (or retry per SDK budget). |
| `CosmosHttpResponseError` other | re-raised. |
| Anything else | re-raised. |

Never swallow auth errors as "key absent".

## 12. RU observability is built in

Layer A's free functions return `(value, ResponseHeaders)`. Layer B stores expose
`store.last_request_charge` and accept `record_ru: Callable[[str, float], None]`. This is
the *minimum* viable observability for a Cosmos library — RU is the currency, and a store
that hides it is one bug away from a four-digit Azure bill.

## 13. Consistency level inherits from the account by default

Cosmos accounts have a configured default consistency level (most commonly Session). The
SDK inherits it when `consistency_level=` is omitted on `CosmosClient` (since 4.3.0b3).
cosmodol does the same — we don't force a default, we don't override the account's. The
constructor takes an optional `consistency_level=` to override on a per-store basis.

## 14. No global mutable state

No module-level `CosmosClient` cache; no process-wide credential cache beyond what the
SDK does itself. The recipe-layer `cosmos_store(...)` factory caches connections per
`(endpoint, database, container)` via `functools.lru_cache`, opt-out, documented.

## 15. Async support is deferred to v2

Same posture as azuredol. The architecture mirrors trivially (`azure.cosmos.aio` has
parallel method names); the first job is to get the sync surface right.

## 16. Testing strategy

- **Unit tests** with `FakeContainerProxy` (`dict`-of-`dict`s backed) for in-memory speed.
- **Integration tests** require the Linux vNext emulator. A `pytest` fixture
  (`cosmos_emulator`) starts the container via Docker if available, otherwise skips with
  a clear message. Tests skip RU-charge assertions when running against the emulator
  (the emulator doesn't populate the RU header).
- **Live-Azure tests** gated by env var `AZURE_COSMOS_LIVE_TEST_ENDPOINT`; never run in
  CI by default.

## 17. Bulk and change-feed are out of v1

- **Bulk**: not implemented in the sync SDK. The async-loop workaround exists, but it's
  the fastest way to overrun an RU budget; we'd rather not ship it without a
  rate-limited wrapper that we've taken the time to design.
- **Change feed**: powerful but not Mapping-shaped. Will land in v2 as a separate
  `cosmodol.changefeed` module, not by overloading the Mapping interface.

## 18. Vector search is out of v1

Cosmos vector indexing (`flat`, `quantizedFlat`, `diskANN`) is GA on the wire but the
Pythonic shape for a vector-aware `Mapping` is unclear. Defer to v2 once we've seen real
usage patterns.
