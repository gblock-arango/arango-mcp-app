import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Union

from arango import ArangoClient
from arango.database import StandardDatabase

from config import settings
from gateway_arango_client import GatewayArangoClient
from gateway_database import GatewayDatabase

logger = logging.getLogger(__name__)


class ArangoDBConnector:
    """Arango connectivity: direct ``python-arango`` or gateway-backed HTTP (``GatewayArangoClient``)."""

    def __init__(self) -> None:
        self.client: Optional[ArangoClient] = None
        self._default_db: Optional[StandardDatabase] = None
        self._server_version: Optional[str] = None
        self._gateway_client: Optional[GatewayArangoClient] = None
        self._gateway_databases: dict[str, GatewayDatabase] = {}
        self._use_gateway: bool = False

    @property
    def server_version(self) -> Optional[str]:
        if self._use_gateway and self._gateway_client:
            return self._gateway_client.server_version
        return self._server_version

    @property
    def gateway(self) -> Optional[GatewayArangoClient]:
        """Set when ``ARANGO_GATEWAY_BASE_URL`` is configured; use ``.request(...)`` for REST."""
        return self._gateway_client

    async def connect(self) -> None:
        """Establish connection (gateway HTTP or direct python-arango)."""
        gw_url = (settings.gateway.base_url or "").strip()
        if gw_url:
            self._use_gateway = True
            self._gateway_client = GatewayArangoClient(settings.gateway)
            self._gateway_client.connect()
            self._gateway_databases = {}
            self.client = None
            self._default_db = None
            self._server_version = self._gateway_client.server_version
            logger.info(
                "Using Arango gateway at %s (Arango server version=%s)",
                gw_url,
                self._server_version,
            )
            return

        self._use_gateway = False
        self._gateway_client = None
        self._gateway_databases = {}
        try:
            if not (settings.arango.hosts or "").strip():
                raise ValueError(
                    "ARANGO_HOSTS is required for direct Arango mode "
                    "(or set ARANGO_GATEWAY_BASE_URL for gateway mode)."
                )
            if not settings.arango.root_password:
                raise ValueError(
                    "ArangoDB password not configured. "
                    "Set ARANGO_ROOT_PASSWORD for direct mode or ARANGO_GATEWAY_BASE_URL for gateway mode."
                )

            hosts = [host.strip() for host in settings.arango.hosts.split(",") if host.strip()]

            logger.info(
                "Connecting to ArangoDB at: %s as user: %s",
                hosts,
                settings.arango.root_username,
            )

            client_kwargs: dict = {"hosts": hosts}

            if settings.arango.verify_ssl:
                client_kwargs["verify_override"] = True
                if settings.arango.ssl_cert_path:
                    client_kwargs["verify_override"] = settings.arango.ssl_cert_path
            else:
                client_kwargs["verify_override"] = False

            self.client = ArangoClient(**client_kwargs)

            self._default_db = self.client.db(
                settings.arango.default_db_name,
                username=settings.arango.root_username,
                password=settings.arango.root_password,
            )

            self._server_version = self._default_db.version()
            logger.info("Connected to ArangoDB server version: %s", self._server_version)

        except ValueError as e:
            logger.error("Configuration error: %s", e)
            raise
        except Exception as e:
            error_msg = str(e).lower()
            if "connection" in error_msg or "connect" in error_msg:
                logger.error("Failed to connect to ArangoDB: %s", e)
            elif "authentication" in error_msg or "unauthorized" in error_msg:
                logger.error("Database authentication failed: %s", e)
            else:
                logger.error("ArangoDB connection error: %s", e)
            raise

    async def disconnect(self) -> None:
        try:
            if self._gateway_client:
                logger.info("Disconnecting from Arango gateway")
                self._gateway_client.disconnect()
                self._gateway_client = None
                self._gateway_databases = {}
                self._use_gateway = False
                self._server_version = None
                return
            if self.client:
                logger.info("Disconnecting from ArangoDB")
                self.client = None
                self._default_db = None
                self._server_version = None
        except Exception as e:
            logger.warning("Error during ArangoDB disconnection: %s", e)

    def get_db(self, db_name: Optional[str] = None) -> Union[StandardDatabase, GatewayDatabase]:
        """Database handle: ``StandardDatabase`` (direct) or ``GatewayDatabase`` (gateway)."""
        if self._use_gateway and self._gateway_client:
            database_name = db_name or settings.arango.default_db_name
            if database_name not in self._gateway_databases:
                self._gateway_databases[database_name] = GatewayDatabase(
                    self._gateway_client, database_name
                )
            return self._gateway_databases[database_name]
        if not self.client:
            raise RuntimeError("ArangoDB client not initialized. Call connect() first.")

        database_name = db_name or settings.arango.default_db_name

        return self.client.db(
            database_name,
            username=settings.arango.root_username,
            password=settings.arango.root_password,
        )

    def get_system_db(self) -> Union[StandardDatabase, GatewayDatabase]:
        return self.get_db("_system")

    def health_check(self) -> bool:
        if self._gateway_client:
            return self._gateway_client.health_check()
        try:
            if not self.client or not self._default_db:
                return False
            self._default_db.properties()
            return True
        except Exception as e:
            logger.warning("Health check failed: %s", e)
            return False


# Global connector instance
arango_connector = ArangoDBConnector()


@asynccontextmanager
async def arango_db_lifespan(mcp_server_instance) -> AsyncIterator[ArangoDBConnector]:
    """Lifespan context manager for MCP server with ArangoDB connection management."""
    logger.info("Starting ArangoDB MCP Server...")

    try:
        await arango_connector.connect()
        logger.info("ArangoDB connection established successfully")

        yield arango_connector

    except Exception as e:
        logger.error("Failed to initialize ArangoDB connection: %s", e)
        raise
    finally:
        logger.info("Shutting down ArangoDB MCP Server...")
        await arango_connector.disconnect()
        logger.info("ArangoDB connection closed")
