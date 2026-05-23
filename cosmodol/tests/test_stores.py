"""Unit tests for cosmodol.stores using FakeContainerProxy (no network)."""

import warnings

import pytest

from cosmodol import (
    CosmosItems,
    CosmosPartitionedItems,
    ItemNotFoundError,
    KeyMismatchError,
)
from cosmodol.testing import FakeContainerProxy


# ---------------------------------------------------------------------------
# CosmosItems
# ---------------------------------------------------------------------------


@pytest.fixture
def items():
    return CosmosItems(
        FakeContainerProxy(partition_key_path="/tenant"),
        partition_key_value="t1",
    )


def test_set_get_round_trip(items):
    items["k1"] = {"name": "Alice", "age": 30}
    got = items["k1"]
    assert got == {"name": "Alice", "age": 30, "id": "k1", "tenant": "t1"}


def test_contains_true_and_false(items):
    items["k1"] = {"v": 1}
    assert "k1" in items
    assert "k99" not in items


def test_missing_raises_keyerror_subclass(items):
    with pytest.raises(KeyError) as ei:
        items["nope"]
    assert isinstance(ei.value, ItemNotFoundError)
    assert ei.value.args == ("nope",)


def test_id_mismatch_in_body_raises(items):
    with pytest.raises(KeyMismatchError):
        items["k1"] = {"id": "k_other", "v": 1}


def test_partition_key_mismatch_in_body_raises(items):
    with pytest.raises(KeyMismatchError):
        items["k1"] = {"tenant": "other", "v": 1}


def test_iter_and_len(items):
    items["k1"] = {"v": 1}
    items["k2"] = {"v": 2}
    assert set(items) == {"k1", "k2"}
    assert len(items) == 2


def test_delete(items):
    items["k1"] = {"v": 1}
    del items["k1"]
    assert "k1" not in items


def test_strip_system_fields_default(items):
    items["k1"] = {"v": 1}
    assert "_etag" not in items["k1"]
    assert "_ts" not in items["k1"]


def test_ru_observability(items):
    items["k1"] = {"v": 1}
    assert items.last_request_charge is not None
    assert items.last_request_charge > 0


def test_replace_with_etag_conditional(items):
    items["k1"] = {"v": 1}
    etag = items.last_response_headers.etag
    items.replace("k1", {"v": 2}, etag=etag)
    assert items["k1"]["v"] == 2


def test_replace_with_stale_etag_raises(items):
    items["k1"] = {"v": 1}
    from azure.cosmos.exceptions import CosmosAccessConditionFailedError

    with pytest.raises(CosmosAccessConditionFailedError):
        items.replace("k1", {"v": 2}, etag="stale-etag")


def test_record_ru_callback():
    seen = []
    fake = FakeContainerProxy(partition_key_path="/tenant")
    items = CosmosItems(
        fake, partition_key_value="t1", record_ru=lambda op, ru: seen.append((op, ru))
    )
    items["k1"] = {"v": 1}
    assert seen and seen[-1][0] == "set"
    assert seen[-1][1] > 0


# ---------------------------------------------------------------------------
# CosmosPartitionedItems
# ---------------------------------------------------------------------------


@pytest.fixture
def parts():
    return CosmosPartitionedItems(
        FakeContainerProxy(partition_key_path="/tenant"),
        partition_key_path="/tenant",
    )


def test_partitioned_set_get(parts):
    parts[("A", "k1")] = {"v": 1}
    assert parts[("A", "k1")]["v"] == 1


def test_partitioned_contains(parts):
    parts[("A", "k1")] = {"v": 1}
    assert ("A", "k1") in parts
    assert ("A", "k99") not in parts
    assert "not_a_tuple" not in parts


def test_partitioned_iter_emits_cross_partition_warning(parts):
    parts[("A", "k1")] = {"v": 1}
    parts[("B", "k1")] = {"v": 2}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        keys = set(parts)
    assert any("cross-partition" in str(x.message) for x in w)
    assert keys == {("A", "k1"), ("B", "k1")}


def test_partitioned_silent_full_scan_suppresses_warning():
    p = CosmosPartitionedItems(
        FakeContainerProxy(partition_key_path="/tenant"),
        partition_key_path="/tenant",
        silent_full_scan=True,
    )
    p[("A", "k1")] = {"v": 1}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _ = list(p)
    assert not any("cross-partition" in str(x.message) for x in w)


def test_partitioned_len_disabled_by_default(parts):
    parts[("A", "k1")] = {"v": 1}
    # Raises TypeError (not NotImplementedError) so list-style consumers using
    # operator.length_hint fall back gracefully.
    with pytest.raises(TypeError):
        len(parts)


def test_partitioned_len_via_query():
    p = CosmosPartitionedItems(
        FakeContainerProxy(partition_key_path="/tenant"),
        partition_key_path="/tenant",
        len_via_query=True,
        silent_full_scan=True,
    )
    p[("A", "k1")] = {"v": 1}
    p[("B", "k2")] = {"v": 2}
    assert len(p) == 2


def test_partitioned_partition_narrowing(parts):
    parts[("A", "k1")] = {"v": 1}
    parts[("A", "k2")] = {"v": 2}
    parts[("B", "k3")] = {"v": 3}
    a_only = parts.partition("A")
    assert set(a_only) == {"k1", "k2"}


def test_partitioned_bad_key_raises():
    p = CosmosPartitionedItems(
        FakeContainerProxy(partition_key_path="/tenant"),
        partition_key_path="/tenant",
    )
    with pytest.raises(TypeError):
        p["not_a_tuple"] = {"v": 1}
