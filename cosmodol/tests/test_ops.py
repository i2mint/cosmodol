"""Key-taking operations honour dol wrappers (i2mint/cosmodol#1).

Every repro from the issue, on both of dol's delegation routes: class-wraps
(``KeyCodecs.prefixed(...)(CosmosItems)(...)``, via ``DelegatedAttribute``) and
instance-wraps (``KeyCodecs.prefixed(...)(CosmosItems(...))``, via ``Store.__getattr__``).
"""

import inspect

import pytest
from azure.cosmos import DatabaseProxy
from dol import KeyCodecs, wrap_kvs

from cosmodol import (
    CosmosAccount,
    CosmosDatabase,
    CosmosItems,
    CosmosPartitionedItems,
    ItemNotFoundError,
    add_container,
    add_database,
    batch_items,
    delete_container,
    delete_database,
    partition,
    replace_item,
)
from cosmodol.testing import FakeContainerProxy

PK = "tenant-1"
PREFIX = "app_"


def _container():
    c = FakeContainerProxy(partition_key_path="/_pk")
    c.upsert_item({"id": "app_doc1", "_pk": PK, "payload": "MINE"})
    c.upsert_item({"id": "doc1", "_pk": PK, "payload": "SOMEONE ELSE'S"})
    return c


def _state(c):
    return {i: d["payload"] for (_pk, i), d in sorted(c._data.items())}


def _class_wrapped(c):
    return KeyCodecs.prefixed(PREFIX)(CosmosItems)(
        c, partition_key_value=PK, partition_key_path="/_pk"
    )


def _instance_wrapped(c):
    return KeyCodecs.prefixed(PREFIX)(
        CosmosItems(c, partition_key_value=PK, partition_key_path="/_pk")
    )


ROUTES = pytest.mark.parametrize(
    "wrap", [_class_wrapped, _instance_wrapped], ids=["class-wrap", "instance-wrap"]
)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@ROUTES
def test_replace_item_maps_the_key(wrap):
    c = _container()
    w = wrap(c)
    assert w["doc1"]["id"] == "app_doc1"
    replace_item(w, "doc1", {"payload": "UPDATED"})
    assert _state(c) == {"app_doc1": "UPDATED", "doc1": "SOMEONE ELSE'S"}


def test_replace_item_through_a_temporary_wrapper():
    """The lost-reference case that defeats ``wrapped_self``: nothing else holds the
    wrapper, and the function is still right because it is handed the wrapper."""
    c = _container()
    replace_item(_instance_wrapped(c), "doc1", {"payload": "UPDATED"})
    assert _state(c)["app_doc1"] == "UPDATED"


def test_replace_item_unwrapped_is_unchanged():
    c = _container()
    leaf = CosmosItems(c, partition_key_value=PK, partition_key_path="/_pk")
    replace_item(leaf, "doc1", {"payload": "NEW"})
    assert _state(c) == {"app_doc1": "MINE", "doc1": "NEW"}


@ROUTES
def test_replace_item_missing_mapped_key_is_loud(wrap):
    c = _container()
    with pytest.raises(ItemNotFoundError):
        replace_item(wrap(c), "nope", {"payload": "x"})
    assert _state(c) == {"app_doc1": "MINE", "doc1": "SOMEONE ELSE'S"}


def test_replace_item_applies_value_codec():
    c = _container()
    Encoded = wrap_kvs(
        KeyCodecs.prefixed(PREFIX)(CosmosItems),
        data_of_obj=lambda s: {"payload": s.upper()},
        obj_of_data=lambda d: d["payload"].lower(),
    )
    w = Encoded(c, partition_key_value=PK, partition_key_path="/_pk")
    replace_item(w, "doc1", "shout")
    assert _state(c)["app_doc1"] == "SHOUT"
    assert w["doc1"] == "shout"


@ROUTES
def test_batch_items_maps_every_key(wrap):
    c = _container()
    c.upsert_item({"id": "app_gone", "_pk": PK, "payload": "x"})
    c.upsert_item({"id": "gone", "_pk": PK, "payload": "keep"})
    batch_items(
        wrap(c),
        [
            ("upsert", "new", {"payload": "N"}),
            ("replace", "doc1", {"payload": "R"}),
            ("delete", "gone"),
        ],
    )
    assert _state(c) == {
        "app_doc1": "R",
        "app_new": "N",
        "doc1": "SOMEONE ELSE'S",
        "gone": "keep",
    }


