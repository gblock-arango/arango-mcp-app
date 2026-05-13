"""
Logical database handle for gateway mode: mirrors the *shape* of ``python-arango``
``StandardDatabase`` / ``StandardCollection`` by translating calls to Arango REST
through :class:`gateway_arango_client.GatewayArangoClient` (``POST …/api/arango/http``).

``GatewayArangoClient`` is only the transport (request/response). This module is the
compatibility layer so ``arango_connector.get_db()`` keeps working in gateway mode.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional, Sequence
from urllib.parse import quote

from arango_mcp.gateway_arango_client import GatewayArangoClient

logger = logging.getLogger(__name__)


class GatewayAPIError(Exception):
    """Arango returned ``error: true`` (or transport failure) through the gateway."""

    def __init__(self, message: str, *, error_code: Any = None) -> None:
        super().__init__(message)
        self.error_message = message
        self.error_code = error_code


def _unwrap_arango_result(result: dict[str, Any], *, op: str) -> Any:
    if result.get("ok"):
        body = result.get("body")
        if isinstance(body, dict) and body.get("error") is True:
            raise GatewayAPIError(
                str(body.get("errorMessage", "Arango error")),
                error_code=body.get("errorNum"),
            )
        return body
    err = result.get("error") or f"HTTP {result.get('status_code')}"
    body = result.get("body")
    code = None
    if isinstance(body, dict):
        code = body.get("errorNum")
        err = str(body.get("errorMessage", err))
    raise GatewayAPIError(err, error_code=code)


def _q(seg: str) -> str:
    return quote(seg, safe="")


class GatewayCursor:
    """Minimal cursor over ``result`` batch (extend later for ``hasMore`` / ``id``)."""

    def __init__(self, batch: list[Any], *, count: Optional[int] = None) -> None:
        self._batch = batch
        self._count = count

    @property
    def count(self) -> Optional[int]:
        return self._count

    def __iter__(self) -> Iterator[Any]:
        return iter(self._batch)


class GatewayAQL:
    def __init__(self, db: GatewayDatabase) -> None:
        self._db = db

    def execute(
        self,
        query: str,
        bind_vars: Optional[Dict[str, Any]] = None,
        count: bool = False,
        **_: Any,
    ) -> GatewayCursor:
        body: Dict[str, Any] = {"query": query, "bindVars": bind_vars or {}}
        if count:
            body["count"] = True
        res = self._db._request(
            "POST", f"/_db/{_q(self._db.name)}/_api/cursor", json_body=body
        )
        data = _unwrap_arango_result(res, op="cursor")
        batch = data.get("result") or []
        c = data.get("count") if count else None
        return GatewayCursor(batch if isinstance(batch, list) else list(batch), count=c)

    def explain(self, query: str, **kwargs: Any) -> Any:
        body: Dict[str, Any] = {"query": query, **kwargs}
        res = self._db._request(
            "POST", f"/_db/{_q(self._db.name)}/_api/explain", json_body=body
        )
        return _unwrap_arango_result(res, op="explain")

    def validate(self, query: str) -> Any:
        body = {"query": query}
        res = self._db._request(
            "POST", f"/_db/{_q(self._db.name)}/_api/query", json_body=body
        )
        return _unwrap_arango_result(res, op="validate")


class GatewayCluster:
    """Maps to ``/_admin/cluster/*`` and related cluster HTTP (see ``python-arango``)."""

    def __init__(self, client: GatewayArangoClient) -> None:
        self._c = client

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        p = path
        if params:
            p += "?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return _unwrap_arango_result(self._c.request("GET", p), op=path)

    def _put_json(self, path: str, data: Any) -> Any:
        return _unwrap_arango_result(self._c.request("PUT", path, json_body=data), op=path)

    def health(self) -> Any:
        return self._get("/_admin/cluster/health")

    def server_role(self) -> str:
        return str(self._get("/_admin/server/role")["role"])

    def server_count(self) -> int:
        res = self._c.request("GET", "/_admin/cluster/numberOfServers")
        body = _unwrap_arango_result(res, op="numberOfServers")
        if isinstance(body, int):
            return int(body)
        if isinstance(body, dict) and "result" in body:
            return int(body["result"])
        return int(body or 0)

    def endpoints(self) -> List[str]:
        data = _unwrap_arango_result(self._c.request("GET", "/_api/cluster/endpoints"), op="endpoints")
        return [item["endpoint"] for item in data.get("endpoints", [])]

    def server_id(self) -> str:
        return str(self._get("/_admin/server/id")["id"])

    def server_statistics(self, server_id: str) -> Any:
        return self._get("/_admin/cluster/nodeStatistics", params={"ServerID": server_id})

    def server_engine(self, server_id: str) -> Any:
        return self._get("/_admin/cluster/nodeEngine", params={"ServerID": server_id})

    def calculate_imbalance(self) -> Any:
        data = _unwrap_arango_result(self._c.request("GET", "/_admin/cluster/rebalance"), op="imbalance")
        return data.get("result", data)

    def rebalance(self, **kwargs: Any) -> Any:
        body: Dict[str, Any] = {"version": kwargs.get("version", 1)}
        if kwargs.get("max_moves") is not None:
            body["maximumNumberOfMoves"] = kwargs["max_moves"]
        if kwargs.get("leader_changes") is not None:
            body["leaderChanges"] = kwargs["leader_changes"]
        if kwargs.get("move_leaders") is not None:
            body["moveLeaders"] = kwargs["move_leaders"]
        if kwargs.get("move_followers") is not None:
            body["moveFollowers"] = kwargs["move_followers"]
        if kwargs.get("pi_factor") is not None:
            body["piFactor"] = kwargs["pi_factor"]
        if kwargs.get("exclude_system_collections") is not None:
            body["excludeSystemCollections"] = kwargs["exclude_system_collections"]
        data = _unwrap_arango_result(
            self._c.request("PUT", "/_admin/cluster/rebalance", json_body=body),
            op="rebalance",
        )
        return data.get("result", data)

    def toggle_maintenance_mode(self, mode: str) -> Any:
        return self._put_json("/_admin/cluster/maintenance", mode)


class GatewayBackup:
    def __init__(self, client: GatewayArangoClient) -> None:
        self._c = client

    def create(self, **kwargs: Any) -> Any:
        body: Dict[str, Any] = {}
        if kwargs.get("label") is not None:
            body["label"] = kwargs["label"]
        if kwargs.get("allow_inconsistent") is not None:
            body["allowInconsistent"] = kwargs["allow_inconsistent"]
        if kwargs.get("force") is not None:
            body["force"] = kwargs["force"]
        if kwargs.get("timeout") is not None:
            body["timeout"] = kwargs["timeout"]
        data = _unwrap_arango_result(
            self._c.request("POST", "/_admin/backup/create", json_body=body),
            op="backup.create",
        )
        return data.get("result", data)

    def get(self, backup_id: Optional[str] = None) -> Any:
        body = None if backup_id is None else {"id": backup_id}
        data = _unwrap_arango_result(
            self._c.request("POST", "/_admin/backup/list", json_body=body),
            op="backup.list",
        )
        return data.get("result", data)

    def restore(self, backup_id: str) -> Any:
        data = _unwrap_arango_result(
            self._c.request("POST", "/_admin/backup/restore", json_body={"id": backup_id}),
            op="backup.restore",
        )
        return data.get("result", data)

    def delete(self, backup_id: str) -> bool:
        _unwrap_arango_result(
            self._c.request("POST", "/_admin/backup/delete", json_body={"id": backup_id}),
            op="backup.delete",
        )
        return True


class GatewayTransactionHandle:
    def __init__(self, db: GatewayDatabase, txn_id: str) -> None:
        self._db = db
        self._id = txn_id

    @property
    def transaction_id(self) -> str:
        return self._id

    def transaction_status(self) -> str:
        res = self._db._request(
            "GET", f"/_db/{_q(self._db.name)}/_api/transaction/{_q(self._id)}"
        )
        data = _unwrap_arango_result(res, op="transaction_status")
        return str(data["result"]["status"])

    def commit_transaction(self) -> bool:
        _unwrap_arango_result(
            self._db._request(
                "PUT", f"/_db/{_q(self._db.name)}/_api/transaction/{_q(self._id)}"
            ),
            op="commit",
        )
        return True

    def abort_transaction(self) -> bool:
        _unwrap_arango_result(
            self._db._request(
                "DELETE", f"/_db/{_q(self._db.name)}/_api/transaction/{_q(self._id)}"
            ),
            op="abort",
        )
        return True


class GatewayGraph:
    def __init__(self, db: GatewayDatabase, graph_name: str) -> None:
        self._db = db
        self._name = graph_name

    @property
    def name(self) -> str:
        return self._name

    def properties(self) -> Any:
        res = self._db._request(
            "GET", f"/_db/{_q(self._db.name)}/_api/gharial/{_q(self._name)}"
        )
        data = _unwrap_arango_result(res, op="graph")
        return self._db._format_graph_props(data.get("graph", data))

    def has_edge_definition(self, name: str) -> bool:
        body = self.properties()
        edges = body.get("edge_definitions") or body.get("edgeDefinitions") or []
        for ed in edges:
            coll = ed.get("edge_collection") or ed.get("collection")
            if coll == name:
                return True
        return False

    def edge_collection(self, name: str) -> GatewayEdgeCollection:
        return GatewayEdgeCollection(self._db, self._name, name)


class GatewayEdgeCollection:
    def __init__(self, db: GatewayDatabase, graph_name: str, edge_coll: str) -> None:
        self._db = db
        self._graph = graph_name
        self._coll = edge_coll

    def insert(self, document: Dict[str, Any], **_: Any) -> Any:
        res = self._db._request(
            "POST",
            f"/_db/{_q(self._db.name)}/_api/gharial/{_q(self._graph)}/edge/{_q(self._coll)}",
            json_body=document,
        )
        return _unwrap_arango_result(res, op="edge_insert")


class GatewayCollection:
    def __init__(self, db: GatewayDatabase, name: str) -> None:
        self._db = db
        self.name = name

    def properties(self) -> Any:
        res = self._db._request(
            "GET", f"/_db/{_q(self._db.name)}/_api/collection/{_q(self.name)}"
        )
        return _unwrap_arango_result(res, op="collection")

    def count(self) -> int:
        res = self._db._request(
            "GET", f"/_db/{_q(self._db.name)}/_api/collection/{_q(self.name)}/count"
        )
        data = _unwrap_arango_result(res, op="count")
        return int(data.get("count", 0))

    def statistics(self) -> Any:
        res = self._db._request(
            "GET", f"/_db/{_q(self._db.name)}/_api/collection/{_q(self.name)}/figures"
        )
        return _unwrap_arango_result(res, op="figures")

    def revision(self) -> Any:
        return self.properties().get("revision") or self.properties().get("_rev")

    def indexes(self) -> Any:
        res = self._db._request(
            "GET",
            f"/_db/{_q(self._db.name)}/_api/index?collection={_q(self.name)}",
        )
        data = _unwrap_arango_result(res, op="indexes")
        return data.get("indexes", [])

    def add_index(self, data: Dict[str, Any]) -> Any:
        res = self._db._request(
            "POST",
            f"/_db/{_q(self._db.name)}/_api/index?collection={_q(self.name)}",
            json_body=data,
        )
        return _unwrap_arango_result(res, op="add_index")

    def delete_index(self, index_id: str, ignore_missing: bool = False) -> Any:
        safe = quote(index_id, safe="")
        res = self._db._request(
            "DELETE", f"/_db/{_q(self._db.name)}/_api/index/{safe}"
        )
        try:
            return _unwrap_arango_result(res, op="delete_index")
        except GatewayAPIError:
            if ignore_missing:
                return False
            raise

    def insert(self, document: Dict[str, Any], **kwargs: Any) -> Any:
        qs = []
        if kwargs.get("return_new"):
            qs.append("returnNew=true")
        if kwargs.get("sync") is not None:
            qs.append(f"waitForSync={'true' if kwargs['sync'] else 'false'}")
        path = f"/_db/{_q(self._db.name)}/_api/document/{_q(self.name)}"
        if qs:
            path += "?" + "&".join(qs)
        res = self._db._request("POST", path, json_body=document)
        return _unwrap_arango_result(res, op="insert")

    def insert_many(self, documents: Sequence[Dict[str, Any]], **_: Any) -> Any:
        path = f"/_db/{_q(self._db.name)}/_api/document/{_q(self.name)}"
        res = self._db._request("POST", path, json_body=list(documents))
        body = _unwrap_arango_result(res, op="insert_many")
        return body if isinstance(body, list) else body.get("result", body)

    def get(self, key: str) -> Any:
        res = self._db._request(
            "GET", f"/_db/{_q(self._db.name)}/_api/document/{_q(self.name)}/{_q(key)}"
        )
        if res.get("ok"):
            body = res.get("body")
            if isinstance(body, dict) and body.get("error") is True:
                if body.get("errorNum") == 1202:
                    return None
            return body
        if res.get("status_code") == 404:
            return None
        return _unwrap_arango_result(res, op="get")

    def find(self, filters: Dict[str, Any], *, skip: int = 0, limit: int = 100) -> GatewayCursor:
        parts = []
        bind: Dict[str, Any] = {"@coll": self.name, "s": skip, "l": limit}
        i = 0
        for k, v in filters.items():
            vn = f"v{i}"
            bind[vn] = v
            parts.append(f"doc.{k} == @{vn}")
            i += 1
        filt = " FILTER " + " AND ".join(parts) if parts else ""
        aql = f"FOR doc IN @@coll{filt} LIMIT @s, @l RETURN doc"
        return self._db.aql.execute(aql, bind_vars=bind)

    def update(self, document: Dict[str, Any], **kwargs: Any) -> Any:
        merge = kwargs.get("merge", True)
        key = document.get("_key") or document.get("_id", "").split("/")[-1]
        path = f"/_db/{_q(self._db.name)}/_api/document/{_q(self.name)}/{_q(str(key))}"
        if not merge:
            res = self._db._request("PUT", path, json_body=document)
        else:
            res = self._db._request("PATCH", path, json_body=document)
        return _unwrap_arango_result(res, op="update")

    def replace(self, document: Dict[str, Any]) -> Any:
        key = document.get("_key") or document.get("_id", "").split("/")[-1]
        path = f"/_db/{_q(self._db.name)}/_api/document/{_q(self.name)}/{_q(str(key))}"
        res = self._db._request("PUT", path, json_body=document)
        return _unwrap_arango_result(res, op="replace")

    def delete(self, key: str, **kwargs: Any) -> Any:
        path = f"/_db/{_q(self._db.name)}/_api/document/{_q(self.name)}/{_q(key)}"
        res = self._db._request("DELETE", path, json_body=None)
        try:
            return _unwrap_arango_result(res, op="delete")
        except GatewayAPIError:
            if kwargs.get("ignore_missing"):
                return False
            raise

    def update_many(self, documents: List[Dict[str, Any]], **kwargs: Any) -> Any:
        out = []
        for d in documents:
            out.append(self.update(d, merge=kwargs.get("merge", True)))
        return out

    def delete_many(self, documents: List[Dict[str, Any]]) -> Any:
        out = []
        for d in documents:
            k = d.get("_key") or str(d.get("_id", "")).split("/")[-1]
            out.append(self.delete(k))
        return out


class GatewayDatabase:
    """Two-way logical proxy: same call patterns as ``StandardDatabase``, gateway HTTP underneath."""

    def __init__(self, client: GatewayArangoClient, name: str) -> None:
        self._client = client
        self.name = name
        self.aql = GatewayAQL(self)
        self.cluster = GatewayCluster(client)
        self.backup = GatewayBackup(client)

    def _request(self, method: str, path: str, *, json_body: Any = None) -> dict[str, Any]:
        return self._client.request(method, path, json_body=json_body)

    @staticmethod
    def _format_graph_props(graph: dict[str, Any]) -> dict[str, Any]:
        """Shape similar to ``python-arango`` ``format_graph_properties``."""
        if not graph:
            return {}
        eds = graph.get("edgeDefinitions") or []
        return {
            "id": graph.get("_id"),
            "name": graph.get("_key"),
            "revision": graph.get("_rev"),
            "orphan_collections": graph.get("orphanCollections", []),
            "edge_definitions": [
                {
                    "edge_collection": d.get("collection"),
                    "from_vertex_collections": d.get("from", []),
                    "to_vertex_collections": d.get("to", []),
                }
                for d in eds
            ],
            "type": graph.get("type"),
            "number_of_shards": graph.get("numberOfShards"),
            "replication_factor": graph.get("replicationFactor"),
        }

    def properties(self) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/database/current")
        return _unwrap_arango_result(res, op="database")

    def collections(self) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/collection")
        data = _unwrap_arango_result(res, op="collections")
        return data.get("result", data)

    def has_collection(self, name: str) -> bool:
        res = self._request(
            "GET", f"/_db/{_q(self.name)}/_api/collection/{_q(name)}"
        )
        if res.get("ok"):
            return True
        if res.get("status_code") == 404:
            return False
        _unwrap_arango_result(res, op="has_collection")
        return False

    def collection(self, name: str) -> GatewayCollection:
        return GatewayCollection(self, name)

    def create_collection(self, name: str, **kwargs: Any) -> Any:
        body: Dict[str, Any] = {"name": name, **kwargs}
        res = self._request(
            "POST", f"/_db/{_q(self.name)}/_api/collection", json_body=body
        )
        return _unwrap_arango_result(res, op="create_collection")

    def delete_collection(self, name: str, **kwargs: Any) -> Any:
        path = f"/_db/{_q(self.name)}/_api/collection/{_q(name)}"
        res = self._request("DELETE", path, json_body=None)
        try:
            _unwrap_arango_result(res, op="delete_collection")
            return True
        except GatewayAPIError as e:
            if kwargs.get("ignore_missing") and e.error_code in (1203, 1924):
                return False
            raise

    def graphs(self) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/gharial")
        data = _unwrap_arango_result(res, op="graphs")
        graphs = data.get("graphs", [])
        return [self._format_graph_props(g) for g in graphs]

    def has_graph(self, name: str) -> bool:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/gharial/{_q(name)}")
        if res.get("ok"):
            return True
        if res.get("status_code") == 404:
            return False
        _unwrap_arango_result(res, op="has_graph")
        return False

    def create_graph(self, name: str, **kwargs: Any) -> GatewayGraph:
        edge_definitions = kwargs.get("edge_definitions") or []
        ed_payload = [
            {
                "collection": d["edge_collection"],
                "from": d["from_vertex_collections"],
                "to": d["to_vertex_collections"],
            }
            for d in edge_definitions
        ]
        data: Dict[str, Any] = {
            "name": name,
            "edgeDefinitions": ed_payload,
            "orphanCollections": kwargs.get("orphan_collections") or [],
        }
        if kwargs.get("smart") is not None:
            data["isSmart"] = kwargs["smart"]
        if kwargs.get("disjoint") is not None:
            data["isDisjoint"] = kwargs["disjoint"]
        opts: Dict[str, Any] = {}
        if kwargs.get("smart_field"):
            opts["smartGraphAttribute"] = kwargs["smart_field"]
        if kwargs.get("shard_count") is not None:
            opts["numberOfShards"] = kwargs["shard_count"]
        if kwargs.get("replication_factor") is not None:
            opts["replicationFactor"] = kwargs["replication_factor"]
        if kwargs.get("write_concern") is not None:
            opts["writeConcern"] = kwargs["write_concern"]
        if kwargs.get("satellite_collections") is not None:
            opts["satellites"] = kwargs["satellite_collections"]
        if opts:
            data["options"] = opts
        if kwargs.get("is_satellite"):
            data.setdefault("options", {})["replicationFactor"] = "satellite"
        self._request("POST", f"/_db/{_q(self.name)}/_api/gharial", json_body=data)
        return GatewayGraph(self, name)

    def graph(self, name: str) -> GatewayGraph:
        return GatewayGraph(self, name)

    def delete_graph(self, name: str, **kwargs: Any) -> bool:
        drop = kwargs.get("drop_collections")
        path = f"/_db/{_q(self.name)}/_api/gharial/{_q(name)}"
        if drop is not None:
            path += f"?dropCollections={'true' if drop else 'false'}"
        res = self._request("DELETE", path, json_body=None)
        try:
            _unwrap_arango_result(res, op="delete_graph")
            return True
        except GatewayAPIError as e:
            if kwargs.get("ignore_missing") and e.error_code == 1924:
                return False
            raise

    def begin_transaction(self, **kwargs: Any) -> GatewayTransactionHandle:
        cols: Dict[str, Any] = {}
        if kwargs.get("read") is not None:
            cols["read"] = kwargs["read"]
        if kwargs.get("write") is not None:
            cols["write"] = kwargs["write"]
        if kwargs.get("exclusive") is not None:
            cols["exclusive"] = kwargs["exclusive"]
        data: Dict[str, Any] = {"collections": cols}
        if kwargs.get("sync") is not None:
            data["waitForSync"] = kwargs["sync"]
        if kwargs.get("allow_implicit") is not None:
            data["allowImplicit"] = kwargs["allow_implicit"]
        if kwargs.get("lock_timeout") is not None:
            data["lockTimeout"] = kwargs["lock_timeout"]
        if kwargs.get("max_size") is not None:
            data["maxTransactionSize"] = kwargs["max_size"]
        if kwargs.get("skip_fast_lock_round") is not None:
            data["skipFastLockRound"] = kwargs["skip_fast_lock_round"]
        res = self._request(
            "POST", f"/_db/{_q(self.name)}/_api/transaction/begin", json_body=data
        )
        out = _unwrap_arango_result(res, op="begin_tx")
        tid = str(out["result"]["id"])
        return GatewayTransactionHandle(self, tid)

    def fetch_transaction(self, txn_id: str) -> GatewayTransactionHandle:
        h = GatewayTransactionHandle(self, txn_id)
        h.transaction_status()
        return h

    def list_transactions(self) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/transaction")
        data = _unwrap_arango_result(res, op="list_tx")
        if isinstance(data, list):
            return data
        return data.get("transactions", data.get("result", []))

    def execute_transaction(self, command: str, **kwargs: Any) -> Any:
        cols: Dict[str, Any] = {"allowImplicit": kwargs.get("allow_implicit")}
        if kwargs.get("read") is not None:
            cols["read"] = kwargs["read"]
        if kwargs.get("write") is not None:
            cols["write"] = kwargs["write"]
        data: Dict[str, Any] = {"action": command, "collections": cols}
        if kwargs.get("params") is not None:
            data["params"] = kwargs["params"]
        if kwargs.get("timeout") is not None:
            data["lockTimeout"] = kwargs["timeout"]
        if kwargs.get("sync") is not None:
            data["waitForSync"] = kwargs["sync"]
        if kwargs.get("max_size") is not None:
            data["maxTransactionSize"] = kwargs["max_size"]
        res = self._request(
            "POST", f"/_db/{_q(self.name)}/_api/transaction", json_body=data
        )
        out = _unwrap_arango_result(res, op="js_tx")
        return out.get("result")

    def databases(self) -> Any:
        res = self._request("GET", "/_api/database")
        data = _unwrap_arango_result(res, op="databases")
        return data.get("result", [])

    def has_database(self, name: str) -> bool:
        return name in self.databases()

    def create_database(self, name: str, **_: Any) -> Any:
        res = self._request("POST", "/_api/database", json_body={"name": name})
        return _unwrap_arango_result(res, op="create_db")

    def delete_database(self, name: str, **kwargs: Any) -> Any:
        res = self._request("DELETE", f"/_api/database/{_q(name)}", json_body=None)
        try:
            return _unwrap_arango_result(res, op="delete_db")
        except GatewayAPIError:
            if kwargs.get("ignore_missing"):
                return False
            raise

    def users(self) -> Any:
        res = self._request("GET", "/_api/user")
        data = _unwrap_arango_result(res, op="users")
        return [
            {"username": r["user"], "active": r["active"], "extra": r.get("extra")}
            for r in data.get("result", [])
        ]

    def user(self, username: str) -> Any:
        res = self._request("GET", f"/_api/user/{_q(username)}")
        data = _unwrap_arango_result(res, op="user")
        return {
            "username": data["user"],
            "active": data["active"],
            "extra": data.get("extra"),
        }

    def create_user(self, **kwargs: Any) -> Any:
        body: Dict[str, Any] = {"user": kwargs["username"], "passwd": kwargs.get("password")}
        if kwargs.get("active") is not None:
            body["active"] = kwargs["active"]
        if kwargs.get("extra") is not None:
            body["extra"] = kwargs["extra"]
        res = self._request("POST", "/_api/user", json_body=body)
        data = _unwrap_arango_result(res, op="create_user")
        return {
            "username": data["user"],
            "active": data["active"],
            "extra": data.get("extra"),
        }

    def update_user(self, **kwargs: Any) -> Any:
        username = kwargs["username"]
        body: Dict[str, Any] = {}
        if kwargs.get("password") is not None:
            body["passwd"] = kwargs["password"]
        if kwargs.get("active") is not None:
            body["active"] = kwargs["active"]
        if kwargs.get("extra") is not None:
            body["extra"] = kwargs["extra"]
        res = self._request("PATCH", f"/_api/user/{_q(username)}", json_body=body)
        data = _unwrap_arango_result(res, op="update_user")
        return {
            "username": data["user"],
            "active": data["active"],
            "extra": data.get("extra"),
        }

    def delete_user(self, username: str, **kwargs: Any) -> Any:
        res = self._request("DELETE", f"/_api/user/{_q(username)}", json_body=None)
        try:
            _unwrap_arango_result(res, op="delete_user")
            return True
        except GatewayAPIError as e:
            if kwargs.get("ignore_missing") and e.error_code == 404:
                return False
            raise

    def permissions(self, username: str) -> Any:
        res = self._request(
            "GET", f"/_api/user/{_q(username)}/database?full=true", json_body=None
        )
        data = _unwrap_arango_result(res, op="permissions")
        return data.get("result", data)

    def permission(self, username: str, database: str, collection: Optional[str] = None) -> str:
        ep = f"/_api/user/{_q(username)}/database/{_q(database)}"
        if collection:
            ep += f"/{_q(collection)}"
        res = self._request("GET", ep, json_body=None)
        data = _unwrap_arango_result(res, op="permission")
        return str(data.get("result", ""))

    def update_permission(
        self, username: str, permission: str, database: str, collection: Optional[str] = None
    ) -> bool:
        ep = f"/_api/user/{_q(username)}/database/{_q(database)}"
        if collection:
            ep += f"/{_q(collection)}"
        res = self._request("PUT", ep, json_body={"grant": permission})
        _unwrap_arango_result(res, op="update_perm")
        return True

    def reset_permission(
        self, username: str, database: str, collection: Optional[str] = None
    ) -> bool:
        ep = f"/_api/user/{_q(username)}/database/{_q(database)}"
        if collection:
            ep += f"/{_q(collection)}"
        res = self._request("DELETE", ep, json_body=None)
        _unwrap_arango_result(res, op="reset_perm")
        return True

    def analyzers(self) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/analyzer")
        data = _unwrap_arango_result(res, op="analyzers")
        return data.get("result", [])

    def analyzer(self, name: str) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/analyzer/{_q(name)}")
        return _unwrap_arango_result(res, op="analyzer")

    def create_analyzer(
        self, name: str, analyzer_type: str, properties: Any = None, features: Any = None
    ) -> Any:
        body: Dict[str, Any] = {"name": name, "type": analyzer_type}
        if properties is not None:
            body["properties"] = properties
        if features is not None:
            body["features"] = features
        res = self._request("POST", f"/_db/{_q(self.name)}/_api/analyzer", json_body=body)
        return _unwrap_arango_result(res, op="create_analyzer")

    def delete_analyzer(self, name: str, **kwargs: Any) -> Any:
        force = kwargs.get("force", False)
        path = f"/_db/{_q(self.name)}/_api/analyzer/{_q(name)}?force={'true' if force else 'false'}"
        res = self._request("DELETE", path, json_body=None)
        try:
            _unwrap_arango_result(res, op="delete_analyzer")
            return True
        except GatewayAPIError:
            if kwargs.get("ignore_missing"):
                return False
            raise

    def views(self) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/view")
        data = _unwrap_arango_result(res, op="views")
        return data.get("result", [])

    def view(self, name: str) -> Any:
        res = self._request("GET", f"/_db/{_q(self.name)}/_api/view/{_q(name)}/properties")
        return _unwrap_arango_result(res, op="view")

    def create_view(self, name: str, view_type: str, properties: Optional[Dict[str, Any]] = None) -> Any:
        body: Dict[str, Any] = {"name": name, "type": view_type}
        if properties:
            body.update(properties)
        res = self._request("POST", f"/_db/{_q(self.name)}/_api/view", json_body=body)
        return _unwrap_arango_result(res, op="create_view")

    def create_arangosearch_view(
        self, name: str, properties: Optional[Dict[str, Any]] = None
    ) -> Any:
        body: Dict[str, Any] = {"name": name, "type": "arangosearch"}
        if properties:
            body.update(properties)
        res = self._request("POST", f"/_db/{_q(self.name)}/_api/view", json_body=body)
        return _unwrap_arango_result(res, op="create_as_view")

    def update_view(self, name: str, properties: Dict[str, Any]) -> Any:
        res = self._request(
            "PATCH",
            f"/_db/{_q(self.name)}/_api/view/{_q(name)}/properties",
            json_body=properties,
        )
        return _unwrap_arango_result(res, op="update_view")

    def update_arangosearch_view(self, name: str, properties: Dict[str, Any]) -> Any:
        return self.update_view(name, properties)

    def replace_view(self, name: str, view_type: str, properties: Dict[str, Any]) -> Any:
        body = dict(properties)
        body.setdefault("type", view_type)
        res = self._request(
            "PUT",
            f"/_db/{_q(self.name)}/_api/view/{_q(name)}/properties",
            json_body=body,
        )
        return _unwrap_arango_result(res, op="replace_view")

    def replace_arangosearch_view(self, name: str, properties: Dict[str, Any]) -> Any:
        return self.replace_view(name, "arangosearch", properties)

    def delete_view(self, name: str, **kwargs: Any) -> Any:
        res = self._request("DELETE", f"/_db/{_q(self.name)}/_api/view/{_q(name)}", json_body=None)
        try:
            _unwrap_arango_result(res, op="delete_view")
            return True
        except GatewayAPIError as e:
            if kwargs.get("ignore_missing") and e.error_code in (1203, 1207):
                return False
            raise
