"""Schemas des PostgreSQL-Studios.

Strukturänderungen kommen als Spezifikation (``StudioOperation``), nie als
SQL. ``services.postgres_ddl`` übersetzt sie; Vorschau und Ausführung
übersetzen dieselbe Spezifikation.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Ident = Annotated[str, Field(min_length=1, max_length=63)]
Expr = Annotated[str, Field(min_length=1, max_length=20_000)]
TypeName = Annotated[str, Field(min_length=1, max_length=120)]
Literal_ = Annotated[str, Field(max_length=10_000)]

FkAction = Literal["no_action", "restrict", "cascade", "set_null", "set_default"]


class ColumnSpec(BaseModel):
    name: Ident
    type: TypeName
    nullable: bool = True
    default: Expr | None = None
    identity: Literal["always", "by_default"] | None = None
    comment: str | None = Field(None, max_length=2000)


class ForeignKeySpec(BaseModel):
    name: Ident | None = None
    columns: list[Ident] = Field(min_length=1, max_length=32)
    ref_schema: Ident = "public"
    ref_table: Ident
    ref_columns: list[Ident] = Field(min_length=1, max_length=32)
    on_delete: FkAction = "no_action"
    on_update: FkAction = "no_action"
    deferrable: bool = False


class CheckSpec(BaseModel):
    name: Ident | None = None
    expression: Expr


class UniqueSpec(BaseModel):
    name: Ident | None = None
    columns: list[Ident] = Field(min_length=1, max_length=32)
    nulls_not_distinct: bool = False


class PrimaryKeySpec(BaseModel):
    name: Ident | None = None
    columns: list[Ident] = Field(min_length=1, max_length=32)


class PartitionBySpec(BaseModel):
    strategy: Literal["range", "list", "hash"]
    columns: list[Ident] = Field(min_length=1, max_length=32)


# ── Schemas ────────────────────────────────────────────────────────────────


class CreateSchemaOp(BaseModel):
    op: Literal["create_schema"]
    name: Ident


class DropSchemaOp(BaseModel):
    op: Literal["drop_schema"]
    name: Ident
    cascade: bool = False


class RenameSchemaOp(BaseModel):
    op: Literal["rename_schema"]
    name: Ident
    new_name: Ident


# ── Tabellen ───────────────────────────────────────────────────────────────


class CreateTableOp(BaseModel):
    op: Literal["create_table"]
    schema_name: Ident = "public"
    name: Ident
    columns: list[ColumnSpec] = Field(min_length=1, max_length=1600)
    primary_key: PrimaryKeySpec | None = None
    uniques: list[UniqueSpec] = Field(default_factory=list, max_length=64)
    checks: list[CheckSpec] = Field(default_factory=list, max_length=64)
    foreign_keys: list[ForeignKeySpec] = Field(default_factory=list, max_length=64)
    partition_by: PartitionBySpec | None = None
    comment: str | None = Field(None, max_length=2000)


class DropTableOp(BaseModel):
    op: Literal["drop_table"]
    schema_name: Ident = "public"
    name: Ident
    cascade: bool = False


class AddColumnAction(BaseModel):
    action: Literal["add_column"]
    column: ColumnSpec


class DropColumnAction(BaseModel):
    action: Literal["drop_column"]
    column: Ident
    cascade: bool = False


class RenameColumnAction(BaseModel):
    action: Literal["rename_column"]
    column: Ident
    new_name: Ident


class AlterColumnTypeAction(BaseModel):
    action: Literal["alter_column_type"]
    column: Ident
    type: TypeName
    using: Expr | None = None


class SetDefaultAction(BaseModel):
    action: Literal["set_default"]
    column: Ident
    default: Expr | None = None


class SetNullableAction(BaseModel):
    action: Literal["set_nullable"]
    column: Ident
    nullable: bool


class AddPrimaryKeyAction(BaseModel):
    action: Literal["add_primary_key"]
    constraint: PrimaryKeySpec


class AddUniqueAction(BaseModel):
    action: Literal["add_unique"]
    constraint: UniqueSpec


class AddCheckAction(BaseModel):
    action: Literal["add_check"]
    constraint: CheckSpec


class AddForeignKeyAction(BaseModel):
    action: Literal["add_foreign_key"]
    constraint: ForeignKeySpec


class DropConstraintAction(BaseModel):
    action: Literal["drop_constraint"]
    name: Ident
    cascade: bool = False


class RenameTableAction(BaseModel):
    action: Literal["rename_table"]
    new_name: Ident


class SetCommentAction(BaseModel):
    action: Literal["set_comment"]
    column: Ident | None = None
    comment: str | None = Field(None, max_length=2000)


TableAction = Annotated[
    Union[
        AddColumnAction,
        DropColumnAction,
        RenameColumnAction,
        AlterColumnTypeAction,
        SetDefaultAction,
        SetNullableAction,
        AddPrimaryKeyAction,
        AddUniqueAction,
        AddCheckAction,
        AddForeignKeyAction,
        DropConstraintAction,
        RenameTableAction,
        SetCommentAction,
    ],
    Field(discriminator="action"),
]


class AlterTableOp(BaseModel):
    op: Literal["alter_table"]
    schema_name: Ident = "public"
    name: Ident
    actions: list[TableAction] = Field(min_length=1, max_length=200)


class PartitionBounds(BaseModel):
    """Grenzen als Werte, nie als Ausdruck: sie werden als Literale gesetzt.

    ``MINVALUE``/``MAXVALUE`` sind bei ``range`` als Wert ``None`` in
    ``from_values``/``to_values`` erlaubt (``minvalue``/``maxvalue`` je Seite).
    """

    kind: Literal["range", "list", "hash", "default"]
    from_values: list[Literal_ | None] = Field(default_factory=list, max_length=32)
    to_values: list[Literal_ | None] = Field(default_factory=list, max_length=32)
    in_values: list[Literal_ | None] = Field(default_factory=list, max_length=1000)
    modulus: int | None = Field(None, ge=1, le=100_000)
    remainder: int | None = Field(None, ge=0, le=100_000)


class CreatePartitionOp(BaseModel):
    op: Literal["create_partition"]
    schema_name: Ident = "public"
    parent: Ident
    name: Ident
    bounds: PartitionBounds


class DetachPartitionOp(BaseModel):
    op: Literal["detach_partition"]
    schema_name: Ident = "public"
    parent: Ident
    name: Ident


# ── Indizes ────────────────────────────────────────────────────────────────


class IndexColumn(BaseModel):
    column: Ident | None = None
    expression: Expr | None = None
    descending: bool = False
    nulls: Literal["first", "last"] | None = None


class CreateIndexOp(BaseModel):
    op: Literal["create_index"]
    schema_name: Ident = "public"
    table: Ident
    name: Ident
    columns: list[IndexColumn] = Field(min_length=1, max_length=32)
    unique: bool = False
    method: Literal["btree", "hash", "gin", "gist", "brin", "spgist"] = "btree"
    include: list[Ident] = Field(default_factory=list, max_length=32)
    where: Expr | None = None
    concurrently: bool = False


class DropIndexOp(BaseModel):
    op: Literal["drop_index"]
    schema_name: Ident = "public"
    name: Ident
    concurrently: bool = False
    cascade: bool = False


# ── Views ──────────────────────────────────────────────────────────────────


class CreateViewOp(BaseModel):
    op: Literal["create_view"]
    schema_name: Ident = "public"
    name: Ident
    query: Expr
    materialized: bool = False
    or_replace: bool = False
    with_data: bool = True


class DropViewOp(BaseModel):
    op: Literal["drop_view"]
    schema_name: Ident = "public"
    name: Ident
    materialized: bool = False
    cascade: bool = False


class RefreshMaterializedViewOp(BaseModel):
    op: Literal["refresh_materialized_view"]
    schema_name: Ident = "public"
    name: Ident
    concurrently: bool = False


# ── Sequenzen ──────────────────────────────────────────────────────────────


class CreateSequenceOp(BaseModel):
    op: Literal["create_sequence"]
    schema_name: Ident = "public"
    name: Ident
    start: int | None = None
    increment: int | None = None
    min_value: int | None = None
    max_value: int | None = None
    cycle: bool = False


class RestartSequenceOp(BaseModel):
    op: Literal["restart_sequence"]
    schema_name: Ident = "public"
    name: Ident
    value: int | None = None


class DropSequenceOp(BaseModel):
    op: Literal["drop_sequence"]
    schema_name: Ident = "public"
    name: Ident
    cascade: bool = False


# ── Enums ──────────────────────────────────────────────────────────────────


class CreateEnumOp(BaseModel):
    op: Literal["create_enum"]
    schema_name: Ident = "public"
    name: Ident
    values: list[Annotated[str, Field(min_length=1, max_length=63)]] = Field(min_length=1, max_length=500)


class AddEnumValueOp(BaseModel):
    op: Literal["add_enum_value"]
    schema_name: Ident = "public"
    name: Ident
    value: Annotated[str, Field(min_length=1, max_length=63)]
    before: str | None = Field(None, max_length=63)
    after: str | None = Field(None, max_length=63)


class RenameEnumValueOp(BaseModel):
    op: Literal["rename_enum_value"]
    schema_name: Ident = "public"
    name: Ident
    value: Annotated[str, Field(min_length=1, max_length=63)]
    new_value: Annotated[str, Field(min_length=1, max_length=63)]


class DropTypeOp(BaseModel):
    op: Literal["drop_type"]
    schema_name: Ident = "public"
    name: Ident
    cascade: bool = False


# ── Funktionen und Trigger ─────────────────────────────────────────────────


class CreateFunctionOp(BaseModel):
    op: Literal["create_function"]
    schema_name: Ident = "public"
    name: Ident
    arguments: str = Field("", max_length=4000)
    returns: str = Field(..., min_length=1, max_length=4000)
    language: Literal["plpgsql", "sql"] = "plpgsql"
    body: Expr
    volatility: Literal["volatile", "stable", "immutable"] = "volatile"
    security_definer: bool = False
    strict: bool = False
    or_replace: bool = True


class DropFunctionOp(BaseModel):
    op: Literal["drop_function"]
    schema_name: Ident = "public"
    name: Ident
    arguments: str = Field("", max_length=4000)
    cascade: bool = False


class CreateTriggerOp(BaseModel):
    op: Literal["create_trigger"]
    schema_name: Ident = "public"
    table: Ident
    name: Ident
    timing: Literal["before", "after", "instead_of"]
    events: list[Literal["insert", "update", "delete", "truncate"]] = Field(min_length=1, max_length=4)
    update_columns: list[Ident] = Field(default_factory=list, max_length=64)
    for_each: Literal["row", "statement"] = "row"
    function_schema: Ident = "public"
    function_name: Ident
    when: Expr | None = None


class DropTriggerOp(BaseModel):
    op: Literal["drop_trigger"]
    schema_name: Ident = "public"
    table: Ident
    name: Ident


class SetTriggerEnabledOp(BaseModel):
    op: Literal["set_trigger_enabled"]
    schema_name: Ident = "public"
    table: Ident
    name: Ident
    enabled: bool


# ── Extensions ─────────────────────────────────────────────────────────────


class CreateExtensionOp(BaseModel):
    op: Literal["create_extension"]
    name: Ident
    schema_name: Ident | None = None


class DropExtensionOp(BaseModel):
    op: Literal["drop_extension"]
    name: Ident
    cascade: bool = False


class UpdateExtensionOp(BaseModel):
    op: Literal["update_extension"]
    name: Ident


# ── Sicherheit ─────────────────────────────────────────────────────────────


class SetRowLevelSecurityOp(BaseModel):
    op: Literal["set_rls"]
    schema_name: Ident = "public"
    table: Ident
    enabled: bool
    forced: bool = False


class CreatePolicyOp(BaseModel):
    op: Literal["create_policy"]
    schema_name: Ident = "public"
    table: Ident
    name: Ident
    command: Literal["all", "select", "insert", "update", "delete"] = "all"
    permissive: bool = True
    roles: list[Ident] = Field(default_factory=list, max_length=64)
    using: Expr | None = None
    with_check: Expr | None = None


class DropPolicyOp(BaseModel):
    op: Literal["drop_policy"]
    schema_name: Ident = "public"
    table: Ident
    name: Ident


GrantObjectType = Literal["table", "sequence", "schema", "function"]


class GrantOp(BaseModel):
    op: Literal["grant", "revoke"]
    object_type: GrantObjectType
    schema_name: Ident = "public"
    # Leer bei object_type=schema. Funktionen mit Signatur: ``name(args)``.
    objects: list[Annotated[str, Field(min_length=1, max_length=4000)]] = Field(default_factory=list, max_length=500)
    privileges: list[str] = Field(min_length=1, max_length=16)
    roles: list[Ident] = Field(min_length=1, max_length=64)
    grant_option: bool = False


class RoleAttributes(BaseModel):
    # SUPERUSER/REPLICATION gibt es hier bewusst nicht; ein Tippfehler oder
    # ein nachgereichtes Feld soll auffallen statt still zu verschwinden.
    model_config = ConfigDict(extra="forbid")

    login: bool | None = None
    inherit: bool | None = None
    createdb: bool | None = None
    createrole: bool | None = None
    bypassrls: bool | None = None
    connection_limit: int | None = Field(None, ge=-1, le=100_000)
    valid_until: str | None = Field(None, max_length=64)
    password: str | None = Field(None, min_length=8, max_length=128)


class CreateRoleOp(BaseModel):
    op: Literal["create_role"]
    name: Ident
    attributes: RoleAttributes = Field(default_factory=RoleAttributes)
    member_of: list[Ident] = Field(default_factory=list, max_length=64)


class AlterRoleOp(BaseModel):
    op: Literal["alter_role"]
    name: Ident
    attributes: RoleAttributes = Field(default_factory=RoleAttributes)
    grant_membership: list[Ident] = Field(default_factory=list, max_length=64)
    revoke_membership: list[Ident] = Field(default_factory=list, max_length=64)


class DropRoleOp(BaseModel):
    op: Literal["drop_role"]
    name: Ident


# ── Instanz ────────────────────────────────────────────────────────────────


class SetParameterOp(BaseModel):
    op: Literal["set_parameter"]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    value: Literal_


class ResetParameterOp(BaseModel):
    op: Literal["reset_parameter"]
    name: Annotated[str, Field(min_length=1, max_length=100)]


# ── Wartung ────────────────────────────────────────────────────────────────


class VacuumOp(BaseModel):
    op: Literal["vacuum"]
    schema_name: Ident | None = None
    table: Ident | None = None
    full: bool = False
    analyze: bool = True


class AnalyzeOp(BaseModel):
    op: Literal["analyze"]
    schema_name: Ident | None = None
    table: Ident | None = None


class ReindexOp(BaseModel):
    op: Literal["reindex"]
    target: Literal["table", "index"]
    schema_name: Ident = "public"
    name: Ident
    concurrently: bool = False


StudioOperation = Annotated[
    Union[
        CreateSchemaOp,
        DropSchemaOp,
        RenameSchemaOp,
        CreateTableOp,
        DropTableOp,
        AlterTableOp,
        CreatePartitionOp,
        DetachPartitionOp,
        CreateIndexOp,
        DropIndexOp,
        CreateViewOp,
        DropViewOp,
        RefreshMaterializedViewOp,
        CreateSequenceOp,
        RestartSequenceOp,
        DropSequenceOp,
        CreateEnumOp,
        AddEnumValueOp,
        RenameEnumValueOp,
        DropTypeOp,
        CreateFunctionOp,
        DropFunctionOp,
        CreateTriggerOp,
        DropTriggerOp,
        SetTriggerEnabledOp,
        CreateExtensionOp,
        DropExtensionOp,
        UpdateExtensionOp,
        SetRowLevelSecurityOp,
        CreatePolicyOp,
        DropPolicyOp,
        GrantOp,
        CreateRoleOp,
        AlterRoleOp,
        DropRoleOp,
        SetParameterOp,
        ResetParameterOp,
        VacuumOp,
        AnalyzeOp,
        ReindexOp,
    ],
    Field(discriminator="op"),
]


class StudioOperationRequest(BaseModel):
    operation: StudioOperation


class StudioPlanResponse(BaseModel):
    statements: list[str]
    mode: str
    identity: str
    scope: str
    destructive: bool
    permission: str


class StudioRunResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = -1
    status: str | None = None
    truncated: bool = False
    duration_ms: int = 0


class StudioExecuteResponse(BaseModel):
    plan: StudioPlanResponse
    results: list[StudioRunResult] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    duration_ms: int = 0


# ── Abfragen, Daten, Überwachung ───────────────────────────────────────────


class StudioSqlRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=1_000_000)
    # rollback: alles am Ende verwerfen (Funktionstest). autocommit: fuer
    # Anweisungen ohne Transaktion (VACUUM, CREATE INDEX CONCURRENTLY).
    rollback: bool = False
    autocommit: bool = False
    row_limit: int = Field(500, ge=1, le=5000)


    @model_validator(mode="after")
    def _eins_von_beiden(self) -> "StudioSqlRequest":
        if self.rollback and self.autocommit:
            raise ValueError("Rollback und Autocommit schließen sich aus.")
        return self


class StudioSqlResponse(BaseModel):
    results: list[StudioRunResult]
    notices: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    rolled_back: bool = False


class StudioExplainRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=200_000)
    analyze: bool = False


FilterOperator = Literal[
    "eq", "neq", "lt", "lte", "gt", "gte",
    "like", "ilike", "not_like", "is_null", "not_null", "in", "contains",
]


class RowFilter(BaseModel):
    column: Ident
    operator: FilterOperator
    value: Any = None


class RowSort(BaseModel):
    column: Ident
    descending: bool = False


class StudioRowsRequest(BaseModel):
    schema_name: Ident = "public"
    table: Ident
    filters: list[RowFilter] = Field(default_factory=list, max_length=20)
    sort: list[RowSort] = Field(default_factory=list, max_length=8)
    limit: Literal[25, 50, 100, 500] = 50
    offset: int = Field(0, ge=0, le=100_000_000)


class StudioRowKey(BaseModel):
    """Zeilenschlüssel: Primärschlüsselwerte oder ``ctid`` ohne PK."""

    values: dict[str, Any] = Field(min_length=1, max_length=32)


class StudioRowInsertRequest(BaseModel):
    schema_name: Ident = "public"
    table: Ident
    values: dict[str, Any] = Field(default_factory=dict, max_length=1600)


class StudioRowUpdateRequest(BaseModel):
    schema_name: Ident = "public"
    table: Ident
    key: StudioRowKey
    changes: dict[str, Any] = Field(min_length=1, max_length=1600)


class StudioRowDeleteRequest(BaseModel):
    schema_name: Ident = "public"
    table: Ident
    keys: list[StudioRowKey] = Field(min_length=1, max_length=500)


class StudioImportRequest(BaseModel):
    schema_name: Ident = "public"
    table: Ident
    columns: list[Ident] = Field(min_length=1, max_length=1600)
    rows: list[list[Any]] = Field(min_length=1, max_length=5000)


class StudioExportRequest(BaseModel):
    schema_name: Ident = "public"
    table: Ident
    format: Literal["csv", "json", "sql"] = "csv"
    filters: list[RowFilter] = Field(default_factory=list, max_length=20)
    sort: list[RowSort] = Field(default_factory=list, max_length=8)


class StudioTableRef(BaseModel):
    schema_name: Ident
    name: Ident


class StudioDumpRequest(BaseModel):
    format: Literal["plain", "custom", "tar"] = "plain"
    schema_only: bool = False
    data_only: bool = False
    schemas: list[Ident] = Field(default_factory=list, max_length=100)
    tables: list[StudioTableRef] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _nicht_beides(self) -> "StudioDumpRequest":
        if self.schema_only and self.data_only:
            raise ValueError("Nur Schema und nur Daten schließen sich aus.")
        return self


class StudioRestoreRequest(BaseModel):
    format: Literal["plain", "custom", "tar"] = "plain"
    data_b64: str = Field(..., min_length=4, max_length=280_000_000)
    # Nur custom/tar: vorhandene Objekte vorher entfernen.
    clean: bool = False
    confirm_name: str = Field(..., min_length=1, max_length=63)


class StudioSessionActionRequest(BaseModel):
    action: Literal["cancel", "terminate"]
