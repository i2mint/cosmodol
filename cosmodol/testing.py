"""Testing helpers for cosmodol — emulator fixture and a minimal in-memory fake.

The emulator-backed pieces require Docker. Unit tests can use ``FakeContainerProxy``
for fast in-memory testing of the metal layer without any SDK / network.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from cosmodol.connection import CosmosConnection


# Well-known emulator credentials. Same on every Cosmos emulator instance.
EMULATOR_ENDPOINT = "https://localhost:8081"
EMULATOR_KEY = (
    "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMb"
    "IZnqyMsEcaGQy67XIw/Jw=="
)
EMULATOR_CONNECTION_STRING = (
    f"AccountEndpoint={EMULATOR_ENDPOINT}/;AccountKey={EMULATOR_KEY};"
)


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def emulator_is_running() -> bool:
    """Return True if the Cosmos emulator's HTTPS endpoint (port 8081) is reachable."""
    return _port_open("127.0.0.1", 8081)


def docker_available() -> bool:
    return shutil.which("docker") is not None


@contextmanager
def cosmos_emulator(container_name: Optional[str] = None, *, wait: float = 60.0):
    """Context manager that ensures the Linux vNext emulator is running.

    If the emulator is already up (port 8081 reachable), do nothing on enter/exit.
    Otherwise start it via ``docker run`` and stop it on exit. Note the emulator takes
    ~30-60 seconds to become ready.

    Args:
        container_name: Docker container name. Random by default.
        wait: Max seconds to wait for the endpoint to come up.

    Yields:
        The emulator connection string.

    Raises:
        RuntimeError: if Docker is unavailable and the emulator is not already running.
    """
    if emulator_is_running():
        yield EMULATOR_CONNECTION_STRING
        return

    if not docker_available():
        raise RuntimeError(
            "Cosmos emulator is not running and Docker is not available. Start manually:\n"
            "  docker run --detach --rm \\\n"
            "    --publish 8081:8081 --publish 8080:8080 --publish 1234:1234 \\\n"
            "    mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview"
        )

    container_name = container_name or f"cosmos-emu-{uuid.uuid4().hex[:8]}"
    cmd = [
        "docker", "run", "-d", "--rm", "--name", container_name,
        "-p", "8081:8081", "-p", "8080:8080", "-p", "1234:1234",
        "mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview",
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + wait
        while not emulator_is_running():
            if time.time() > deadline:
                raise RuntimeError(
                    f"Cosmos emulator did not become ready within {wait}s."
                )
            time.sleep(1.0)
        yield EMULATOR_CONNECTION_STRING
    finally:
        subprocess.call(
            ["docker", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def mk_emulator_connection() -> CosmosConnection:
    """Build a ``CosmosConnection`` pointing at the running emulator. Disables SSL
    verification (the emulator uses a self-signed cert)."""
    # The emulator presents a self-signed cert; pass connection_verify=False via
    # client_kwargs so requests through azure-core skip verification.
    return CosmosConnection(
        connection_string=EMULATOR_CONNECTION_STRING,
        client_kwargs={"connection_verify": False},
    )


# ---------------------------------------------------------------------------
# In-memory fake — for unit tests that don't want the emulator overhead.
# Implements the subset of ContainerProxy used by cosmodol.base + .stores.
# ---------------------------------------------------------------------------


class _FakeLastHeaders(dict):
    pass


class _FakeClientConnection:
    def __init__(self):
        self.last_response_headers = _FakeLastHeaders()


class FakeContainerProxy:
    """A dict-of-dicts-backed stand-in for ``azure.cosmos.ContainerProxy``.

    Supports the subset of methods that cosmodol.base / .stores call:
    ``read_item``, ``upsert_item``, ``replace_item``, ``delete_item``, ``query_items``,
    ``read`` (container metadata). Storage is ``{(pk_value, id): item_dict}``.
    """

    def __init__(
        self,
        *,
        container_id: str = "fake-container",
        partition_key_path: str = "/id",
    ):
        self.container_link = f"dbs/fake-db/colls/{container_id}"
        self.id = container_id
        self.partition_key_path = partition_key_path
        self._pk_prop = partition_key_path.lstrip("/")
        self._data: dict[tuple, dict] = {}
        self._etag_counter = 0
        self.client_connection = _FakeClientConnection()

    # ---- helpers ----

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def _set_hdrs(self, *, charge: Optional[float] = 1.0, etag: Optional[str] = None):
        self.client_connection.last_response_headers = _FakeLastHeaders(
            {"x-ms-request-charge": str(charge) if charge is not None else None,
             "etag": etag} if etag else
            {"x-ms-request-charge": str(charge) if charge is not None else None}
        )

    # ---- API surface ----

    def read(self) -> dict:
        self._set_hdrs(charge=1.0)
        return {
            "id": self.id,
            "partitionKey": {"paths": [self.partition_key_path], "kind": "Hash"},
        }

    def read_item(self, item: str, partition_key: Any) -> dict:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        key = (partition_key, item)
        if key not in self._data:
            self._set_hdrs(charge=1.0)
            raise CosmosResourceNotFoundError(
                message=f"Resource Not Found: {key}",
                response=None,
            )
        self._set_hdrs(charge=1.0, etag=self._data[key].get("_etag"))
        return dict(self._data[key])

    def upsert_item(self, body: dict) -> dict:
        id_ = body["id"]
        pk = body[self._pk_prop]
        etag = self._next_etag()
        stored = dict(body)
        stored["_etag"] = etag
        stored["_ts"] = int(time.time())
        self._data[(pk, id_)] = stored
        self._set_hdrs(charge=10.0, etag=etag)
        return dict(stored)

    def replace_item(
        self,
        item: str,
        body: dict,
        *,
        if_match_etag: Optional[str] = None,
    ) -> dict:
        from azure.cosmos.exceptions import (
            CosmosAccessConditionFailedError,
            CosmosResourceNotFoundError,
        )

        pk = body[self._pk_prop]
        key = (pk, item)
        if key not in self._data:
            raise CosmosResourceNotFoundError(message=f"Not found: {key}", response=None)
        if if_match_etag is not None and self._data[key].get("_etag") != if_match_etag:
            raise CosmosAccessConditionFailedError(
                message="ETag mismatch", response=None
            )
        etag = self._next_etag()
        stored = dict(body)
        stored["_etag"] = etag
        stored["_ts"] = int(time.time())
        stored["id"] = item
        self._data[key] = stored
        self._set_hdrs(charge=10.0, etag=etag)
        return dict(stored)

    def delete_item(
        self,
        item: str,
        partition_key: Any,
        *,
        if_match_etag: Optional[str] = None,
    ) -> None:
        from azure.cosmos.exceptions import (
            CosmosAccessConditionFailedError,
            CosmosResourceNotFoundError,
        )

        key = (partition_key, item)
        if key not in self._data:
            raise CosmosResourceNotFoundError(message=f"Not found: {key}", response=None)
        if if_match_etag is not None and self._data[key].get("_etag") != if_match_etag:
            raise CosmosAccessConditionFailedError(
                message="ETag mismatch", response=None
            )
        del self._data[key]
        self._set_hdrs(charge=5.0)

    def query_items(
        self,
        query: str,
        *,
        parameters: Optional[list[dict]] = None,
        partition_key: Any = None,
        enable_cross_partition_query: bool = False,
        max_item_count: Optional[int] = None,
        **_kw,
    ) -> Iterator[dict]:
        """Very small SQL emulator: supports COUNT(1) and projections of c.id / c.<pk>.

        Sufficient for the queries cosmodol issues internally. Real SQL is out of scope.
        """
        if partition_key is not None:
            items = [v for (pk, _), v in self._data.items() if pk == partition_key]
        else:
            items = list(self._data.values())

        # parameter substitution (simple): @name -> value
        params = {p["name"]: p["value"] for p in (parameters or [])}
        if params:
            for name, val in params.items():
                if isinstance(val, str):
                    query = query.replace(name, f"'{val}'")
                else:
                    query = query.replace(name, str(val))

        q_upper = query.upper().strip()
        self._set_hdrs(charge=2.5)
        if "COUNT(1)" in q_upper:
            yield len(items)
            return
        if "SELECT VALUE C.ID" in q_upper:
            for it in items:
                yield it["id"]
            return
        # Projection select of c.id, c.<pk>
        if "C.ID" in q_upper and f"C.{self._pk_prop.upper()}" in q_upper:
            for it in items:
                yield {"id": it["id"], self._pk_prop: it[self._pk_prop]}
            return
        # Fallback: SELECT *
        for it in items:
            yield dict(it)
