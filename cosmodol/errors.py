"""Custom exceptions and error-translation decorator for cosmodol.

All Azure Cosmos SDK exception handling for the Mapping-shaped methods on close-to-metal
stores funnels through ``translate_cosmos_errors`` so the auth/throttle vs not-found
distinction stays auditable from one place. See ``misc/docs/design_decisions.md`` §11.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Callable

from azure.cosmos.exceptions import (
    CosmosResourceExistsError,
    CosmosResourceNotFoundError,
)


def _extract_key(func, args, kwargs, key_arg):
    """Resolve the user-facing key from *args/**kwargs.

    Supports either a positional int index OR a name (resolved via the function's
    signature so it works whether the caller passed by position or by keyword).
    """
    if isinstance(key_arg, int):
        return args[key_arg] if key_arg < len(args) else None
    # By name: prefer kwargs; fall back to positional lookup via signature.
    if key_arg in kwargs:
        return kwargs[key_arg]
    try:
        sig = inspect.signature(func)
        params = list(sig.parameters)
        idx = params.index(key_arg)
        return args[idx] if idx < len(args) else None
    except (ValueError, TypeError):
        return None


class ItemNotFoundError(KeyError):
    """Raised when an item does not exist for a (partition_key, id)."""


class ItemAlreadyExistsError(KeyError):
    """Raised on strict-create attempts when the item already exists."""


class ContainerNotFoundError(KeyError):
    """Raised when a Cosmos container does not exist."""


class DatabaseNotFoundError(KeyError):
    """Raised when a Cosmos database does not exist."""


class ContainerNotEmptyError(RuntimeError):
    """Raised on ``del db_store[name]`` when the container has items. See
    ``misc/docs/design_decisions.md`` §8.
    """


class DatabaseNotEmptyError(RuntimeError):
    """Raised on ``del account_store[name]`` when the database has containers. See
    ``misc/docs/design_decisions.md`` §8.
    """


class KeyMismatchError(ValueError):
    """Raised when a written body's ``id`` (or partition-key value) disagrees with the
    inferred value (from the dict key). See ``misc/docs/design_decisions.md`` §9.
    """


class CosmosThrottleError(RuntimeError):
    """Wraps ``CosmosHttpResponseError`` with HTTP 429 (RU exhaustion)."""


# Cosmos id-string validation.
_ID_FORBIDDEN = set("/\\?#")
_ID_MAX_LEN = 255


def validate_cosmos_id(k) -> None:
    """Raise ``ValueError`` if ``k`` is not a valid Cosmos item ``id``.

    Cosmos may accept invalid ids on write but the item then becomes unreachable from
    the SDK. We fail loudly at write time. See ``misc/docs/cosmos_db_reference.md``
    §"id rules".
    """
    if not isinstance(k, str):
        raise ValueError(
            f"Cosmos item id must be a str, got {type(k).__name__}: {k!r}"
        )
    if not k:
        raise ValueError("Cosmos item id must be a non-empty string.")
    if len(k) > _ID_MAX_LEN:
        raise ValueError(
            f"Cosmos item id length {len(k)} exceeds {_ID_MAX_LEN}: {k!r}"
        )
    bad = sorted(set(k) & _ID_FORBIDDEN)
    if bad:
        raise ValueError(
            f"Cosmos item id contains forbidden characters {bad}: {k!r}. "
            "Use URL-safe base64 if you need to encode arbitrary strings."
        )


def translate_cosmos_errors(
    *,
    key_arg: int | str = 0,
    not_found_cls: type[KeyError] = ItemNotFoundError,
    exists_cls: type[KeyError] = ItemAlreadyExistsError,
) -> Callable:
    """Decorator: translate Cosmos SDK exceptions into ``KeyError`` subclasses.

    Auth errors and any other Cosmos errors propagate untouched. HTTP 429 throttling is
    wrapped in ``CosmosThrottleError`` so callers can distinguish it. See
    ``misc/docs/design_decisions.md`` §11.

    Args:
        key_arg: Position (int) or name (str) of the key argument in the wrapped
            method's signature. Used to populate ``KeyError(key)``. Default 0; for
            methods the user-facing key is typically at index 1 (``self`` at 0).
        not_found_cls: Exception class to raise on ``CosmosResourceNotFoundError``.
        exists_cls: Exception class to raise on ``CosmosResourceExistsError``.
    """
    from azure.cosmos.exceptions import CosmosHttpResponseError

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except CosmosResourceNotFoundError as e:
                k = _extract_key(func, args, kwargs, key_arg)
                raise not_found_cls(k) from e
            except CosmosResourceExistsError as e:
                k = _extract_key(func, args, kwargs, key_arg)
                raise exists_cls(k) from e
            except CosmosHttpResponseError as e:
                if getattr(e, "status_code", None) == 429:
                    raise CosmosThrottleError(str(e)) from e
                raise

        return wrapper

    return decorator
