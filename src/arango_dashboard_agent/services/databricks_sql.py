"""Helpers for the Databricks SQL Statement Execution API."""

from typing import Any

from databricks.sdk import WorkspaceClient


def execute_sql(statement: str, warehouse_id: str) -> dict[str, Any]:
    """Execute SQL and return payload with columns and rows."""
    workspace_client = WorkspaceClient()
    response = workspace_client.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        wait_timeout="30s",
    )

    raw_status = response.status.state if response.status else None
    status = str(raw_status) if raw_status is not None else ""
    if status and not status.endswith("SUCCEEDED"):
        err = response.status.error.message if response.status.error else "unknown error"
        raise RuntimeError(f"Databricks SQL statement failed ({status}): {err}")

    if not response.manifest or not response.manifest.schema:
        return {"columns": [], "rows": []}

    columns = [col.name for col in response.manifest.schema.columns]
    rows = []
    if response.result and response.result.data_array:
        for row in response.result.data_array:
            rows.append(dict(zip(columns, row)))

    return {"columns": columns, "rows": rows}
