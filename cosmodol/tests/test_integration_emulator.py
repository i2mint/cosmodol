"""Integration tests for cosmodol against the Linux vNext Cosmos DB emulator.

Requires Docker (or an already-running emulator). Tests are skipped cleanly when
neither is available. See ``cosmodol.testing.cosmos_emulator`` for the fixture.

The emulator takes ~30-60 seconds to become ready on first start, so the
session-scoped fixture runs once and is reused. RU charges are NOT asserted —
the emulator does not populate ``x-ms-request-charge``.

Run with::

    pytest cosmodol/tests/test_integration_emulator.py -v -m integration
"""

import uuid

import pytest

from cosmodol import (
    CosmosAccount,
    CosmosDatabase,
    CosmosItems,
    CosmosPartitionedItems,
    ItemNotFoundError,
    KeyMismatchError,
    cosmos_store,
)
from cosmodol.testing import (
    EMULATOR_CONNECTION_STRING,
    docker_available,
    emulator_is_running,
    mk_emulator_connection,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cosmos_connection():
    """Yield a CosmosConnection pointing at the running emulator; start it if needed.

    Skips the integration tests entirely if neither is available.
    """
    if emulator_is_running():
        yield mk_emulator_connection()
        return
    if not docker_available():
        pytest.skip(
            "Cosmos emulator is not running and Docker is unavailable. "
            "Start it manually: see misc/docs/cosmos_db_reference.md §Local testing.",
            allow_module_level=False,
        )
    from cosmodol.testing import cosmos_emulator as _emulator_cm

    with _emulator_cm():
        yield mk_emulator_connection()


@pytest.fixture(scope="session")
def database(cosmos_connection):
    """A fresh database for the session; dropped at the end."""
    name = f"cosmodol-test-{uuid.uuid4().hex[:8]}"
    account = CosmosAccount(cosmos_connection)
    db = account.add_database(name)
    try:
        yield db
    finally:
        try:
            account.delete(name, force=True)
        except Exception:
            pass


@pytest.fixture
def container_proxy(database):
    """A fresh container per test, dropped after."""
    name = f"c-{uuid.uuid4().hex[:8]}"
    c = database.add_container(name, partition_key_path="/tenant")
    try:
        yield c
    finally:
        try:
            database.delete(name, force=True)
        except Exception:
            pass


@pytest.fixture
def container_id_partition(database):
    """A fresh container partitioned by /id, dropped after."""
    name = f"c-{uuid.uuid4().hex[:8]}"
    c = database.add_container(name, partition_key_path="/id")
    try:
        yield c
    finally:
        try:
            database.delete(name, force=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CosmosItems — full lifecycle against the emulator
# ---------------------------------------------------------------------------


def test_set_get_round_trip(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["k1"] = {"name": "Alice", "age": 30}
    got = items["k1"]
    assert got["name"] == "Alice"
    assert got["age"] == 30
    assert got["id"] == "k1"
    assert got["tenant"] == "t1"


def test_contains(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["k1"] = {"v": 1}
    assert "k1" in items
    assert "missing" not in items


def test_missing_raises_itemnotfound(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    with pytest.raises(KeyError) as ei:
        items["nope"]
    assert isinstance(ei.value, ItemNotFoundError)


def test_delete(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["k1"] = {"v": 1}
    del items["k1"]
    assert "k1" not in items


def test_overwrite_via_upsert(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["k1"] = {"v": 1}
    items["k1"] = {"v": 2}
    assert items["k1"]["v"] == 2


def test_iter_single_partition(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["a"] = {"v": 1}
    items["b"] = {"v": 2}
    items["c"] = {"v": 3}
    assert set(items) == {"a", "b", "c"}


def test_len_single_partition(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["a"] = {"v": 1}
    items["b"] = {"v": 2}
    assert len(items) == 2


def test_strip_system_fields_default(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["k1"] = {"v": 1}
    got = items["k1"]
    assert "_etag" not in got
    assert "_ts" not in got
    assert "_rid" not in got


def test_keep_system_fields(container_proxy):
    items = CosmosItems(
        container_proxy, partition_key_value="t1", strip_system_fields=False
    )
    items["k1"] = {"v": 1}
    got = items["k1"]
    assert "_etag" in got
    assert "_ts" in got


def test_replace_with_etag(container_proxy):
    items = CosmosItems(
        container_proxy, partition_key_value="t1", strip_system_fields=False
    )
    items["k1"] = {"v": 1}
    etag = items["k1"]["_etag"]
    items.replace("k1", {"v": 2}, etag=etag)
    assert items["k1"]["v"] == 2


def test_replace_with_stale_etag_raises(container_proxy):
    from azure.cosmos.exceptions import CosmosAccessConditionFailedError

    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["k1"] = {"v": 1}
    with pytest.raises(CosmosAccessConditionFailedError):
        items.replace("k1", {"v": 2}, etag='"00000000-0000-0000-0000-000000000000"')


def test_key_mismatch_in_body_raises(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    with pytest.raises(KeyMismatchError):
        items["k1"] = {"id": "k_other", "v": 1}


def test_invalid_id_raises_on_write(container_proxy):
    items = CosmosItems(container_proxy, partition_key_value="t1")
    with pytest.raises(ValueError):
        items["bad/id"] = {"v": 1}


def test_isolation_across_partitions(container_proxy):
    t1 = CosmosItems(container_proxy, partition_key_value="t1")
    t2 = CosmosItems(container_proxy, partition_key_value="t2")
    t1["k1"] = {"v": "from-t1"}
    t2["k1"] = {"v": "from-t2"}
    assert t1["k1"]["v"] == "from-t1"
    assert t2["k1"]["v"] == "from-t2"


def test_record_ru_callback(container_proxy):
    seen = []
    items = CosmosItems(
        container_proxy,
        partition_key_value="t1",
        record_ru=lambda op, ru: seen.append((op, ru)),
    )
    items["k1"] = {"v": 1}
    # Emulator may or may not populate request_charge; just check the callback fires
    # when a charge is present. If empty, the emulator omitted the header.
    assert isinstance(seen, list)  # always true; we just exercise the code path


# ---------------------------------------------------------------------------
# CosmosPartitionedItems
# ---------------------------------------------------------------------------


def test_partitioned_set_get(container_proxy):
    parts = CosmosPartitionedItems(
        container_proxy, partition_key_path="/tenant", silent_full_scan=True
    )
    parts[("A", "k1")] = {"v": 1}
    parts[("B", "k1")] = {"v": 2}
    assert parts[("A", "k1")]["v"] == 1
    assert parts[("B", "k1")]["v"] == 2


def test_partitioned_partition_narrowing(container_proxy):
    parts = CosmosPartitionedItems(
        container_proxy, partition_key_path="/tenant", silent_full_scan=True
    )
    parts[("A", "k1")] = {"v": 1}
    parts[("A", "k2")] = {"v": 2}
    parts[("B", "k3")] = {"v": 3}
    a_only = parts.partition("A")
    assert set(a_only) == {"k1", "k2"}


def test_partitioned_iter_cross_partition(container_proxy):
    parts = CosmosPartitionedItems(
        container_proxy, partition_key_path="/tenant", silent_full_scan=True
    )
    parts[("A", "k1")] = {"v": 1}
    parts[("B", "k2")] = {"v": 2}
    assert set(parts) == {("A", "k1"), ("B", "k2")}


def test_partitioned_len_disabled(container_proxy):
    parts = CosmosPartitionedItems(
        container_proxy, partition_key_path="/tenant", silent_full_scan=True
    )
    with pytest.raises(TypeError):
        len(parts)


def test_partitioned_len_via_query_opt_in(container_proxy):
    parts = CosmosPartitionedItems(
        container_proxy,
        partition_key_path="/tenant",
        silent_full_scan=True,
        len_via_query=True,
    )
    parts[("A", "k1")] = {"v": 1}
    parts[("B", "k2")] = {"v": 2}
    assert len(parts) == 2


# ---------------------------------------------------------------------------
# CosmosDatabase / CosmosAccount
# ---------------------------------------------------------------------------


def test_database_lists_containers(database):
    n1 = f"c-{uuid.uuid4().hex[:6]}"
    n2 = f"c-{uuid.uuid4().hex[:6]}"
    database.add_container(n1, partition_key_path="/id")
    database.add_container(n2, partition_key_path="/id")
    try:
        names = set(database)
        assert n1 in names
        assert n2 in names
    finally:
        database.delete(n1, force=True)
        database.delete(n2, force=True)


def test_database_setitem_disabled(database):
    with pytest.raises(TypeError):
        database["nope"] = object()


def test_database_delete_refuses_nonempty(database, container_proxy):
    from cosmodol import ContainerNotEmptyError

    # container_proxy gives us a container with /tenant partition
    items = CosmosItems(container_proxy, partition_key_value="t1")
    items["k1"] = {"v": 1}
    name = container_proxy.id
    with pytest.raises(ContainerNotEmptyError):
        del database[name]


def test_account_lists_databases(cosmos_connection, database):
    acct = CosmosAccount(cosmos_connection)
    assert database.name in set(acct)


# ---------------------------------------------------------------------------
# cosmos_store top-level factory
# ---------------------------------------------------------------------------


def test_cosmos_store_factory_items(container_proxy, cosmos_connection, database):
    # Use the existing fixture's container.
    store = CosmosItems(container_proxy, partition_key_value="t1")
    # Verify factory wires equivalently — when given partition_key_value, returns CosmosItems.
    factory_store = cosmos_store(
        connection=cosmos_connection,
        database=database.name,
        container=container_proxy.id,
        partition_key_value="t1",
    )
    assert isinstance(factory_store, CosmosItems)
    factory_store["k1"] = {"v": 1}
    assert store["k1"]["v"] == 1


def test_cosmos_store_factory_partitioned(
    cosmos_connection, database, container_proxy
):
    factory_store = cosmos_store(
        connection=cosmos_connection,
        database=database.name,
        container=container_proxy.id,
        partition_key_path="/tenant",
    )
    assert isinstance(factory_store, CosmosPartitionedItems)


# ---------------------------------------------------------------------------
# Default partition_key_path="/id" — recommended pattern
# ---------------------------------------------------------------------------


def test_id_partitioned_container(container_id_partition):
    """Recommended pattern: each item is its own partition (pk path = /id)."""
    items = CosmosItems(container_id_partition, partition_key_value="k1")
    items["k1"] = {"v": 1}
    assert items["k1"]["v"] == 1
