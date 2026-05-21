import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from arango_dashboard_agent.services.gateway_url_registry import effective_gateway_base_url

from arango_mcp.config import gateway_resolution_config, settings
from arango_mcp.gateway_arango_client import GatewayArangoClient
from arango_mcp.gateway_database import GatewayDatabase

logger = logging.getLogger(__name__)


class ArangoDBConnector:
    """Arango connectivity via ``arango-gateway-app`` only (no ``python-arango``)."""

    def __init__(self) -> None:
        self._server_version: Optional[str] = None
        self._gateway_client: Optional[GatewayArangoClient] = None
        self._gateway_databases: dict[str, GatewayDatabase] = {}

    @property
    def server_version(self) -> Optional[str]:
        if self._gateway_client:
            return self._gateway_client.server_version
        return self._server_version

    @property
    def gateway(self) -> Optional[GatewayArangoClient]:
        return self._gateway_client

    async def connect(
        self,
        *,
        gateway_auth_config: dict | None = None,
        outbound_bearer: str | None = None,
    ) -> None:
        cfg = (
            gateway_auth_config
            if gateway_auth_config is not None
            else gateway_resolution_config(settings)
        )
        effective_gw = effective_gateway_base_url(cfg).strip()

        if not effective_gw:
            raise ValueError(
                "Gateway URL is unset: set ARANGO_GATEWAY_BASE_URL or publish an active row to "
                "ARANGO_GATEWAY_REGISTRY_TABLE and set DATABRICKS_SQL_WAREHOUSE_ID for UC reads."
            )

        auth_cfg = dict(cfg) if isinstance(cfg, dict) else gateway_resolution_config(settings)
        self._gateway_client = GatewayArangoClient(
            settings.gateway,
            effective_base_url=effective_gw,
            outbound_bearer=outbound_bearer,
            auth_config=auth_cfg,
        )
        self._gateway_client.connect()
        self._gateway_databases = {}
        self._server_version = self._gateway_client.server_version
        logger.info(
            "Using Arango gateway at %s (Arango server version=%s)",
            effective_gw,
            self._server_version,
        )

    async def disconnect(self) -> None:
        try:
            if self._gateway_client:
                logger.info("Disconnecting from Arango gateway")
                self._gateway_client.disconnect()
                self._gateway_client = None
                self._gateway_databases = {}
                self._server_version = None
        except Exception as e:
            logger.warning("Error during Arango gateway disconnection: %s", e)

    def get_db(self, db_name: Optional[str] = None) -> GatewayDatabase:
        if not self._gateway_client:
            raise RuntimeError("Arango gateway not connected. Call connect() first.")
        database_name = db_name or settings.arango.default_db_name
        if database_name not in self._gateway_databases:
            self._gateway_databases[database_name] = GatewayDatabase(
                self._gateway_client, database_name
            )
        return self._gateway_databases[database_name]

    def get_system_db(self) -> GatewayDatabase:
        return self.get_db("_system")

    def health_check(self) -> bool:
        if self._gateway_client:
            return self._gateway_client.health_check()
        return False


arango_connector = ArangoDBConnector()


@asynccontextmanager
async def arango_db_lifespan(mcp_server_instance) -> AsyncIterator[ArangoDBConnector]:
    """Lifespan context manager for MCP server with Arango gateway connection management."""
    logger.info("Starting ArangoDB MCP Server...")

    try:
        await arango_connector.connect()
        logger.info("Arango gateway connection established successfully")
    except Exception as e:
        logger.warning(
            "Arango gateway connect failed at MCP session start (MCP list/handshake may "
            "still succeed; Arango tools fail until gateway registry is configured): %s",
            e,
        )

    try:
        yield arango_connector
    finally:
        logger.info("Shutting down ArangoDB MCP Server...")
        await arango_connector.disconnect()
        logger.info("Arango gateway connection closed")
