"""Close-to-metal CRUD facade for Cosmos DB.

Functional layer over ``ContainerProxy``. Each function takes the container plus
normalized args and returns ``(value, ResponseHeaders)`` so callers can observe RU
charges and ETags. The Mapping-shaped stores in ``cosmodol.stores`` route all their
work through these.

See ``misc/docs/architecture.md`` Layer A.
"""

from __future__ import annotations

from typing import Any, Iterator, NamedTuple, Optional

from azure.cosmos import ContainerProxy

from cosmodol.errors import translate_cosmos_errors


class ResponseHeaders(NamedTuple):
    """Subset of Cosmos response headers we surface for observability.

    Attributes:
        request_charge: RU consumed by the operation. ``None`` when the emulator (which
            does not populate this header) was the backend.
        etag: ETag of the (created / read / replaced) item, when applicable.
    """

    request_charge: Optional[float]
    etag: Optional[str]


def _headers(container: ContainerProxy) -> ResponseHeaders:
    """Extract our observability tuple from the last response on the container proxy."""
    h = container.client_connection.last_response_headers or {}
    rc = h.get("x-ms-request-charge")
    return ResponseHeaders(
        request_charge=float(rc) if rc is not None else None,
        etag=h.get("etag"),
    )


@translate_cosmos_errors(key_arg="id")
def point_get(
    container: ContainerProxy,
    id: str,
    partition_key: Any,
) -> tuple[dict, ResponseHeaders]:
    """Point read of one item. ~1 RU/KB. Raises ``ItemNotFoundError`` on missing."""
    item = container.read_item(item=id, partition_key=partition_key)
    return dict(item), _headers(container)


@translate_cosmos_errors(key_arg="id")
def point_upsert(
    container: ContainerProxy,
    body: dict,
    *,
    etag: Optional[str] = None,
) -> tuple[dict, ResponseHeaders]:
    """Insert-or-replace an item. ``id`` and the partition-key value are extracted from
    ``body``.

    Note: ``etag`` parameter accepted for symmetry but Cosmos does not honor it on
    ``upsert_item``; use ``point_replace`` for ETag-conditional writes.
    """
    _ = etag  # accepted for symmetry; see docstring
    item = container.upsert_item(body=body)
    return dict(item), _headers(container)


@translate_cosmos_errors(key_arg="id")
def point_replace(
    container: ContainerProxy,
    id: str,
    body: dict,
    partition_key: Any,
    *,
    etag: Optional[str] = None,
) -> tuple[dict, ResponseHeaders]:
    """Full replace of an item. With ``etag``, performs an If-Match conditional write."""
    kwargs = {}
    if etag is not None:
        kwargs["if_match_etag"] = etag
    item = container.replace_item(item=id, body=body, **kwargs)
    return dict(item), _headers(container)


@translate_cosmos_errors(key_arg="id")
def point_delete(
    container: ContainerProxy,
    id: str,
    partition_key: Any,
    *,
    etag: Optional[str] = None,
) -> ResponseHeaders:
    """Point delete. Raises ``ItemNotFoundError`` on missing."""
    kwargs = {}
    if etag is not None:
        kwargs["if_match_etag"] = etag
    container.delete_item(item=id, partition_key=partition_key, **kwargs)
    return _headers(container)


def point_contains(
    container: ContainerProxy,
    id: str,
    partition_key: Any,
) -> tuple[bool, ResponseHeaders]:
    """Existence check via point read. ``True/False``. Never raises ``KeyError``.

    Cheaper than a ``SELECT VALUE COUNT(1)`` query — ~1 RU vs ≥ 2.3 RU.
    """
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        container.read_item(item=id, partition_key=partition_key)
        return True, _headers(container)
    except CosmosResourceNotFoundError:
        return False, _headers(container)


def query(
    container: ContainerProxy,
    sql: str,
    *,
    parameters: Optional[list[dict]] = None,
    partition_key: Any = None,
    cross_partition: bool = False,
    max_item_count: Optional[int] = None,
) -> Iterator[dict]:
    """Run a SQL query. Yields dicts.

    Either pass ``partition_key=`` (cheap, single-partition) or
    ``cross_partition=True`` (RU scales with data). One of the two is required by Cosmos.
    """
    kwargs: dict = {"query": sql}
    if parameters is not None:
        kwargs["parameters"] = parameters
    if partition_key is not None:
        kwargs["partition_key"] = partition_key
    elif cross_partition:
        kwargs["enable_cross_partition_query"] = True
    if max_item_count is not None:
        kwargs["max_item_count"] = max_item_count
    for item in container.query_items(**kwargs):
        # ``SELECT VALUE ...`` queries yield raw scalars (str, int, float, bool, list).
        # Other queries yield CosmosDict (a dict subclass). Pass through either shape
        # so callers can distinguish; convert to plain dict in the dict-shaped case.
        if isinstance(item, dict):
            yield dict(item)
        else:
            yield item


def batch(
    container: ContainerProxy,
    operations: list[tuple],
    partition_key: Any,
) -> list[dict]:
    """Transactional batch within one logical partition.

    Args:
        operations: List of ``(op_name, args_tuple, kwargs_dict)`` triples. Cosmos op
            names: ``"create"``, ``"upsert"``, ``"replace"``, ``"patch"``, ``"read"``,
            ``"delete"``. ≤ 100 ops, ≤ 1.2 MB total.
        partition_key: All ops must share this partition-key value.

    Returns:
        List of per-op result dicts as returned by the SDK.
    """
    results = container.execute_item_batch(
        batch_operations=operations, partition_key=partition_key
    )
    return [dict(r) for r in results]
