"""Tree-shaped stores: mapping-of-databases and mapping-of-containers.

- ``CosmosDatabase`` — keys are container names; values are ``ContainerProxy`` (or a
  store-wrapped form via ``store_factory``).
- ``CosmosAccount`` — keys are database names; values are ``CosmosDatabase`` instances.

Container/database creation is NOT via ``__setitem__`` — too parameter-rich; use
``cosmodol.add_container(db_store, ...)`` / ``cosmodol.add_database(account_store, ...)``
(free functions, so a ``dol`` key codec around the store is honoured). See
``misc/docs/design_decisions.md`` §7.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Optional, Union

from azure.cosmos import CosmosClient, DatabaseProxy
from dol import KvReader

from cosmodol.connection import CosmosConnection
from cosmodol.routing import claim
from cosmodol.errors import (
    ContainerNotEmptyError,
    ContainerNotFoundError,
    DatabaseNotEmptyError,
    DatabaseNotFoundError,
    MovedToFunction,
)

_MOVED_REASON = (
    "As a method it ignored any dol key codec wrapped around the store and acted on the "
    "unmapped name (i2mint/cosmodol#1); the function maps it through every layer."
)


# ---------------------------------------------------------------------------
# CosmosDatabase — mapping of container name -> container (or wrapped store)
# ---------------------------------------------------------------------------


class CosmosDatabase(KvReader):
    """Mapping of container name → ``ContainerProxy`` (or store-wrapped form).

    ``__setitem__`` is disabled — use ``cosmodol.add_container(db_store, name, ...)``.
    ``__delitem__`` refuses non-empty containers; use
    ``cosmodol.delete_container(db_store, name, force=True)``.

    Args:
        database: Either an existing ``DatabaseProxy``, a database name string (resolved
            via ``connection``), or ``None`` (use ``connection.database_name``).
        connection: ``CosmosConnection`` (or anything ``from_anything`` accepts).
        store_factory: Optional callable ``(ContainerProxy) -> Mapping`` used to wrap
            the value returned by ``__getitem__``. Defaults to passing the
            ``ContainerProxy`` through.
    """

    def __init__(
        self,
        database: Union[DatabaseProxy, str],
        *,
        connection: Any = None,
        store_factory: Optional[Callable] = None,
    ):
        if isinstance(database, DatabaseProxy):
            self.database = database
            self._connection = None
        else:
            self._connection = CosmosConnection.from_anything(connection)
            self.database = self._connection.database(database)
        self.store_factory = store_factory

    @property
    def name(self) -> str:
        return self.database.id

    def __iter__(self) -> Iterator[str]:
        for c in self.database.list_containers():
            yield c["id"]

    def __contains__(self, k: str) -> bool:
        try:
            self.database.get_container_client(k).read()
            return True
        except Exception:
            return False

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __getitem__(self, k: str):
        c = self.database.get_container_client(k)
        # Cheap existence check via read().
        try:
            c.read()
        except Exception as e:
            raise ContainerNotFoundError(k) from e
        if self.store_factory is None:
            return c
        return self.store_factory(c)

    def __setitem__(self, k, v):
        if (handler := claim(self, "__setitem__", k)) is not None:
            return handler(k, v)  # cosmodol.add_container / add_database
        raise TypeError(
            "Container creation does not go through __setitem__ (too parameter-rich). "
            "Use `cosmodol.add_container(db_store, name, partition_key_path=..., ...)` instead."
        )

    def __delitem__(self, k: str) -> None:
        if (handler := claim(self, "__delitem__", k)) is not None:
            return handler(k)  # a forced cosmodol.delete_container / delete_database
        try:
            c = self.database.get_container_client(k)
            c.read()
        except Exception as e:
            raise ContainerNotFoundError(k) from e
        # Refuse non-empty container; force-delete via cosmodol.delete_container.
        try:
            next(
                iter(
                    c.query_items(
                        query="SELECT VALUE c.id FROM c OFFSET 0 LIMIT 1",
                        enable_cross_partition_query=True,
                    )
                )
            )
            raise ContainerNotEmptyError(
                f"Container {k!r} is not empty. Call "
                f"`cosmodol.delete_container(db_store, {k!r}, force=True)` to "
                "cascade-delete its items."
            )
        except StopIteration:
            pass
        self.database.delete_container(k)

    def __repr__(self) -> str:
        return f"CosmosDatabase({self.name!r})"

    # ---- creators / destroyers: free functions in cosmodol.ops (i2mint/cosmodol#1) ----

    add_container = MovedToFunction(
        "cosmodol.add_container(db_store, name, partition_key_path=..., ...)",
        reason=_MOVED_REASON,
    )
    delete = MovedToFunction(
        "cosmodol.delete_container(db_store, name, force=True)", reason=_MOVED_REASON
    )


# ---------------------------------------------------------------------------
# CosmosAccount — mapping of database name -> CosmosDatabase
# ---------------------------------------------------------------------------


class CosmosAccount(KvReader):
    """Mapping of database name → ``CosmosDatabase``.

    ``__setitem__`` is disabled — use ``cosmodol.add_database(account_store, name, ...)``.
    ``__delitem__`` refuses non-empty databases; use
    ``cosmodol.delete_database(account_store, name, force=True)``.
    """

    def __init__(
        self,
        connection: Any = None,
        *,
        database_factory: Optional[Callable] = None,
    ):
        self._connection = CosmosConnection.from_anything(connection)
        self._client = self._connection.client
        self.database_factory = database_factory or (
            lambda db_proxy: CosmosDatabase(db_proxy)
        )

    @property
    def client(self) -> CosmosClient:
        return self._client

    def __iter__(self) -> Iterator[str]:
        for db in self._client.list_databases():
            yield db["id"]

    def __contains__(self, k: str) -> bool:
        try:
            self._client.get_database_client(k).read()
            return True
        except Exception:
            return False

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __getitem__(self, k: str) -> CosmosDatabase:
        db = self._client.get_database_client(k)
        try:
            db.read()
        except Exception as e:
            raise DatabaseNotFoundError(k) from e
        return self.database_factory(db)

    def __setitem__(self, k, v):
        if (handler := claim(self, "__setitem__", k)) is not None:
            return handler(k, v)  # cosmodol.add_container / add_database
        raise TypeError(
            "Database creation does not go through __setitem__ (too parameter-rich). "
            "Use `cosmodol.add_database(account_store, name, throughput=...)` instead."
        )

    def __delitem__(self, k: str) -> None:
        if (handler := claim(self, "__delitem__", k)) is not None:
            return handler(k)  # a forced cosmodol.delete_container / delete_database
        try:
            db = self._client.get_database_client(k)
            db.read()
        except Exception as e:
            raise DatabaseNotFoundError(k) from e
        try:
            next(iter(db.list_containers()))
            raise DatabaseNotEmptyError(
                f"Database {k!r} is not empty. Call "
                f"`cosmodol.delete_database(account_store, {k!r}, force=True)` to "
                "cascade-delete its containers."
            )
        except StopIteration:
            pass
        self._client.delete_database(k)

    def __repr__(self) -> str:
        try:
            url = self._client.client_connection.url_connection
        except AttributeError:
            url = "<unknown>"
        return f"CosmosAccount(endpoint={url!r})"

    # ---- creators / destroyers: free functions in cosmodol.ops (i2mint/cosmodol#1) ----

    add_database = MovedToFunction(
        "cosmodol.add_database(account_store, name, throughput=...)",
        reason=_MOVED_REASON,
    )
    delete = MovedToFunction(
        "cosmodol.delete_database(account_store, name, force=True)",
        reason=_MOVED_REASON,
    )
