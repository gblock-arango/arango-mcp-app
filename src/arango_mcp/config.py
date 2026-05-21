from __future__ import annotations

from pathlib import Path
from typing import Any

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


_DEFAULT_GENIE_SPACE_REGISTRY_TABLE = "workspace.default.genie_space_registry"
_DEFAULT_ARANGO_AGENT_REGISTRY_TABLE = "workspace.default.arango_agent_registry"
_DEFAULT_ARANGO_WORKFLOW_REGISTRY_TABLE = "workspace.default.arango_workflow_registry"


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

    debug_startup_checks: bool = Field(
        default=False,
        description="Run Genie startup diagnostics when the HTTP app boots; env: DEBUG_STARTUP_CHECKS",
    )
    debug_webhook_url: str = Field(
        default="",
        description="Optional POST target for startup debug payload; env: DEBUG_WEBHOOK_URL",
    )

    genie_space_id: str = Field(default="", description="env: GENIE_SPACE_ID")
    genie_space_registry_table: str = Field(
        default=_DEFAULT_GENIE_SPACE_REGISTRY_TABLE,
        description="env: GENIE_SPACE_REGISTRY_TABLE",
    )
    genie_space_registry_auto_create: bool = Field(
        default=True,
        description="env: GENIE_SPACE_REGISTRY_AUTO_CREATE",
    )
    genie_auto_provision: bool = Field(default=True, description="env: GENIE_AUTO_PROVISION")
    genie_disable_auto_provision: bool = Field(
        default=False,
        description="env: GENIE_DISABLE_AUTO_PROVISION",
    )
    genie_serialized_space: str = Field(default="", description="env: GENIE_SERIALIZED_SPACE")
    genie_serialized_space_file: str = Field(
        default="",
        description="env: GENIE_SERIALIZED_SPACE_FILE",
    )
    genie_space_title: str = Field(
        default="Genie (Arango agent)",
        description="env: GENIE_SPACE_TITLE",
    )
    genie_space_description: str = Field(default="", description="env: GENIE_SPACE_DESCRIPTION")
    genie_space_parent_path: str = Field(default="", description="env: GENIE_SPACE_PARENT_PATH")
    genie_provision_lock_path: str = Field(default="", description="env: GENIE_PROVISION_LOCK_PATH")
    genie_message_timeout_seconds: float = Field(
        default=600.0,
        description="env: GENIE_MESSAGE_TIMEOUT_SECONDS",
    )

    arango_agent_registry_table: str = Field(
        default=_DEFAULT_ARANGO_AGENT_REGISTRY_TABLE,
        description="UC Delta table where this app publishes its public base_url; env: ARANGO_AGENT_REGISTRY_TABLE",
    )
    arango_agent_registry_auto_create: bool = Field(
        default=True,
        description="When true, app startup upserts this app's URL into ARANGO_AGENT_REGISTRY_TABLE; env: ARANGO_AGENT_REGISTRY_AUTO_CREATE",
    )

    arango_workflow_app_base_url: str = Field(
        default="",
        description="Optional override for arango-workflow-app URL; env: ARANGO_WORKFLOW_APP_BASE_URL",
    )
    arango_workflow_registry_table: str = Field(
        default=_DEFAULT_ARANGO_WORKFLOW_REGISTRY_TABLE,
        description="UC table where arango-workflow-app publishes base_url; env: ARANGO_WORKFLOW_REGISTRY_TABLE",
    )

    mcp_cors_allow_origins: str = Field(
        default="",
        description="Comma-separated ``Access-Control-Allow-Origin`` values for ``/mcp`` (Genie Code / browser). "
        "Use ``*`` for dev (no credentials). When empty in Databricks App runtime, ``asgi`` derives a single "
        "origin from ``DATABRICKS_HOST`` so Genie Code in the workspace UI can call this app without hand "
        "configuring CORS. env: MCP_CORS_ALLOW_ORIGINS",
    )

    arango_conversation_url: str = Field(
        default="",
        description="Optional HTTPS URL for POST /api/arango/chat to forward to (cluster ADA). Empty => stub. "
        "env: ARANGO_CONVERSATION_URL",
    )
    arango_conversation_timeout_seconds: float = Field(
        default=120.0,
        description="env: ARANGO_CONVERSATION_TIMEOUT_SECONDS",
    )

    geniemcp_serving_endpoint: str = Field(
        default="",
        description="Serving endpoint for MCP mode chat (OpenAI-compatible); env: GENIEMCP_SERVING_ENDPOINT",
    )
    tool_router_serving_endpoint: str = Field(
        default="",
        description="Optional separate serving endpoint for ToolRouter / orchestration LLM; when set, "
        "MCP dashboard chat prefers this over GENIEMCP_SERVING_ENDPOINT. env: TOOL_ROUTER_SERVING_ENDPOINT",
    )
    geniemcp_max_tools: int = Field(
        default=20,
        description="Max tools on Genie Code /mcp from genie_code_manifest.json (Databricks ~20-tool "
        "workspace cap); env: GENIEMCP_MAX_TOOLS",
    )
    geniemcp_model_max_tools: int = Field(
        default=32,
        description="Max tools sent to the serving endpoint per chat request (Databricks Llama cap is 32). "
        "Full catalog remains on /mcp/internal; orchestrator picks a subset per turn. "
        "env: GENIEMCP_MODEL_MAX_TOOLS",
    )
    geniemcp_max_rounds: int = Field(
        default=8,
        description="Max assistant↔tool rounds per request; env: GENIEMCP_MAX_ROUNDS",
    )
    geniemcp_foundation_model_query: str = Field(
        default="",
        description="When GENIEMCP_SERVING_ENDPOINT and TOOL_ROUTER_SERVING_ENDPOINT are unset, resolve a "
        "READY serving endpoint by matching this string to endpoint names and served foundation_model.name "
        "(SDK list/get). env: GENIEMCP_FOUNDATION_MODEL_QUERY",
    )
    geniemcp_resolve_foundation_endpoint_deep: bool = Field(
        default=False,
        description="When resolving GENIEMCP_FOUNDATION_MODEL_QUERY, call serving_endpoints.get for every "
        "endpoint if list() omits foundation_model metadata (slower). env: GENIEMCP_RESOLVE_FOUNDATION_ENDPOINT_DEEP",
    )

    arango: ArangoDBSettings = Field(default_factory=ArangoDBSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    server: ServerSettings = Field(default_factory=ServerSettings)


def gateway_resolution_config(app: AppSettings) -> dict[str, str]:
    """Mapping expected by :func:`arango_dashboard_agent.services.gateway_url_registry.effective_gateway_base_url`."""
    return {
        "ARANGO_GATEWAY_BASE_URL": app.gateway.base_url,
        "ARANGO_GATEWAY_REGISTRY_TABLE": app.gateway.registry_table,
        "DATABRICKS_SQL_WAREHOUSE_ID": app.databricks_sql_warehouse_id,
    }


def flask_app_config(app: AppSettings | None = None) -> dict[str, Any]:
    """Uppercase keys for Flask ``app.config`` (Genie registry + gateway URL resolution)."""
    s = app or settings
    return {
        **gateway_resolution_config(s),
        "ARANGO_REGISTRY_TABLE": s.arango_registry_table,
        "DEBUG_STARTUP_CHECKS": s.debug_startup_checks,
        "DEBUG_WEBHOOK_URL": s.debug_webhook_url,
        "GENIE_SPACE_ID": s.genie_space_id,
        "GENIE_SPACE_REGISTRY_TABLE": s.genie_space_registry_table,
        "GENIE_SPACE_REGISTRY_AUTO_CREATE": s.genie_space_registry_auto_create,
        "GENIE_AUTO_PROVISION": s.genie_auto_provision,
        "GENIE_DISABLE_AUTO_PROVISION": s.genie_disable_auto_provision,
        "GENIE_SERIALIZED_SPACE": s.genie_serialized_space,
        "GENIE_SERIALIZED_SPACE_FILE": s.genie_serialized_space_file,
        "GENIE_SPACE_TITLE": s.genie_space_title,
        "GENIE_SPACE_DESCRIPTION": s.genie_space_description,
        "GENIE_SPACE_PARENT_PATH": s.genie_space_parent_path,
        "GENIE_PROVISION_LOCK_PATH": s.genie_provision_lock_path,
        "GENIE_MESSAGE_TIMEOUT_SECONDS": s.genie_message_timeout_seconds,
        "ARANGO_AGENT_REGISTRY_TABLE": s.arango_agent_registry_table,
        "ARANGO_AGENT_REGISTRY_AUTO_CREATE": s.arango_agent_registry_auto_create,
        "ARANGO_WORKFLOW_APP_BASE_URL": (s.arango_workflow_app_base_url or "").strip(),
        "ARANGO_WORKFLOW_REGISTRY_TABLE": s.arango_workflow_registry_table,
        "MCP_CORS_ALLOW_ORIGINS": s.mcp_cors_allow_origins,
        "ARANGO_CONVERSATION_URL": (s.arango_conversation_url or "").strip(),
        "ARANGO_CONVERSATION_TIMEOUT_SECONDS": s.arango_conversation_timeout_seconds,
        "GENIEMCP_SERVING_ENDPOINT": (s.geniemcp_serving_endpoint or "").strip(),
        "TOOL_ROUTER_SERVING_ENDPOINT": (s.tool_router_serving_endpoint or "").strip(),
        "GENIEMCP_MAX_TOOLS": int(s.geniemcp_max_tools),
        "GENIEMCP_MODEL_MAX_TOOLS": int(s.geniemcp_model_max_tools),
        "GENIEMCP_MAX_ROUNDS": int(s.geniemcp_max_rounds),
        "GENIEMCP_FOUNDATION_MODEL_QUERY": (s.geniemcp_foundation_model_query or "").strip(),
        "GENIEMCP_RESOLVE_FOUNDATION_ENDPOINT_DEEP": bool(s.geniemcp_resolve_foundation_endpoint_deep),
    }


def genie_cli_config_dict() -> dict[str, Any]:
    """Config dict for ``arango_dashboard_agent.provision_genie_uc`` / deploy scripts (uses process env via ``settings``)."""
    return flask_app_config(settings)


# Global settings instance
settings = AppSettings()