def test_batch_items_rejects_sdk_triples_and_bad_shapes():
    w = _class_wrapped(_container())
    with pytest.raises(ValueError, match="Unknown batch op"):
        batch_items(w, [("frobnicate", "k")])
    with pytest.raises(ValueError, match=r"takes \(op, key\)"):
        batch_items(w, [("delete", "doc1", "extra", {})])
    with pytest.raises(ValueError, match=r"takes \(op, key, value\)"):
        batch_items(w, [("upsert", "doc1")])


def test_functions_validate_their_first_argument():
    with pytest.raises(TypeError, match="needs a CosmosItems"):
        replace_item({"doc1": {}}, "doc1", {})
    with pytest.raises(TypeError, match="needs a CosmosPartitionedItems"):
        partition(_class_wrapped(_container()), PK)


# ---------------------------------------------------------------------------
# partition
# ---------------------------------------------------------------------------


def _redacted_partitioned(c):
    Redacted = wrap_kvs(
        CosmosPartitionedItems, obj_of_data=lambda d: {**d, "payload": "<redacted>"}
    )
    return Redacted(c, partition_key_path="/_pk", silent_full_scan=True)


def test_partition_refuses_to_strip_wrappers():
    p = _redacted_partitioned(_container())
    assert p[(PK, "doc1")]["payload"] == "<redacted>"
    with pytest.raises(TypeError, match="partition\\(\\) refused"):
        partition(p, PK)


def test_partition_wrap_rewraps_the_child():
    p = _redacted_partitioned(_container())
    sub = partition(p, PK, wrap=wrap_kvs(obj_of_data=lambda d: {**d, "payload": "-"}))
    assert sub["doc1"]["payload"] == "-"


def test_partition_unwrapped_returns_leaf():
    leaf = CosmosPartitionedItems(_container(), partition_key_path="/_pk")
    sub = partition(leaf, PK)
    assert type(sub) is CosmosItems
    assert sub["doc1"]["payload"] == "SOMEONE ELSE'S"


# ---------------------------------------------------------------------------
# Trees (hand-rolled backend fakes; CosmosDatabase / CosmosAccount are the real ones)
# ---------------------------------------------------------------------------


class _FakeContainer:
    def __init__(self, cid, items, *, pk_path="/tenant"):
        self.id, self.items, self.pk_path = cid, list(items), pk_path
        self.deleted = []

    def read(self):
        return {"id": self.id, "partitionKey": {"paths": [self.pk_path]}}

    def query_items(self, query, enable_cross_partition_query=False, **kw):
        prop = self.pk_path.strip("/")
        for i in self.items:
            yield {"id": i["id"], "pk": i.get(prop)} if "AS pk" in query else dict(i)

    def delete_item(self, item, partition_key=None):
        self.deleted.append((item, partition_key))
        self.items = [i for i in self.items if i["id"] != item]


class _FakeDatabase(DatabaseProxy):
    def __init__(self, containers, *, dbid="db"):
        self.id, self.containers, self.created = dbid, containers, []

    def read(self):
        return {"id": self.id}

    def list_containers(self):
        return [{"id": n} for n in self.containers]

    def get_container_client(self, k):
        if k not in self.containers:
            raise LookupError(k)
        return self.containers[k]

    def create_container(self, id, **kw):
        self.created.append(id)
        self.containers[id] = _FakeContainer(id, [])
        return self.containers[id]

    def delete_container(self, k):
        del self.containers[k]


def _db():
    return _FakeDatabase(
        {
            "app_events": _FakeContainer("app_events", [{"id": "e1", "tenant": "t"}]),
            "events": _FakeContainer(
                "events", [{"id": f"prod-{i}", "tenant": "t"} for i in range(3)]
            ),
        }
    )


DB_ROUTES = pytest.mark.parametrize(
    "wrap",
    [
        lambda db: KeyCodecs.prefixed(PREFIX)(CosmosDatabase)(db),
        lambda db: KeyCodecs.prefixed(PREFIX)(CosmosDatabase(db)),
    ],
    ids=["class-wrap", "instance-wrap"],
)


