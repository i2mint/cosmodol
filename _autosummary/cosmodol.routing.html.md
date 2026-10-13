# cosmodol.routing

Route an operation through a wrapped store’s own `__setitem__`/`__delitem__`.

The functions in `cosmodol.ops` need Cosmos semantics a mapping write does not have (a
conditional replace, a transactional batch, a cascading delete, a parameterised create).
Calling the leaf directly skips every wrapping layer’s own write logic: write guards,
write-through caches, `preset=` hooks, scope filters on new keys (i2mint/cosmodol#3).
Instead, an operation *redirects* the leaf: it installs a one-shot handler for one leaf
method, then performs the plain mapping write (`store[k] = v` / `del store[k]`) on the
caller’s outer store. Every layer runs exactly as for that write; when the call reaches the
leaf, the leaf hands its (fully mapped) arguments to the handler instead of doing its usual
upsert or delete.

```pycon
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
```

The redirect is pinned to the leaf key the operation computed (`dol.inner_most_key`), so a
layer that writes some *other* key first (an index, an audit record) cannot have the
operation’s semantics applied to it: that write is refused instead.

The redirect is held in a `ContextVar`, so it is invisible to other threads and tasks, and
it only applies to the leaf object it names. The flip side is a layer that performs the
write in another thread, or later (a write-behind buffer). Two things are done about it
(i2mint/cosmodol#5):

- While an operation is pending, a write of *the same leaf key* arriving from a context that
  has no redirect (another thread, or a task that did not copy the context) is refused with
  [`ConcurrentWriteError`](#cosmodol.routing.ConcurrentWriteError), before it acts, instead of running as an ordinary upsert or
  > delete outside the operation’s checks. Writes of other keys are not affected, and neither
  > is a write through a *different leaf object* on the same container (a `partition()`
  > child, a second `CosmosItems`): the registry matches leaves by identity.
- A write performed *after* the operation has finished is indistinguishable from any other
  writer’s, so it cannot be refused: the operation raises [`RedirectError`](#cosmodol.routing.RedirectError) when the
  write it asked for never reached the leaf, which is the only signal there is.

```pycon
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
```

### Module Attributes

| [`WRITE_METHODS`](#cosmodol.routing.WRITE_METHODS)   | while one is pending on a leaf, any of them called with anything but the expected method and key is refused.   |
|------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|

### Functions

| [`claim`](#cosmodol.routing.claim)(leaf, method, key)             | The handler redirecting `leaf.<method>(key, ...)` right now, or `None`.   |
|---------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| [`redirect`](#cosmodol.routing.redirect)(leaf, method, key, handler) | Within the block, the call `leaf.<method>(key, ...)` goes to `handler`.   |

### Classes

| [`Redirect`](#cosmodol.routing.Redirect)(leaf, method, key, handler[, ...])   | One pending redirect of `leaf.<method>(key, ...)` to `handler`.   |
|------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|

### Exceptions

| [`ConcurrentWriteError`](#cosmodol.routing.ConcurrentWriteError)   | A write of a key arrived from another thread or task while an operation on it was pending.   |
|-------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| [`RedirectError`](#cosmodol.routing.RedirectError)          | A wrapping layer did not turn one mapping write into exactly one leaf write.                 |

### *exception* cosmodol.routing.ConcurrentWriteError

Bases: [`RedirectError`](#cosmodol.routing.RedirectError)

A write of a key arrived from another thread or task while an operation on it was pending.

### *class* cosmodol.routing.Redirect(leaf, method, key, handler, claimed=False, result=None, error=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One pending redirect of `leaf.<method>(key, ...)` to `handler`.

After the block, `claimed` says whether the write reached the leaf, `result` is
what the handler returned, and `error` what it raised (re-raised by the caller
even if a layer swallowed it).

### *exception* cosmodol.routing.RedirectError

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

A wrapping layer did not turn one mapping write into exactly one leaf write.

### cosmodol.routing.WRITE_METHODS *= ('_\_setitem_\_', '_\_delitem_\_')*

while one is pending on a leaf, any of them called
with anything but the expected method and key is refused.

* **Type:**
  The leaf methods a redirect guards

### cosmodol.routing.claim(leaf, method, key)

The handler redirecting `leaf.<method>(key, ...)` right now, or `None`.

Called by a leaf at the top of each of its `WRITE_METHODS`. The returned callable
runs the handler and records its result or error.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)]

### cosmodol.routing.redirect(leaf, method, key, handler)

Within the block, the call `leaf.<method>(key, ...)` goes to `handler`.

Any other write call on `leaf` within the block (another method, another key, or
the same call a second time) raises `RedirectError` before touching anything: one
mapping write was expected to reach the leaf once, as `key`.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Redirect`](#cosmodol.routing.Redirect)]
