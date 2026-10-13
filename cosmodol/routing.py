"""Route an operation through a wrapped store's own ``__setitem__``/``__delitem__``.

The functions in ``cosmodol.ops`` need Cosmos semantics a mapping write does not have (a
conditional replace, a transactional batch, a cascading delete, a parameterised create).
Calling the leaf directly skips every wrapping layer's own write logic: write guards,
write-through caches, ``preset=`` hooks, scope filters on new keys (i2mint/cosmodol#3).
Instead, an operation *redirects* the leaf: it installs a one-shot handler for one leaf
method, then performs the plain mapping write (``store[k] = v`` / ``del store[k]``) on the
caller's outer store. Every layer runs exactly as for that write; when the call reaches the
leaf, the leaf hands its (fully mapped) arguments to the handler instead of doing its usual
upsert or delete.

>>> class Leaf(dict):
...     def __setitem__(self, k, v):
...         if (handler := claim(self, "__setitem__", k)) is not None:
...             return handler(k, v)
...         super().__setitem__(k, v)
>>> leaf = Leaf()
>>> with redirect(leaf, "__setitem__", "a", lambda k, v: f"handled {k}={v}") as r:
...     leaf["a"] = 1
>>> r.result, dict(leaf)
('handled a=1', {})

The redirect is pinned to the leaf key the operation computed (``dol.inner_most_key``), so a
layer that writes some *other* key first (an index, an audit record) cannot have the
operation's semantics applied to it: that write is refused instead.

The redirect is held in a ``ContextVar``, so it is invisible to other threads and tasks, and
it only applies to the leaf object it names. The flip side is a layer that performs the
write in another thread, or later (a write-behind buffer). Two things are done about it
(i2mint/cosmodol#5):

- While an operation is pending, a write of *the same leaf key* arriving from a context that
  has no redirect (another thread, or a task that did not copy the context) is refused with
  :class:`ConcurrentWriteError`, before it acts, instead of running as an ordinary upsert or
  delete outside the operation's checks. Writes of other keys are not affected, and neither
  is a write through a *different leaf object* on the same container (a ``partition()``
  child, a second ``CosmosItems``): the registry matches leaves by identity.
- A write performed *after* the operation has finished is indistinguishable from any other
  writer's, so it cannot be refused: the operation raises :class:`RedirectError` when the
  write it asked for never reached the leaf, which is the only signal there is.

>>> import threading
>>> started, release = threading.Event(), threading.Event()
>>> def slow(k, v):
...     started.set(); release.wait(5); return "done"
>>> def op():
...     with redirect(leaf, "__setitem__", "a", slow):
...         leaf["a"] = 1
>>> t = threading.Thread(target=op); t.start(); _ = started.wait(5)
>>> try:
...     leaf["a"] = 2  # same key, other thread, while the operation is pending
... except ConcurrentWriteError as e:
...     print("refused")
refused
>>> leaf["b"] = 3  # another key is fine
>>> release.set(); t.join()
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional


class RedirectError(RuntimeError):
    """A wrapping layer did not turn one mapping write into exactly one leaf write."""


class ConcurrentWriteError(RedirectError):
    """A write of a key arrived from another thread or task while an operation on it was pending."""


#: The leaf methods a redirect guards: while one is pending on a leaf, any of them called
#: with anything but the expected method and key is refused.
WRITE_METHODS = ("__setitem__", "__delitem__")


@dataclass(
    eq=False
)  # compared by identity: the registry removes by it, and a leaf's == reads the store
class Redirect:
    """One pending redirect of ``leaf.<method>(key, ...)`` to ``handler``.

    After the block, ``claimed`` says whether the write reached the leaf, ``result`` is
    what the handler returned, and ``error`` what it raised (re-raised by the caller
    even if a layer swallowed it).
    """

    leaf: Any
    method: str
    key: Any
    handler: Callable
    claimed: bool = False
    result: Any = None
    error: Optional[BaseException] = None


_active: ContextVar[Optional[Redirect]] = ContextVar("cosmodol_redirect", default=None)

# Every redirect pending in any thread, so that a write from a context with no redirect can be
# checked against them (a ContextVar is invisible to other threads).
_pending: list[Redirect] = []
_pending_lock = threading.Lock()


@contextmanager
def redirect(leaf, method: str, key, handler: Callable) -> Iterator[Redirect]:
    """Within the block, the call ``leaf.<method>(key, ...)`` goes to ``handler``.

    Any other write call on ``leaf`` within the block (another method, another key, or
    the same call a second time) raises ``RedirectError`` before touching anything: one
    mapping write was expected to reach the leaf once, as ``key``.
    """
    r = Redirect(leaf, method, key, handler)
    token = _active.set(r)
    with _pending_lock:
        _pending.append(r)
    try:
        yield r
    finally:
        try:
            with _pending_lock:
                _pending.remove(r)
        finally:
            _active.reset(token)


def _refuse_if_pending(leaf, method: str, key) -> None:
    """Refuse a write of ``key`` on ``leaf`` while another context has an operation pending on it."""
    with _pending_lock:
        pending = next(
            (p for p in _pending if p.leaf is leaf and _same_key(p.key, key)), None
        )
    if pending is not None:
        raise ConcurrentWriteError(
            f"{method}({key!r}) on the cosmodol store was refused: another thread, task or "
            f"redirect has an operation ({pending.method} of {pending.key!r}) pending on the same "
            "key. Retry once it has finished (i2mint/cosmodol#5)."
        )


def _same_key(a, b) -> bool:
    try:
        return bool(a == b)
    except Exception:  # an exotic key type: treat as different
        return False


def claim(leaf, method: str, key) -> Optional[Callable]:
    """The handler redirecting ``leaf.<method>(key, ...)`` right now, or ``None``.

    Called by a leaf at the top of each of its ``WRITE_METHODS``. The returned callable
    runs the handler and records its result or error.
    """
    r = _active.get()
    if r is None or r.leaf is not leaf:
        _refuse_if_pending(leaf, method, key)
        return None
    if r.claimed:
        outcome = "failed" if r.error is not None else "was performed"
        raise RedirectError(
            f"A wrapping layer made a second {method} call ({key!r}) on the cosmodol "
            f"store during one operation; refused. The first call ({r.key!r}) {outcome}."
        )
    if (method, key) != (r.method, r.key):
        raise RedirectError(
            f"A wrapping layer called {method}({key!r}) on the cosmodol store where "
            f"the operation expected {r.method}({r.key!r}); refused before acting."
        )

    def run(*args):
        r.claimed = True
        try:
            r.result = r.handler(*args)
        except BaseException as e:
            r.error = e
            raise
        return r.result

    return run
