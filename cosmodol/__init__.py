"""Access Azure Cosmos DB (NoSQL/Core API) through a Mapping interface.

``cosmodol`` exposes Azure Cosmos DB as ``dol``-style ``Mapping`` /
``MutableMapping`` interfaces, layered over the official ``azure-cosmos`` SDK.

Quick start::

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

See ``misc/docs/architecture.md`` for the layered design.

If your Cosmos account was provisioned with the **MongoDB API**, use ``pymongo`` +
``mongodol`` directly — this package only targets the NoSQL/Core API.
"""

from cosmodol.connection import CosmosConnection, resolve_credential
from cosmodol.base import (
    ResponseHeaders,
    batch,
    point_contains,
    point_delete,
    point_get,
    point_replace,
    point_upsert,
    query,
)
from cosmodol.errors import (
    ContainerNotEmptyError,
    ContainerNotFoundError,
    CosmosThrottleError,
    DatabaseNotEmptyError,
    DatabaseNotFoundError,
    ItemAlreadyExistsError,
    ItemNotFoundError,
    KeyMismatchError,
    translate_cosmos_errors,
    validate_cosmos_id,
)
from cosmodol.stores import (
    CosmosItems,
    CosmosPartitionedItems,
    SYSTEM_FIELDS,
)
from cosmodol.trees import (
    CosmosAccount,
    CosmosDatabase,
)
from cosmodol.recipes import (
    cosmos_store,
    strip_system_fields,
)


__all__ = [
    # connection
    "CosmosConnection",
    "resolve_credential",
    # base / metal layer
    "ResponseHeaders",
    "batch",
    "point_contains",
    "point_delete",
    "point_get",
    "point_replace",
    "point_upsert",
    "query",
    # errors
    "ContainerNotEmptyError",
    "ContainerNotFoundError",
    "CosmosThrottleError",
    "DatabaseNotEmptyError",
    "DatabaseNotFoundError",
    "ItemAlreadyExistsError",
    "ItemNotFoundError",
    "KeyMismatchError",
    "translate_cosmos_errors",
    "validate_cosmos_id",
    # stores
    "CosmosItems",
    "CosmosPartitionedItems",
    "SYSTEM_FIELDS",
    # trees
    "CosmosAccount",
    "CosmosDatabase",
    # recipes
    "cosmos_store",
    "strip_system_fields",
]
