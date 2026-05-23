# Azure Cosmos DB — Technical Reference for cosmodol

A condensed reference for contributors. The full citations are kept in the package's
GitHub issues. The goal here is to make the *design choices* in
[architecture.md](architecture.md) and [design_decisions.md](design_decisions.md)
self-contained.

---

## What is Cosmos DB?

Globally distributed, fully managed multi-model NoSQL database. The underlying engine
exposes five wire-compatible API surfaces, chosen at account-creation time and immutable
thereafter:

| API | Drivers people use | "Container" called | "Item" called |
|---|---|---|---|
| **NoSQL** ("Core" / "SQL API") | `azure-cosmos` (this is what cosmodol targets) | Container | Item |
| MongoDB | `pymongo` (works as-is) | Collection | Document |
| Cassandra | DataStax CQL driver | Table | Row |
| Gremlin | `gremlinpython` | Graph | Node/Edge |
| Table | `azure-data-tables` | Table | Item |

The `azure-cosmos` PyPI package only targets the **NoSQL API**. There is no shared substrate across APIs; an account is permanently bound to one. For Cosmos-for-MongoDB accounts, the right answer is `pymongo` + `mongodol`.

## Resource hierarchy

```
Account                       https://{acct}.documents.azure.com:443/
└── Database                  (pure namespace; can hold shared throughput up to 25 containers)
    └── Container             (the unit of partitioning, indexing, throughput)
        └── Item              (JSON doc with system + user properties)
```

System properties on every item: `_rid`, `_etag`, `_ts`, `_self`, plus `id` and the
partition-key property.

## Partition key — the central concept

A container is sharded by the value of one (or up to three, "HPK") JSON property paths
designated as the partition key, e.g. `/userId` or `/_pk`. Properties of a logical partition:

- Capped at **20 GB** of storage per logical partition.
- Bound to a single physical partition; physical partitions cap at **10 000 RU/s** and **50 GB**.
- Addressed by hash of the partition-key value (cardinality matters for distribution).
- **Mandatory** on every container created on the v2 partitioning scheme — the SDK requires `PartitionKey(path="/...")` on `database.create_container()`.

Every item-level method except `create_item`/`upsert_item` requires `partition_key=`. Cosmos uses it to route to the correct physical replica without scanning. `create_item`/`upsert_item` extract the partition-key value from the body itself.

**Partition-key values are immutable.** To "move" an item you delete + recreate (non-atomic across partitions).

## Authentication

`CosmosClient(url, credential=...)` accepts:

1. **Account key** (string) — primary/secondary master key from `az cosmosdb list-keys`. Full access; simplest.
2. **Resource tokens** (dict) — narrowly scoped tokens minted via SDK `User`/`Permission`. Mobile/client-side scenarios.
3. **AAD credential** — any `azure.identity` credential; `DefaultAzureCredential()` is the recommended path. Principal needs RBAC role assignments including `readMetadata` permission.
4. **Connection string** — `CosmosClient.from_connection_string("AccountEndpoint=...;AccountKey=...")`. Wrapper around #1.

SDK clients:

- `azure.cosmos.CosmosClient` — root, sync.
- `azure.cosmos.DatabaseProxy` — from `client.get_database_client(name)`. Manages containers, users, throughput.
- `azure.cosmos.ContainerProxy` — from `database.get_container_client(name)`. All item-level CRUD.
- `azure.cosmos.aio.CosmosClient` — fully separate async client; must be entered as `async with` so account-info caching happens.

## Core operations cosmodol wraps

All on `ContainerProxy`; each call returns either a `CosmosDict` (a dict subclass) or
similar; response headers carry `etag` and `x-ms-request-charge` (RU consumed).

| Operation | Method | Partition key | Notes |
|---|---|---|---|
| Create (fail-on-dup) | `create_item(body, **kw)` | extracted from body | Raises `CosmosResourceExistsError` on dup. |
| Point read | `read_item(item, partition_key, **kw)` | required | **~1 RU/KB**; the cheapest possible op. |
| Upsert | `upsert_item(body, **kw)` | extracted from body | Insert-or-replace. |
| Full replace | `replace_item(item, body, partition_key=None, **kw)` | required | |
| Patch (partial) | `patch_item(item, partition_key, patch_operations, **kw)` | required | Up to 10 ops per call. |
| Delete | `delete_item(item, partition_key, **kw)` | required | |
| SQL query | `query_items(query, parameters=None, partition_key=None, enable_cross_partition_query=None, max_item_count=None, **kw)` | required *or* cross-partition flag | Query engine; more RU than point read even for `WHERE id = @id`. |
| Read all | `read_all_items(max_item_count=None, **kw)` | implicit cross-partition | |
| Batch (transactional) | `execute_item_batch(batch_operations, partition_key, **kw)` | required (single partition) | ≤100 ops, ≤1.2 MB total. |
| List containers | `database.list_containers()` | — | |
| List databases | `client.list_databases()` | — | |

### Point read is *not* a query

`SELECT * FROM c WHERE c.id = @id` is **not** equivalent to `read_item(id, partition_key=pk)`. The query goes through the query engine, costs more RU, and must specify `partition_key=` or `enable_cross_partition_query=True`. **`cosmodol.__contains__` and `__getitem__` always use `read_item`.**

### Transactional batch limits

- ≤ 100 operations per batch.
- ≤ 1.2 MB total payload.
- All operations must share one partition key — there are no cross-partition transactions.
- Op-level kwargs available (e.g. `if_match_etag` on replace inside a batch).
- Failure raises `CosmosBatchOperationError`; whole batch rolls back.