@DB_ROUTES
def test_delete_container_force_drops_the_mapped_container(wrap):
    db = _db()
    events = db.containers["events"]
    view = wrap(db)
    assert view["events"].id == "app_events"
    delete_container(view, "events", force=True)
    assert sorted(db.containers) == ["events"]
    assert len(events.items) == 3


def test_delete_container_force_passes_the_real_partition_key():
    db = _db()
    target = db.containers["events"]
    delete_container(CosmosDatabase(db), "events", force=True)
    assert target.deleted == [(f"prod-{i}", "t") for i in range(3)]


@DB_ROUTES
def test_delete_container_without_force_is_delitem(wrap):
    db = _db()
    with pytest.raises(Exception, match="not empty"):
        delete_container(wrap(db), "events")
    assert sorted(db.containers) == ["app_events", "events"]


@DB_ROUTES
def test_add_container_maps_the_name(wrap):
    db = _db()
    add_container(wrap(db), "logs")
    assert db.created == ["app_logs"]


class _FakeClient:
    def __init__(self, dbs):
        self.dbs = dbs

    def list_databases(self):
        return [{"id": n} for n in self.dbs]

    def get_database_client(self, k):
        if k not in self.dbs:
            raise LookupError(k)
        return self.dbs[k]

    def create_database(self, id, offer_throughput=None, **kw):
        self.dbs[id] = _FakeDatabase({}, dbid=id)
        return self.dbs[id]

    def delete_database(self, k):
        del self.dbs[k]


class _FakeConnection:
    def __init__(self, client):
        self.client = client


def _account(wrap_cls):
    client = _FakeClient(
        {
            "app_main": _FakeDatabase({"c": _FakeContainer("c", [])}, dbid="app_main"),
            "main": _FakeDatabase({"prod": _FakeContainer("prod", [])}, dbid="main"),
        }
    )
    from unittest.mock import patch

    with patch(
        "cosmodol.trees.CosmosConnection.from_anything",
        side_effect=lambda c: c,
    ):
        acct = (
            KeyCodecs.prefixed(PREFIX)(CosmosAccount)(_FakeConnection(client))
            if wrap_cls
            else KeyCodecs.prefixed(PREFIX)(CosmosAccount(_FakeConnection(client)))
        )
    return acct, client


@pytest.mark.parametrize("wrap_cls", [True, False], ids=["class-wrap", "instance-wrap"])
def test_delete_database_force_drops_the_mapped_database(wrap_cls):
    acct, client = _account(wrap_cls)
    delete_database(acct, "main", force=True)
    assert sorted(client.dbs) == ["main"]
    assert "prod" in client.dbs["main"].containers


@pytest.mark.parametrize("wrap_cls", [True, False], ids=["class-wrap", "instance-wrap"])
def test_add_database_maps_the_name(wrap_cls):
    acct, client = _account(wrap_cls)
    db = add_database(acct, "extra")
    assert "app_extra" in client.dbs and "extra" not in client.dbs
    assert db.name == "app_extra"


# ---------------------------------------------------------------------------
# The moved methods, and the conformance inventory (s3dol ADR-0011 D5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj, name, replacement",
    [
        (lambda: _class_wrapped(_container()), "replace", "cosmodol.replace_item"),
        (lambda: _instance_wrapped(_container()), "batch", "cosmodol.batch_items"),
        (
            lambda: CosmosPartitionedItems(_container(), partition_key_path="/_pk"),
            "partition",
            "cosmodol.partition",
        ),
        (lambda: CosmosDatabase(_db()), "delete", "cosmodol.delete_container"),
        (
            lambda: KeyCodecs.prefixed(PREFIX)(CosmosDatabase)(_db()),
            "add_container",
            "cosmodol.add_container",
        ),
    ],
)
def test_moved_methods_say_where_they_went(obj, name, replacement):
    o = obj()
    assert not hasattr(o, name)
    with pytest.raises(AttributeError, match=replacement):
        getattr(o, name)


#: Every public method of every store class, reviewed for taking a key at any position
#: (or nested in a structure). A NEW method fails this test on purpose: decide whether it
#: takes a key, and if it does, make it a free function in ``cosmodol.ops`` instead.
PUBLIC_METHOD_INVENTORY = {
    # ``query``: takes SQL, not a store key; documented as speaking leaf ids/documents.
    CosmosItems: {"query"},
    CosmosPartitionedItems: {"query"},
    CosmosDatabase: set(),
    CosmosAccount: set(),
}


