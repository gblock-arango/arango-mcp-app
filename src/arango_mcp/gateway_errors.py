"""Gateway-mode Arango error types (no ``python-arango`` dependency).

All Arango REST failures surface as :class:`~arango_mcp.gateway_database.GatewayAPIError`.
These names exist only for backwards-compatible decorator signatures.
"""

from __future__ import annotations

from arango_mcp.gateway_database import GatewayAPIError

# Re-export the single real error type used in gateway mode.
ArangoGatewayError = GatewayAPIError

# Legacy aliases — never raised directly; gateway raises GatewayAPIError with error_code.
AnalyzerGetError = GatewayAPIError
AQLQueryExplainError = GatewayAPIError
AQLQueryValidateError = GatewayAPIError
CollectionConfigureError = GatewayAPIError
DocumentReplaceError = GatewayAPIError
TransactionInitError = GatewayAPIError
TransactionListError = GatewayAPIError
UserUpdateError = GatewayAPIError
ViewGetError = GatewayAPIError
ViewUpdateError = GatewayAPIError