### ETag / optimistic concurrency

Every item carries `_etag`. Pass `etag=value` to `if_match_etag=`/`if_none_match_etag=` on replace/patch/delete (and per-op inside a batch). Mismatch → `CosmosAccessConditionFailedError` (HTTP 412).

## `id` rules (encoded as validation in cosmodol)

- Must be a **string**.
- Unique within a logical partition (not container-wide).
- Maximum **255 characters** (emulator caps at 254).
- **Forbidden characters: `/`, `\`, `?`, `#`**. Cosmos may accept writes with these but the item then becomes unreachable from the SDK. URL-encoding does not help; URL-safe base64 (no padding) is the standard escape.

## RU economics (1 KB items, default indexing)

| Op | Cost |
|---|---|
| Point read | ~1 RU |
| Insert | ~5 RU (scales with # of indexed properties) |
| Upsert / replace | ~10 RU |
| Delete | ~5 RU |
| Single-partition query | ≥ 2.3 RU + result-size cost |
| Cross-partition query | sum across physical partitions + ~2.3 RU/physical-partition overhead |
| `COUNT(1)` over a partition | scales with cardinality, not result size |

Strong / Bounded Staleness reads cost roughly **2× more RU** than weaker levels (two replicas consulted).

`CosmosThrottleError` (HTTP 429) on RU exhaustion. SDK retries with `RetryOptions` (configurable `max_retry_attempt_count`, `max_retry_wait_time_in_seconds`).

## Consistency levels

| Level | Notes |
|---|---|
| Strong | Linearizable; blocked >5000 mi apart; incompatible with multi-region writes. |
| Bounded Staleness | Bounded by K updates or T time. |
| **Session** (default) | Read-your-writes per session token. **Most apps want this.** |
| Consistent Prefix | No out-of-order observation. |
| Eventual | Cheapest reads. |

Configured at the **account** level. Since SDK 4.3.0b3, omitting `consistency_level=` on
`CosmosClient` inherits the account default — which is what cosmodol does.

## Indexing

By default, **every property of every item is indexed**, on every container, consistently. This is what makes ad-hoc SQL queries fast on Cosmos. Trade-offs:

- Writes cost more RU as indexed-property count grows.
- Custom `indexingPolicy` can exclude paths, add composite indexes (multi-property sort), spatial indexes, or vector indexes.

For cosmodol use cases that dump opaque payloads (base64 blobs, embeddings), a
"minimal indexing" preset (`indexingPolicy = {"automatic": False}` or exclude-all paths)
slashes write RU costs. Recipe-level helper.

## Schema-on-read

Cosmos stores arbitrary JSON. There's no schema enforcement at the container level. Implications for cosmodol:

- **Values are naturally `dict`.** No serializer layer needed for the JSON-typed default case.
- For non-JSON Python objects (pickle, parquet bytes), wrap with `dol.wrap_kvs(value_codec=...)` that encodes into a `{"id": ..., "_pk": ..., "_blob": "<b64>"}` envelope.
- The mandatory system property `id` (and partition-key property) **must** appear in the stored body. cosmodol injects them automatically on writes so the user can pass the "pure" value dict.

## Pitfalls (encoded in tests/validators where possible)

- **Forgetting `partition_key=`** — #1 source of `CosmosHttpResponseError` for newcomers.
- **`id` validation** — must be string, ≤ 255 chars, no `/ \ ? #`. cosmodol validates at write time.
- **Partition-key values are immutable.** No `move`/`rename`.
- **Word collision with `azure-storage-blob`** — Cosmos containers ≠ Blob containers. cosmodol uses the `Cosmos*` prefix on every public class.
- **Bulk is not implemented in the sync SDK.** Workaround uses the async client; we document but don't ship in v1.
- **Cross-partition asymmetry**: sync client requires `enable_cross_partition_query=True`; async client defaults to cross-partition when no `partition_key=` given. cosmodol normalises with a single `cross_partition=True` kwarg.
- **Group By and a few advanced query forms** are unsupported by the Python SDK. Not relevant to Mapping.
- **Python `True`/`False` ↔ JSON `true`/`false`** — SDK transcodes for parameter binding, but raw SQL strings must use lowercase `true`/`false`.

## Local testing — Cosmos DB Emulator

### Linux vNext (preview) — the one to use on Mac/ARM

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

- HTTP by default (no cert hassle); pass `--protocol https` if needed.
- Supports: NoSQL API only, gateway mode, document CRUD, batch, bulk, change feed, queries (order-by/aggregates/joins), TTL containers.
- **Notable holes**: RU accounting not implemented (header missing/zero), stored procs/triggers/UDFs not planned, parallel cross-partition queries not yet implemented, no offers/permissions/users endpoints. Custom index policies accepted but no-op.

Skip RU-charge assertions when running against the emulator.

### Legacy classic emulator

Windows-native, also a Linux docker image. **Does not run on Apple Silicon.** Capped at 254-char ids. Only relevant for code that already runs on Windows CI; otherwise use vNext.

## Cosmos DB for MongoDB API

A wire-protocol implementation of MongoDB on top of the Cosmos engine. Selected at
**account-creation time** (Azure portal → New Cosmos DB → "Azure Cosmos DB for MongoDB").
Such accounts are **not** addressable through `azure-cosmos`. The user wires up
`pymongo` + `mongodol` directly with the MongoDB-style connection string from the portal.

cosmodol's README prominently directs users with MongoDB-API accounts there.
