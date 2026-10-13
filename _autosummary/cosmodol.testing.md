# cosmodol.testing

Testing helpers for cosmodol — emulator fixture and a minimal in-memory fake.

The emulator-backed pieces require Docker. Unit tests can use `FakeContainerProxy`
for fast in-memory testing of the metal layer without any SDK / network.

### Functions

| [`cosmos_emulator`](#cosmodol.testing.cosmos_emulator)([container_name, wait])   | Context manager that ensures the Linux vNext emulator is running.             |
|--------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| `docker_available`()                                                                       |                                                                               |
| [`emulator_is_running`](#cosmodol.testing.emulator_is_running)()                     | Return True if the Cosmos emulator's HTTPS endpoint (port 8081) is reachable. |
| [`mk_emulator_connection`](#cosmodol.testing.mk_emulator_connection)()                  | Build a `CosmosConnection` pointing at the running emulator.                  |

### Classes

| [`FakeContainerProxy`](#cosmodol.testing.FakeContainerProxy)(\*[, container_id, ...])   | A dict-of-dicts-backed stand-in for `azure.cosmos.ContainerProxy`.   |
|------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|

### *class* cosmodol.testing.FakeContainerProxy(, container_id='fake-container', partition_key_path='/id')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A dict-of-dicts-backed stand-in for `azure.cosmos.ContainerProxy`.

Supports the subset of methods that cosmodol.base / .stores call:
`read_item`, `upsert_item`, `replace_item`, `delete_item`, `query_items`,
`read` (container metadata). Storage is `{(pk_value, id): item_dict}`.

#### execute_item_batch(batch_operations, partition_key)

All-or-nothing batch: on any failure the container is restored and the error
re-raised. Supports `create`/`upsert`/`replace`/`read`/`delete`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### query_items(query, , parameters=None, partition_key=None, enable_cross_partition_query=False, max_item_count=None, \*\*\_kw)

Very small SQL emulator: supports COUNT(1) and projections of c.id / c.<pk>.

Sufficient for the queries cosmodol issues internally. Real SQL is out of scope.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### cosmodol.testing.cosmos_emulator(container_name=None, , wait=60.0)

Context manager that ensures the Linux vNext emulator is running.

If the emulator is already up (port 8081 reachable), do nothing on enter/exit.
Otherwise start it via `docker run` and stop it on exit. Note the emulator takes
~30-60 seconds to become ready.

* **Parameters:**
  * **container_name** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Docker container name. Random by default.
  * **wait** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Max seconds to wait for the endpoint to come up.
* **Yields:**
  The emulator connection string.
* **Raises:**
  [**RuntimeError**](https://docs.python.org/3/builtins/exceptions.html#RuntimeError) – if Docker is unavailable and the emulator is not already running.

### cosmodol.testing.emulator_is_running()

Return True if the Cosmos emulator’s HTTPS endpoint (port 8081) is reachable.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### cosmodol.testing.mk_emulator_connection()

Build a `CosmosConnection` pointing at the running emulator. Disables SSL
verification (the emulator uses a self-signed cert).

* **Return type:**
  [`CosmosConnection`](cosmodol.connection.md#cosmodol.connection.CosmosConnection)