@pytest.mark.parametrize("cls", list(PUBLIC_METHOD_INVENTORY), ids=lambda c: c.__name__)
def test_no_unreviewed_public_methods(cls):
    from dol.base import KvPersister

    inherited = set(dir(KvPersister))
    own = {
        name
        for name, attr in inspect.getmembers(cls)
        if not name.startswith("_")
        and (name not in inherited or name in vars(cls))  # incl. overridden Mapping API
        and callable(attr)  # functions, classmethods, staticmethods
        and not isinstance(attr, type)
    }
    assert own == PUBLIC_METHOD_INVENTORY[cls]


def test_inventory_sees_classmethods_and_overrides():
    class Grown(CosmosItems):
        @classmethod
        def wipe(cls, k): ...

        def get(self, k, default=None): ...

    with pytest.raises(AssertionError):
        PUBLIC_METHOD_INVENTORY[Grown] = PUBLIC_METHOD_INVENTORY[CosmosItems]
        try:
            test_no_unreviewed_public_methods(Grown)
        finally:
            del PUBLIC_METHOD_INVENTORY[Grown]


# ---------------------------------------------------------------------------
# Adversarial-review findings
# ---------------------------------------------------------------------------


def _scoped_items(c):
    from dol import filt_iter

    return filt_iter(
        CosmosItems(c, partition_key_value=PK, partition_key_path="/_pk"),
        filt=lambda k: k.startswith(PREFIX),
    )


def test_scope_filter_stops_replace_and_batch_on_out_of_scope_keys():
    c = _container()
    view = _scoped_items(c)
    with pytest.raises(ItemNotFoundError):
        replace_item(view, "doc1", {"payload": "OVERWRITTEN"})
    with pytest.raises(ItemNotFoundError):
        batch_items(view, [("delete", "doc1")])
    assert _state(c) == {"app_doc1": "MINE", "doc1": "SOMEONE ELSE'S"}
    replace_item(view, "app_doc1", {"payload": "OK"})  # in scope: allowed
    assert _state(c)["app_doc1"] == "OK"


def test_batch_key_created_earlier_in_the_batch_needs_no_membership():
    c = _container()
    batch_items(
        _class_wrapped(c), [("upsert", "fresh", {"payload": "1"}), ("replace", "fresh", {"payload": "2"})]
    )
    assert _state(c)["app_fresh"] == "2"


def test_batch_per_op_kwargs_and_sdk_triples():
    c = _container()
    etag = c._data[(PK, "app_doc1")]["_etag"]
    batch_items(_class_wrapped(c), [("replace", "doc1", {"payload": "E"}, {"if_match_etag": etag})])
    assert _state(c)["app_doc1"] == "E"
    with pytest.raises(ValueError, match="SDK"):
        batch_items(_class_wrapped(c), [("upsert", ({"id": "x"},), {})])


def test_scope_filter_stops_forced_container_delete():
    from dol import filt_iter

    db = _db()
    view = filt_iter(CosmosDatabase(db), filt=lambda k: k.startswith(PREFIX))
    with pytest.raises(Exception, match="events"):
        delete_container(view, "events", force=True)
    assert sorted(db.containers) == ["app_events", "events"]


def test_add_database_does_not_raise_after_creating():
    from dol import filt_iter

    acct, client = _account(True)
    hidden = filt_iter(acct, filt=lambda k: k.startswith("x"))
    db = add_database(hidden, "extra")
    assert db.name == "app_extra" and "app_extra" in client.dbs


def test_forced_delete_handles_hierarchical_and_missing_partition_keys():
    from cosmodol.ops import NonePartitionKeyValue, _partition_key_expr

    multi = _FakeContainer("m", [])
    multi.read = lambda: {"id": "m", "partitionKey": {"paths": ["/t", "/u/v"], "kind": "MultiHash"}}
    assert _partition_key_expr(multi) == '[c["t"], c["u"]["v"]]'

    db = _db()
    target = db.containers["events"]
    target.query_items = lambda query, **kw: iter([{"id": "a", "pk": ["T", "U"]}, {"id": "b"}])
    delete_container(CosmosDatabase(db), "events", force=True)
    assert target.deleted == [("a", ["T", "U"]), ("b", NonePartitionKeyValue)]
