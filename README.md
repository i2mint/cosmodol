# cosmodol

Azure **Cosmos DB** Data Object Layer — access Cosmos DB through a `dict`-like
[`dol`](https://github.com/i2mint/dol) `Mapping` / `MutableMapping` interface.

To install: `pip install cosmodol`

## Scope

`cosmodol` targets the **NoSQL (Core / SQL) API** of Azure Cosmos DB via the official
[`azure-cosmos`](https://pypi.org/project/azure-cosmos/) Python SDK.

If your Cosmos DB account was provisioned with the **MongoDB API**, use
[`pymongo`](https://pypi.org/project/pymongo/) directly with
[`mongodol`](https://github.com/i2mint/mongodol) — Cosmos's MongoDB-API accounts speak the
MongoDB wire protocol, and `cosmodol` cannot reach them.

For Azure Blob Storage, see the sibling package
[`azuredol`](https://github.com/i2mint/azuredol).

## Quick start

```python
from cosmodol import cosmos_store

# Single-partition store (simplest)
store = cosmos_store(
    connection_string="AccountEndpoint=https://localhost:8081/;AccountKey=…",
    database="mydb",
    container="mycontainer",
    partition_key_value="my-partition",
)

store["k1"] = {"name": "Alice", "age": 30}
store["k1"]                # → {"id": "k1", "_pk": "my-partition", "name": "Alice", "age": 30}
"k1" in store              # → True
del store["k1"]
```

## See also

- [Architecture (misc/docs/architecture.md)](misc/docs/architecture.md) — the layered design.
- [Design decisions (misc/docs/design_decisions.md)](misc/docs/design_decisions.md) — defaults and rationale.
- [Cosmos DB reference (misc/docs/cosmos_db_reference.md)](misc/docs/cosmos_db_reference.md) — distilled SDK and service facts.
- [`dol`](https://github.com/i2mint/dol) — the underlying data-object-layer toolkit.
