"""Ops functions run every wrapping layer's own write logic (i2mint/cosmodol#3).

Write guards, write-through caches, ``preset=`` hooks and scope filters on new keys all
live in a layer's ``__setitem__``/``__delitem__``; the functions in ``cosmodol.ops`` now
perform their writes through those methods (see ``cosmodol.routing``).
"""

import pytest
from dol import KeyCodecs, Store, filt_iter, wrap_kvs
from dol.trans import mk_read_only

from cosmodol import (
    CosmosDatabase,
    CosmosItems,
    add_container,
    add_database,
    batch_items,
    delete_container,
    delete_database,
    replace_item,
)
from cosmodol.routing import RedirectError
from cosmodol.tests.test_ops import PK, _account, _container, _db, _state


def _leaf(c):
    return CosmosItems(c, partition_key_value=PK, partition_key_path="/_pk")


class _ReadOnly(Store):
    def __setitem__(self, k, v):
        raise PermissionError("read-only")

    def __delitem__(self, k):
        raise PermissionError("read-only")


class _WriteThrough(Store):
    def __init__(self, store):
        super().__init__(store)
        self.cache = {}

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self.cache[k] = v


class _Swallow(Store):
    def __setitem__(self, k, v):
        pass


class _Twice(Store):
    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        super().__setitem__(k + "_copy", v)


# ---------------------------------------------------------------------------
# Write guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "guarded",
    [
        lambda c: (_ReadOnly(_leaf(c)), "app_doc1"),
        lambda c: (KeyCodecs.prefixed("app_")(_ReadOnly(_leaf(c))), "doc1"),
        lambda c: (
            mk_read_only(type("RO", (CosmosItems,), {}))(
                c, partition_key_value=PK, partition_key_path="/_pk"
            ),
            "app_doc1",
        ),
    ],
    ids=["layer", "layer-under-codec", "read-only-subclass"],
)
def test_write_guard_refuses_item_ops(guarded):
    c = _container()
    before = _state(c)
    store, k = guarded(c)
    with pytest.raises((PermissionError, ValueError)):
        replace_item(store, k, {"payload": "X"})
    with pytest.raises((PermissionError, ValueError)):
        batch_items(store, [("upsert", "new", {"payload": "N"})])
    with pytest.raises((PermissionError, ValueError)):
        batch_items(store, [("delete", k)])
    assert _state(c) == before


def test_write_guard_refuses_tree_ops():
    raw = _db()
    db = _ReadOnly(CosmosDatabase(raw))
    with pytest.raises(PermissionError):
        add_container(db, "logs")
    with pytest.raises(PermissionError):
        delete_container(db, "events", force=True)
    assert sorted(raw.containers) == ["app_events", "events"] and not raw.created

    acct, client = _account(False)
    ro = _ReadOnly(acct)
    with pytest.raises(PermissionError):
        add_database(ro, "extra")
    with pytest.raises(PermissionError):
        delete_database(ro, "main", force=True)
    assert sorted(client.dbs) == ["app_main", "main"]


# ---------------------------------------------------------------------------
# Hooks and caches
# ---------------------------------------------------------------------------


def test_preset_hook_runs_for_replace_and_batch():
    c = _container()
    stamped = wrap_kvs(_leaf(c), preset=lambda k, v: {**v, "payload": f"{k}:{v['payload']}"})
    replace_item(stamped, "doc1", {"payload": "R"})
    batch_items(stamped, [("upsert", "new", {"payload": "N"})])
    assert _state(c)["doc1"] == "doc1:R" and _state(c)["new"] == "new:N"


def test_write_through_cache_sees_the_replace_and_not_a_failed_one():
    c = _container()
    store = _WriteThrough(_leaf(c))
    replace_item(store, "doc1", {"payload": "R"})
    assert store.cache == {"doc1": {"payload": "R"}}
    with pytest.raises(Exception):
        replace_item(store, "doc1", {"payload": "stale"}, etag="not-the-etag")
    assert store.cache == {"doc1": {"payload": "R"}} and _state(c)["doc1"] == "R"


# ---------------------------------------------------------------------------
# Scope on creation
# ---------------------------------------------------------------------------


def test_scope_filter_stops_creating_ops():
    c = _container()
    scoped = filt_iter(_leaf(c), filt=lambda k: k.startswith("app_"))
    before = _state(c)
    with pytest.raises(KeyError):
        batch_items(
            scoped,
            [("upsert", "app_ok", {"payload": "ok"}), ("create", "other", {"payload": "x"})],
        )
    assert _state(c) == before  # nothing was committed: the batch never left

    raw = _db()
    db = filt_iter(CosmosDatabase(raw), filt=lambda k: k.startswith("app_"))
    with pytest.raises(KeyError):
        add_container(db, "logs")
    add_container(db, "app_logs")
    assert raw.created == ["app_logs"]


# ---------------------------------------------------------------------------
# Layers that do not turn one write into one leaf write
# ---------------------------------------------------------------------------


def test_a_swallowed_write_is_loud():
    c = _container()
    before = _state(c)
    with pytest.raises(RedirectError, match="did not reach"):
        replace_item(_Swallow(_leaf(c)), "doc1", {"payload": "X"})
    with pytest.raises(RedirectError, match="did not reach"):
        batch_items(_Swallow(_leaf(c)), [("upsert", "doc1", {"payload": "X"})])
    assert _state(c) == before


def test_a_multiplied_write_is_loud_and_a_batch_sends_nothing():
    c = _container()
    before = _state(c)
    with pytest.raises(RedirectError, match="several"):
        batch_items(_Twice(_leaf(c)), [("upsert", "doc1", {"payload": "X"})])
    assert _state(c) == before


def test_no_redirect_outlives_its_operation():
    c = _container()
    leaf = _leaf(c)
    store = _WriteThrough(leaf)
    with pytest.raises(Exception):
        replace_item(store, "absent", {"payload": "X"})
    store["plain"] = {"payload": "P"}  # an ordinary upsert again
    del store["doc1"]
    assert _state(c)["plain"] == "P" and "doc1" not in _state(c)


# ---------------------------------------------------------------------------
# Patch has no mapping-write equivalent
# ---------------------------------------------------------------------------


def test_patch_on_a_wrapped_store_needs_an_explicit_opt_in():
    c = _container()
    store = KeyCodecs.prefixed("app_")(_leaf(c))
    ops = [("patch", "doc1", [{"op": "set", "path": "/payload", "value": "P"}])]
    with pytest.raises(TypeError, match="unguarded_patch"):
        batch_items(store, ops)
    with pytest.raises(NotImplementedError, match="patch"):  # reached the (fake) SDK
        batch_items(store, ops, unguarded_patch=True)
    with pytest.raises(NotImplementedError, match="patch"):  # unwrapped: no opt-in needed
        batch_items(_leaf(c), [("patch", "app_doc1", ops[0][2])])
