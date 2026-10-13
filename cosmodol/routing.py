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
it only applies to the leaf object it names. The flip side: a layer that performs the write
in another thread, or later (a write-behind buffer), escapes it, and that write happens as
the leaf's ordinary upsert or delete.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional


class RedirectError(RuntimeError):
    """A wrapping layer did not turn one mapping write into exactly one leaf write."""


#: The leaf methods a redirect guards: while one is pending on a leaf, any of them called
#: with anything but the expected method and key is refused.
WRITE_METHODS = ("__setitem__", "__delitem__")


@dataclass
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


@contextmanager
def redirect(leaf, method: str, key, handler: Callable) -> Iterator[Redirect]:
    """Within the block, the call ``leaf.<method>(key, ...)`` goes to ``handler``.

    Any other write call on ``leaf`` within the block (another method, another key, or
    the same call a second time) raises ``RedirectError`` before touching anything: one
    mapping write was expected to reach the leaf once, as ``key``.
    """
    r = Redirect(leaf, method, key, handler)
    token = _active.set(r)
    try:
        yield r
    finally:
        _active.reset(token)


def claim(leaf, method: str, key) -> Optional[Callable]:
    """The handler redirecting ``leaf.<method>(key, ...)`` right now, or ``None``.

    Called by a leaf at the top of each of its ``WRITE_METHODS``. The returned callable
    runs the handler and records its result or error.
    """
    r = _active.get()
    if r is None or r.leaf is not leaf:
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
