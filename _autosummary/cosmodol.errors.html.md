# cosmodol.errors

Custom exceptions and error-translation decorator for cosmodol.

All Azure Cosmos SDK exception handling for the Mapping-shaped methods on close-to-metal
stores funnels through `translate_cosmos_errors` so the auth/throttle vs not-found
distinction stays auditable from one place. See `misc/docs/design_decisions.md` §11.

### Functions

| [`translate_cosmos_errors`](#cosmodol.errors.translate_cosmos_errors)(\*[, key_arg, ...])   | Decorator: translate Cosmos SDK exceptions into `KeyError` subclasses.   |
|------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`validate_cosmos_id`](#cosmodol.errors.validate_cosmos_id)(k)                         | Raise `ValueError` if `k` is not a valid Cosmos item `id`.               |

### Classes

| [`MovedToFunction`](#cosmodol.errors.MovedToFunction)(replacement, \*[, reason])   | Class attribute marking a method that moved to a free function.   |
|-----------------------------------------------------------------------------------------------|-------------------------------------------------------------------|

### Exceptions

| [`ContainerNotEmptyError`](#cosmodol.errors.ContainerNotEmptyError)   | Raised on `del db_store[name]` when the container has items.                                                      |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| [`ContainerNotFoundError`](#cosmodol.errors.ContainerNotFoundError)   | Raised when a Cosmos container does not exist.                                                                    |
| [`CosmosThrottleError`](#cosmodol.errors.CosmosThrottleError)      | Wraps `CosmosHttpResponseError` with HTTP 429 (RU exhaustion).                                                    |
| [`DatabaseNotEmptyError`](#cosmodol.errors.DatabaseNotEmptyError)    | Raised on `del account_store[name]` when the database has containers.                                             |
| [`DatabaseNotFoundError`](#cosmodol.errors.DatabaseNotFoundError)    | Raised when a Cosmos database does not exist.                                                                     |
| [`ItemAlreadyExistsError`](#cosmodol.errors.ItemAlreadyExistsError)   | Raised on strict-create attempts when the item already exists.                                                    |
| [`ItemNotFoundError`](#cosmodol.errors.ItemNotFoundError)        | Raised when an item does not exist for a (partition_key, id).                                                     |
| [`KeyMismatchError`](#cosmodol.errors.KeyMismatchError)         | Raised when a written body's `id` (or partition-key value) disagrees with the inferred value (from the dict key). |

### *exception* cosmodol.errors.ContainerNotEmptyError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Raised on `del db_store[name]` when the container has items. See
`misc/docs/design_decisions.md` §8.

### *exception* cosmodol.errors.ContainerNotFoundError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised when a Cosmos container does not exist.

### *exception* cosmodol.errors.CosmosThrottleError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Wraps `CosmosHttpResponseError` with HTTP 429 (RU exhaustion).

### *exception* cosmodol.errors.DatabaseNotEmptyError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Raised on `del account_store[name]` when the database has containers. See
`misc/docs/design_decisions.md` §8.

### *exception* cosmodol.errors.DatabaseNotFoundError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised when a Cosmos database does not exist.

### *exception* cosmodol.errors.ItemAlreadyExistsError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised on strict-create attempts when the item already exists.

### *exception* cosmodol.errors.ItemNotFoundError

Bases: [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)

Raised when an item does not exist for a (partition_key, id).

### *exception* cosmodol.errors.KeyMismatchError

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

Raised when a written body’s `id` (or partition-key value) disagrees with the
inferred value (from the dict key). See `misc/docs/design_decisions.md` §9.

### *class* cosmodol.errors.MovedToFunction(replacement, , reason='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Class attribute marking a method that moved to a free function.

Instance access raises an `AttributeError` naming the replacement, so
`hasattr(store, name)` is `False` and the error says where to go. Class access
returns the marker itself, which keeps `dol`’s class-wrapping (`delegate_to`
walks `dir(cls)`) working.

### cosmodol.errors.translate_cosmos_errors(\*, key_arg=0, not_found_cls=<class 'cosmodol.errors.ItemNotFoundError'>, exists_cls=<class 'cosmodol.errors.ItemAlreadyExistsError'>)

Decorator: translate Cosmos SDK exceptions into `KeyError` subclasses.

Auth errors and any other Cosmos errors propagate untouched. HTTP 429 throttling is
wrapped in `CosmosThrottleError` so callers can distinguish it. See
`misc/docs/design_decisions.md` §11.

* **Parameters:**
  * **key_arg** ([`int`](https://docs.python.org/3/builtins/functions.html#int) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Position (int) or name (str) of the key argument in the wrapped
    method’s signature. Used to populate `KeyError(key)`. Default 0; for
    methods the user-facing key is typically at index 1 (`self` at 0).
  * **not_found_cls** ([`type`](https://docs.python.org/3/builtins/functions.html#type)[[`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)]) – Exception class to raise on `CosmosResourceNotFoundError`.
  * **exists_cls** ([`type`](https://docs.python.org/3/builtins/functions.html#type)[[`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError)]) – Exception class to raise on `CosmosResourceExistsError`.
* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)

### cosmodol.errors.validate_cosmos_id(k)

Raise `ValueError` if `k` is not a valid Cosmos item `id`.

Cosmos may accept invalid ids on write but the item then becomes unreachable from
the SDK. We fail loudly at write time. See `misc/docs/cosmos_db_reference.md`
§”id rules”.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
