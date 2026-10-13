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
...         if (handler := claim(self, "__setitem__")) is not None:
...             return handler(k, v)
...         super().__setitem__(k, v)
>>> leaf = Leaf()
>>> with redirect(leaf, "__setitem__", lambda k, v: f"handled {k}={v}") as r:
...     leaf["a"] = 1
>>> r.results, dict(leaf)
(['handled a=1'], {})

The redirect is held in a ``ContextVar``, so it is invisible to other threads and tasks, and
it only applies to the leaf object it names.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional


class RedirectError(RuntimeError):
    """A wrapping layer did not turn one mapping write into exactly one leaf write."""


@dataclass
class Redirect:
    """One pending redirect of ``method`` on ``leaf`` to ``handler``.

    ``results`` holds what the handler returned, one entry per leaf call (so after a
    redirect, ``len(results)`` says whether the write reached the leaf).
    """

    leaf: Any
    method: str
    handler: Callable
    results: list = field(default_factory=list)


_active: ContextVar[Optional[Redirect]] = ContextVar("cosmodol_redirect", default=None)


@contextmanager
def redirect(leaf, method: str, handler: Callable) -> Iterator[Redirect]:
    """Within the block, the first call of ``leaf.<method>`` goes to ``handler``.

    A second call of the same method on the same leaf within the block raises
    ``RedirectError``: one mapping write was expected to reach the leaf once.
    """
    r = Redirect(leaf, method, handler)
    token = _active.set(r)
    try:
        yield r
    finally:
        _active.reset(token)


def claim(leaf, method: str) -> Optional[Callable]:
    """The handler redirecting ``leaf.<method>`` right now, or ``None``.

    Called by a leaf at the top of the method. The returned callable runs the handler and
    records its result.
    """
    r = _active.get()
    if r is None or r.leaf is not leaf or r.method != method:
        return None
    if r.results:
        raise RedirectError(
            f"A wrapping layer turned one write into several {method} calls on the "
            "cosmodol store; refusing the extra one. The first was already performed."
        )

    def run(*args):
        r.results.append(None)  # claimed, even if the handler raises
        r.results[-1] = r.handler(*args)
        return r.results[-1]

    return run
