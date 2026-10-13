# cosmodol.connection

Connection-layer for cosmodol.

Owns the expensive resource (`CosmosClient`) and the credential cascade.
See `misc/docs/architecture.md` Layer 0 and `misc/docs/design_decisions.md` §14.

### Functions

| [`resolve_credential`](#cosmodol.connection.resolve_credential)(\*[, credential, ...])   | Resolve a credential into a normalized form that can build a `CosmosClient`.   |
|----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|

### Classes

| [`CosmosConnection`](#cosmodol.connection.CosmosConnection)([credential, ...])   | Holds a resolved credential and a lazy `CosmosClient`.   |
|----------------------------------------------------------------------------------------|----------------------------------------------------------|

### *class* cosmodol.connection.CosmosConnection(credential=None, connection_string=None, endpoint=None, key=None, consistency_level=None, client_kwargs=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Holds a resolved credential and a lazy `CosmosClient`.

This is the dependency-injection seam for the package. Tests construct one pointing
at the emulator without touching any store class.

* **Parameters:**
  * **credential** (`Union`[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`None`](https://docs.python.org/3/builtins/constants.html#None)]) – explicit credential object or account-key string.
  * **connection_string** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – full `AccountEndpoint=...;AccountKey=...` string.
  * **endpoint** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Cosmos endpoint URL (with or without credential).
  * **key** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – account master key.
  * **consistency_level** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – optional override; defaults to inheriting the account default.
  * **client_kwargs** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)) – extra kwargs forwarded to `CosmosClient`.

#### *property* client *: CosmosClient*

The lazily-constructed `CosmosClient`. Cached for the connection’s lifetime.

#### *classmethod* from_anything(source)

Convenience: build a `CosmosConnection` from a thing-or-spec.

Accepts:

> - `CosmosConnection` (returned as-is)
> - `CosmosClient` (wrapped without further resolution)
> - `str` (connection string)
> - `dict` (passed as kwargs)
> - `None` (defer to env / AAD)
* **Return type:**
  [`CosmosConnection`](#cosmodol.connection.CosmosConnection)

### cosmodol.connection.resolve_credential(, credential=None, connection_string=None, endpoint=None, key=None)

Resolve a credential into a normalized form that can build a `CosmosClient`.

Cascade (first hit wins):

1. Explicit `credential=` (with `endpoint=` for the URL)
2. Explicit `connection_string=`
3. Explicit `endpoint=` + `key=` (or just `endpoint=` + AAD)
4. Env var `AZURE_COSMOS_CONNECTION_STRING`
5. Env vars `AZURE_COSMOS_ENDPOINT` + `AZURE_COSMOS_KEY`
6. Env var `AZURE_COSMOS_ENDPOINT` alone + `DefaultAzureCredential`

* **Returns:**
  `{"url": "...", "credential": <obj>}`
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – if no source resolves.
