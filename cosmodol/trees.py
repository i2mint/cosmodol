"""Tree-shaped stores: mapping-of-databases and mapping-of-containers.

- ``CosmosDatabase`` — keys are container names; values are ``ContainerProxy`` (or a
  store-wrapped form via ``store_factory``).
- ``CosmosAccount`` — keys are database names; values are ``CosmosDatabase`` instances.

Container/database creation is NOT via ``__setitem__`` — too parameter-rich; use
``add_container(...)`` / ``add_database(...)``. See
``misc/docs/design_decisions.md`` §7.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Optional, Union

from azure.cosmos import CosmosClient, DatabaseProxy, PartitionKey
from dol import KvReader

from cosmodol.connection import CosmosConnection
from cosmodol.errors import (
    ContainerNotEmptyError,
    ContainerNotFoundError,
    DatabaseNotEmptyError,
    DatabaseNotFoundError,
)


# ---------------------------------------------------------------------------
# CosmosDatabase — mapping of container name -> container (or wrapped store)
# ---------------------------------------------------------------------------


class CosmosDatabase(KvReader):
    """Mapping of container name → ``ContainerProxy`` (or store-wrapped form).

    ``__setitem__`` is disabled — use ``add_container(name, partition_key_path=..., ...)``.
    ``__delitem__`` refuses non-empty containers; use ``self.delete(name, force=True)``.

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
        raise TypeError(
            "Container creation does not go through __setitem__ (too parameter-rich). "
            "Use `database_store.add_container(name, partition_key_path=..., ...)` instead."
        )

    def __delitem__(self, k: str) -> None:
        try:
            c = self.database.get_container_client(k)
            c.read()
        except Exception as e:
            raise ContainerNotFoundError(k) from e
        # Refuse non-empty container; force-delete via .delete(k, force=True).
        try:
            next(iter(c.query_items(
                query="SELECT VALUE c.id FROM c OFFSET 0 LIMIT 1",
                enable_cross_partition_query=True,
            )))
            raise ContainerNotEmptyError(
                f"Container {k!r} is not empty. Call "
                f"`db_store.delete({k!r}, force=True)` to cascade-delete its items."
            )
        except StopIteration:
            pass
        self.database.delete_container(k)

    def __repr__(self) -> str:
        return f"CosmosDatabase({self.name!r})"

    # ---- explicit creators / destroyers ----

    def add_container(
        self,
        name: str,
        *,
        partition_key_path: str = "/id",
        throughput: Optional[int] = None,
        indexing_policy: Optional[dict] = None,
        default_ttl: Optional[int] = None,
        unique_key_policy: Optional[dict] = None,
        conflict_resolution_policy: Optional[dict] = None,
        **extra,
    ):
        """Create a container.

        Args:
            name: Container id.
            partition_key_path: Partition-key path (e.g. ``"/id"``, ``"/tenantId"``).
                Defaults to ``"/id"`` per ``misc/docs/design_decisions.md`` §3.
            throughput: Provisioned RU/s (e.g. 400). ``None`` uses the database's
                shared throughput if any, otherwise the account default.
            indexing_policy, default_ttl, unique_key_policy, conflict_resolution_policy:
                Standard Cosmos container kwargs.
            **extra: Forwarded to ``DatabaseProxy.create_container``.
        """
        return self.database.create_container(
            id=name,
            partition_key=PartitionKey(path=partition_key_path),
            offer_throughput=throughput,
            indexing_policy=indexing_policy,
            default_ttl=default_ttl,
            unique_key_policy=unique_key_policy,
            conflict_resolution_policy=conflict_resolution_policy,
            **extra,
        )

    def delete(self, k: str, *, force: bool = False) -> None:
        """Delete a container; if ``force=True``, cascade-delete its items first."""
        try:
            c = self.database.get_container_client(k)
            c.read()
        except Exception as e:
            raise ContainerNotFoundError(k) from e
        if force:
            for item in c.query_items(
                query="SELECT c.id, c._partitionKey FROM c",
                enable_cross_partition_query=True,
            ):
                c.delete_item(item=item["id"], partition_key=item.get("_partitionKey"))
        self.database.delete_container(k)


# ---------------------------------------------------------------------------
# CosmosAccount — mapping of database name -> CosmosDatabase
# ---------------------------------------------------------------------------


class CosmosAccount(KvReader):
    """Mapping of database name → ``CosmosDatabase``.

    ``__setitem__`` is disabled — use ``add_database(name, throughput=...)``.
    ``__delitem__`` refuses non-empty databases; use ``self.delete(name, force=True)``.
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
        raise TypeError(
            "Database creation does not go through __setitem__ (too parameter-rich). "
            "Use `account_store.add_database(name, throughput=...)` instead."
        )

    def __delitem__(self, k: str) -> None:
        try:
            db = self._client.get_database_client(k)
            db.read()
        except Exception as e:
            raise DatabaseNotFoundError(k) from e
        try:
            next(iter(db.list_containers()))
            raise DatabaseNotEmptyError(
                f"Database {k!r} is not empty. Call "
                f"`account_store.delete({k!r}, force=True)` to cascade-delete its containers."
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

    def add_database(
        self,
        name: str,
        *,
        throughput: Optional[int] = None,
        **extra,
    ) -> CosmosDatabase:
        db = self._client.create_database(id=name, offer_throughput=throughput, **extra)
        return self.database_factory(db)

    def delete(self, k: str, *, force: bool = False) -> None:
        """Delete a database; if ``force=True``, cascade-delete its containers first."""
        try:
            db = self._client.get_database_client(k)
            db.read()
        except Exception as e:
            raise DatabaseNotFoundError(k) from e
        if force:
            for c in db.list_containers():
                db.delete_container(c["id"])
        self._client.delete_database(k)
