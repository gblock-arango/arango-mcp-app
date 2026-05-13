"""Shared Unity Catalog table FQN types (decoupled from Arango registry service)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RegistryTableRef:
    catalog: str
    schema: str
    table: str

    @property
    def fqn(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{self.table}`"


def parse_fqn_table(table_name: str) -> RegistryTableRef:
    """Parse ``catalog.schema.table``."""
    parts = table_name.split(".")
    if len(parts) != 3 or any(not p.strip() for p in parts):
        raise ValueError("table name must be fully qualified as catalog.schema.table")
    return RegistryTableRef(catalog=parts[0], schema=parts[1], table=parts[2])
