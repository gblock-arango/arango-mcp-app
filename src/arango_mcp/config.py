from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ArangoDBSettings(BaseSettings):
    """ArangoDB connection and configuration settings.

    Credentials are loaded from environment variables that should be configured
    in the MCP client's mcp.json configuration file.

    Example mcp.json (repo root = this project; ``PYTHONPATH=src`` loads ``arango_mcp``):
    {
      "mcpServers": {
        "arangodb-mcp": {
          "command": "poetry",
          "args": ["run", "python", "-m", "arango_mcp.main"],
          "env": {
            "PYTHONPATH": "src",
            "ARANGO_HOSTS": "http://localhost:8529",
            "ARANGO_ROOT_USERNAME": "root",
            "ARANGO_ROOT_PASSWORD": "your_password_here",
            "ARANGO_DEFAULT_DB_NAME": "myapp"
          }
        }
      }
    }
    """

    model_config = SettingsConfigDict(env_prefix="ARANGO_", env_file=".env", extra="ignore")

    # Connection settings for **direct** python-arango mode (ignored when gateway is set).
    hosts: str = Field(
        default="",
        description="ArangoDB server URLs (e.g., http://localhost:8529); required if not using gateway",
    )
    root_username: str = Field(
        default="",
        description="ArangoDB username for direct mode",
    )
    root_password: str = Field(
        default="",
        description="ArangoDB password for direct mode; required if not using gateway",
    )
    default_db_name: str = Field(default="_system", description="Default database name")

    # Connection pool settings
    max_connections: int = Field(
        default=50, description="Maximum concurrent connections (reserved, not yet wired)"
    )
    timeout: int = Field(
        default=30, description="Connection timeout in seconds (reserved, not yet wired)"
    )

    # SSL settings
    verify_ssl: bool = Field(default=True, description="Verify SSL certificates")
    ssl_cert_path: str = Field(
        default="", description="Path to SSL certificate file (supports cross-platform paths)"
    )

    @field_validator("ssl_cert_path")
    @classmethod
    def validate_ssl_cert_path(cls, v: str) -> str:
        """Validate and normalize SSL certificate path for cross-platform compatibility."""
        if not v:  # Empty string is valid (no SSL cert)
            return v

        try:
            # Convert to Path object for cross-platform handling
            cert_path = Path(v).resolve()

            # Check if file exists (only if path is provided)
            if not cert_path.exists():
                raise ValueError(f"SSL certificate file not found: {cert_path}")

            if not cert_path.is_file():
                raise ValueError(f"SSL certificate path is not a file: {cert_path}")

            # Return the resolved absolute path as string
            return str(cert_path)

        except Exception as e:
            raise ValueError(f"Invalid SSL certificate path '{v}': {e}") from e


class GatewaySettings(BaseSettings):
    """``arango-gateway-app`` base URL and auth for ``POST /api/arango/http``."""

    model_config = SettingsConfigDict(env_prefix="ARANGO_GATEWAY_", env_file=".env", extra="ignore")

    base_url: str = Field(
        default="",
        description="HTTPS origin of arango-gateway-app (no trailing slash), e.g. https://….databricksapps.com",
    )
    registry_table: str = Field(
        default="workspace.default.arango_gateway_registry",
        description="UC Delta table where arango-gateway-app publishes base_url (catalog.schema.table); "
        "env: ARANGO_GATEWAY_REGISTRY_TABLE",
    )
    bearer_token: str = Field(
        default="",
        description="Rarely needed: optional Bearer for gateway ingress if your deployment requires it "
        "(same default as arango-dashboard-app: resolve URL from UC, call gateway without token)",
    )
    tls_verify: bool = Field(default=True, description="Verify TLS when calling the gateway")
    timeout_seconds: float = Field(
        default=120.0,
        description="HTTP timeout for each gateway / Arango forward call",
    )


class ServerSettings(BaseSettings):
    """MCP server configuration settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    server_name: str = "ArangoDB MCP Server"
    server_version: str = "2.0.0"
    log_level: str = "INFO"
    enable_metrics: bool = Field(
        default=False, description="Enable metrics collection (reserved, not yet wired)"
    )
    enable_js_transactions: bool = Field(
        default=False,
        description="Enable server-side JavaScript transaction execution (execute-transaction tool). "
        "Disabled by default because it allows arbitrary JS on the database server.",
    )


class AppSettings(BaseSettings):
    """Main application settings container."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    databricks_sql_warehouse_id: str = Field(
        default="",
        description="SQL warehouse id for UC reads (gateway URL registry); env: DATABRICKS_SQL_WAREHOUSE_ID",
    )
    arango_registry_table: str = Field(
        default="workspace.default.arango_connection_registry",
        description="UC Arango connection registry FQN (for deploy grants / future tools); env: ARANGO_REGISTRY_TABLE",
    )

    arango: ArangoDBSettings = Field(default_factory=ArangoDBSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    server: ServerSettings = Field(default_factory=ServerSettings)


def gateway_resolution_config(app: AppSettings) -> dict[str, str]:
    """Mapping expected by :func:`arango_mcp.services.gateway_url_registry.effective_gateway_base_url`."""
    return {
        "ARANGO_GATEWAY_BASE_URL": app.gateway.base_url,
        "ARANGO_GATEWAY_REGISTRY_TABLE": app.gateway.registry_table,
        "DATABRICKS_SQL_WAREHOUSE_ID": app.databricks_sql_warehouse_id,
    }


# Global settings instance
settings = AppSettings()
