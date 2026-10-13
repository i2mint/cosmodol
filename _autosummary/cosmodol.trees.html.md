# cosmodol.trees

Tree-shaped stores: mapping-of-databases and mapping-of-containers.

- `CosmosDatabase` — keys are container names; values are `ContainerProxy` (or a
  store-wrapped form via `store_factory`).
- `CosmosAccount` — keys are database names; values are `CosmosDatabase` instances.

Container/database creation is NOT via `__setitem__` — too parameter-rich; use
`cosmodol.add_container(db_store, ...)` / `cosmodol.add_database(account_store, ...)`
(free functions, so a `dol` key codec around the store is honoured). See
`misc/docs/design_decisions.md` §7.

### Classes

| [`CosmosAccount`](#cosmodol.trees.CosmosAccount)([connection, database_factory])   | Mapping of database name → `CosmosDatabase`.                          |
|--------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`CosmosDatabase`](#cosmodol.trees.CosmosDatabase)(database, \*[, connection, ...]) | Mapping of container name → `ContainerProxy` (or store-wrapped form). |

### *class* cosmodol.trees.CosmosAccount(connection=None, , database_factory=None)

Bases: `KvReader`

Mapping of database name → `CosmosDatabase`.

`__setitem__` is disabled — use `cosmodol.add_database(account_store, name, ...)`.
`__delitem__` refuses non-empty databases; use
`cosmodol.delete_database(account_store, name, force=True)`.

### *class* cosmodol.trees.CosmosDatabase(database, , connection=None, store_factory=None)

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
