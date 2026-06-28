import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Database, Package, Play, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { api } from "@/api/client";
import { toast } from "@/stores/toastStore";
import { confirm } from "@/stores/confirmStore";
import type { PostgresCredential, PostgresDatabase, PostgresExtension, PostgresResources, PostgresRowsResult } from "@/types";
import { PostgresCredentialsDialog } from "@/components/server/PostgresCredentialsDialog";

// Whitelist muss mit backend/config.py uebereinstimmen -- der Backend-Filter
// ist die eigentliche Sicherheitsgrenze. Hier nur als UI-Hint, damit der
// User nicht raten muss.
const AVAILABLE_EXTENSIONS = [
  { name: "pgcrypto", hint: "UUID/Crypto" },
  { name: "uuid-ossp", hint: "UUID" },
  { name: "citext", hint: "Case-insensitive Text" },
  { name: "hstore", hint: "Key/Value" },
  { name: "pg_trgm", hint: "Volltextsuche" },
  { name: "btree_gin", hint: "GIN-Index" },
  { name: "btree_gist", hint: "GiST-Index" },
  { name: "fuzzystrmatch", hint: "Levenshtein" },
  { name: "unaccent", hint: "Akzent-Suche" },
  { name: "isn", hint: "ISBN/ISSN" },
  { name: "ltree", hint: "Hierarchien" },
  { name: "tablefunc", hint: "crosstab()" },
  { name: "lo", hint: "Large Objects" },
  { name: "tcn", hint: "Trigger-Notify" },
];

interface Props {
  serverId: number;
}

interface PgTable {
  schema: string;
  name: string;
}

export function DatabaseManager({ serverId }: Props) {
  const { t } = useTranslation();
  const [resources, setResources] = useState<PostgresResources>({ databases: [], users: [] });
  const [selectedDbId, setSelectedDbId] = useState<number | null>(null);
  const [tables, setTables] = useState<PgTable[]>([]);
  const [selectedTable, setSelectedTable] = useState<PgTable | null>(null);
  const [rows, setRows] = useState<PostgresRowsResult | null>(null);
  const [credentials, setCredentials] = useState<PostgresCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [searchText, setSearchText] = useState("");
  const [sqlText, setSqlText] = useState("select now();");
  const [sqlResult, setSqlResult] = useState<PostgresRowsResult | null>(null);
  const [newDbName, setNewDbName] = useState("");
  const [newTable, setNewTable] = useState({
    name: "",
    columns: "id:integer:pk\nname:text:not_null",
  });
  const [extensions, setExtensions] = useState<PostgresExtension[]>([]);
  const [newExtension, setNewExtension] = useState("");

  const selectedDatabase = useMemo(
    () => resources.databases.find((db) => db.id === selectedDbId) || null,
    [resources.databases, selectedDbId],
  );

  const fetchResources = async () => {
    const data = await api<PostgresResources>(`/servers/${serverId}/databases`);
    setResources(data);
    setSelectedDbId((current) => current ?? data.databases[0]?.id ?? null);
  };

  const fetchTables = async (databaseId: number) => {
    const data = await api<{ tables: PgTable[] }>(`/servers/${serverId}/databases/tables/list`, {
      method: "POST",
      body: JSON.stringify({ database_id: databaseId }),
    });
    setTables(data.tables);
    setSelectedTable((current) => current ?? data.tables[0] ?? null);
  };

  const fetchExtensions = async (databaseId: number) => {
    const data = await api<PostgresExtension[]>(`/servers/${serverId}/databases/extensions/list`, {
      method: "POST",
      body: JSON.stringify({ database_id: databaseId }),
    });
    setExtensions(data);
  };

  useEffect(() => {
    setLoading(true);
    fetchResources()
      .catch((err) => toast.error(err.message || t("common.error")))
      .finally(() => setLoading(false));
  }, [serverId]);

  useEffect(() => {
    if (!selectedDbId) {
      setTables([]);
      setSelectedTable(null);
      setExtensions([]);
      return;
    }
    fetchTables(selectedDbId).catch((err) => toast.error(err.message || t("common.error")));
    fetchExtensions(selectedDbId).catch((err) => toast.error(err.message || t("common.error")));
  }, [selectedDbId]);

  const runBusy = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    try {
      await action();
    } catch (err: any) {
      toast.error(err.message || t("common.error"));
    } finally {
      setBusy(null);
    }
  };

  const bootstrap = () =>
    runBusy("bootstrap", async () => {
      const result = await api<{ credentials: PostgresCredential[] }>(`/servers/${serverId}/databases/bootstrap`, {
        method: "POST",
        body: JSON.stringify({ database_count: 1 }),
      });
      setCredentials(result.credentials);
      await fetchResources();
    });

  const createDatabase = () =>
    runBusy("create-db", async () => {
      const result = await api<{ credential: PostgresCredential }>(`/servers/${serverId}/databases`, {
        method: "POST",
        body: JSON.stringify({ name: newDbName.trim() || null }),
      });
      setNewDbName("");
      setCredentials([result.credential]);
      await fetchResources();
    });

  const createUser = () =>
    runBusy("create-user", async () => {
      if (!selectedDbId) return;
      const result = await api<{ credential: PostgresCredential }>(`/servers/${serverId}/databases/users`, {
        method: "POST",
        body: JSON.stringify({ database_id: selectedDbId, username: null }),
      });
      setCredentials([result.credential]);
      await fetchResources();
    });

  const rotatePassword = (userId: number) =>
    runBusy(`rotate-${userId}`, async () => {
      const result = await api<PostgresCredential>(`/servers/${serverId}/databases/users/${userId}/rotate`, {
        method: "POST",
      });
      setCredentials([{ ...result, database_name: selectedDatabase?.name || "" }]);
      await fetchResources();
    });

  const deleteUser = (userId: number, username: string) =>
    runBusy(`delete-user-${userId}`, async () => {
      const ok = await confirm({
        title: t("databases.deleteUser"),
        message: t("databases.deleteUserConfirm", { name: username }),
        confirmText: t("common.delete"),
        danger: true,
      });
      if (!ok) return;
      const typed = window.prompt(t("databases.typeNameConfirm", { name: username })) || "";
      if (typed !== username) return;
      await api(`/servers/${serverId}/databases/users/${userId}`, {
        method: "DELETE",
        body: JSON.stringify({ confirm_name: typed }),
      });
      await fetchResources();
    });

  const deleteDatabase = (database: PostgresDatabase) =>
    runBusy(`delete-db-${database.id}`, async () => {
      const ok = await confirm({
        title: t("databases.deleteDatabase"),
        message: t("databases.deleteDatabaseConfirm", { name: database.name }),
        confirmText: t("common.delete"),
        danger: true,
      });
      if (!ok) return;
      const typed = window.prompt(t("databases.typeNameConfirm", { name: database.name })) || "";
      if (typed !== database.name) return;
      await api(`/servers/${serverId}/databases/${database.id}`, {
        method: "DELETE",
        body: JSON.stringify({ confirm_name: typed }),
      });
      await fetchResources();
      setTables([]);
      setRows(null);
    });

  const loadRows = (table = selectedTable) => {
    if (!selectedDbId || !table) return;
    void runBusy("rows", async () => {
      const result = await api<PostgresRowsResult>(`/servers/${serverId}/databases/rows`, {
        method: "POST",
        body: JSON.stringify({
          database_id: selectedDbId,
          schema_name: table.schema,
          table_name: table.name,
          search: searchText || null,
          limit: 500,
          offset: 0,
        }),
      });
      setRows(result);
    });
  };

  const createTable = () =>
    runBusy("create-table", async () => {
      if (!selectedDbId) return;
      const columns = newTable.columns
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const [name, type, ...flags] = line.split(":").map((part) => part.trim());
          return { name, type, primary_key: flags.includes("pk"), not_null: flags.includes("not_null") };
        });
      await api(`/servers/${serverId}/databases/tables`, {
        method: "POST",
        body: JSON.stringify({
          database_id: selectedDbId,
          schema_name: "public",
          table_name: newTable.name,
          columns,
        }),
      });
      setNewTable({ ...newTable, name: "" });
      await fetchTables(selectedDbId);
    });

  const installExtension = () =>
    runBusy("install-extension", async () => {
      if (!selectedDbId || !newExtension.trim()) return;
      const updated = await api<PostgresExtension[]>(`/servers/${serverId}/databases/extensions`, {
        method: "POST",
        body: JSON.stringify({ database_id: selectedDbId, name: newExtension.trim() }),
      });
      setExtensions(updated);
      setNewExtension("");
      toast.success(t("databases.extensionInstalled", { name: newExtension.trim() }));
    });

  const dropExtension = (name: string) =>
    runBusy(`drop-extension-${name}`, async () => {
      if (!selectedDbId) return;
      const typed = window.prompt(t("databases.typeNameConfirm", { name })) || "";
      if (typed !== name) return;
      const updated = await api<PostgresExtension[]>(`/servers/${serverId}/databases/extensions/${name}`, {
        method: "DELETE",
        body: JSON.stringify({ database_id: selectedDbId, confirm_name: typed }),
      });
      setExtensions(updated);
    });

  const runSql = () =>
    runBusy("sql", async () => {
      if (!selectedDbId) return;
      const result = await api<PostgresRowsResult>(`/servers/${serverId}/databases/sql`, {
        method: "POST",
        body: JSON.stringify({ database_id: selectedDbId, sql: sqlText, limit: 500 }),
      });
      setSqlResult(result);
    });

  if (loading) {
    return <div className="msm-card p-6 text-on-surface-variant">{t("common.loading")}</div>;
  }

  return (
    <div className="space-y-4">
      {resources.databases.length === 0 ? (
        <div className="msm-card p-8 text-center border-dashed border-2 border-outline-variant">
          <Database className="w-10 h-10 text-secondary mx-auto mb-3" />
          <h3 className="font-headline text-body-lg text-on-surface">{t("databases.emptyTitle")}</h3>
          <p className="font-body-md text-sm text-on-surface-variant mt-1 mb-4">{t("databases.emptyHint")}</p>
          <button className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2" onClick={bootstrap} disabled={busy === "bootstrap"}>
            <Plus className="w-4 h-4" />
            {t("databases.bootstrap")}
          </button>
        </div>
      ) : (
        <>
          <section className="msm-card p-5">
            <div className="flex flex-col lg:flex-row lg:items-end gap-4 justify-between">
              <div>
                <h2 className="font-headline text-body-lg text-primary">{t("tabs.databases")}</h2>
                <p className="font-body-md text-sm text-on-surface-variant">{t("databases.subtitle")}</p>
              </div>
              <div className="flex flex-col sm:flex-row gap-2">
                <input className="msm-input" value={newDbName} onChange={(e) => setNewDbName(e.target.value)} placeholder={t("databases.newDatabasePlaceholder")} />
                <button className="msm-btn-primary inline-flex items-center gap-2 px-3 py-2" onClick={createDatabase} disabled={busy === "create-db"}>
                  <Plus className="w-4 h-4" />
                  {t("databases.createDatabase")}
                </button>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="space-y-2">
                {resources.databases.map((database) => (
                  <button
                    key={database.id}
                    type="button"
                    className={`w-full flex items-center justify-between rounded-lg border px-3 py-2 text-left ${selectedDbId === database.id ? "border-secondary bg-secondary/10" : "border-outline-variant bg-surface-container"}`}
                    onClick={() => {
                      setSelectedDbId(database.id);
                      setSelectedTable(null);
                      setRows(null);
                    }}
                  >
                    <span className="font-mono text-sm text-on-surface">{database.name}</span>
                    <Trash2 className="w-4 h-4 text-status-error" onClick={(event) => { event.stopPropagation(); void deleteDatabase(database); }} />
                  </button>
                ))}
              </div>
              <div className="space-y-2">
                <button
                  className="msm-btn-secondary mb-2 inline-flex items-center gap-2 px-3 py-2"
                  onClick={createUser}
                  disabled={!selectedDbId || busy === "create-user"}
                >
                  <Plus className="w-4 h-4" />
                  {t("databases.createUser")}
                </button>
                {resources.users.map((user) => (
                  <div key={user.id} className="flex items-center justify-between rounded-lg border border-outline-variant bg-surface-container px-3 py-2">
                    <div>
                      <div className="font-mono text-sm text-on-surface">{user.username}</div>
                      <div className="font-mono text-xs text-on-surface-variant">{user.password_mask}</div>
                    </div>
                    <div className="flex gap-2">
                      <button className="msm-btn-secondary inline-flex items-center gap-2 px-3 py-2" onClick={() => rotatePassword(user.id)}>
                        <RefreshCw className="w-4 h-4" />
                        {t("databases.rotate")}
                      </button>
                      <button className="msm-btn-secondary px-3 py-2 text-status-error" onClick={() => deleteUser(user.id, user.username)}>
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="grid grid-cols-1 xl:grid-cols-[320px_1fr] gap-4">
            <div className="msm-card p-5 space-y-4">
              <h3 className="font-headline text-body-md text-on-surface">{t("databases.tables")}</h3>
              <div className="space-y-2">
                {tables.map((table) => (
                  <button
                    key={`${table.schema}.${table.name}`}
                    className={`w-full rounded-lg border px-3 py-2 text-left font-mono text-sm ${selectedTable?.name === table.name ? "border-secondary bg-secondary/10 text-on-surface" : "border-outline-variant bg-surface-container text-on-surface-variant"}`}
                    onClick={() => { setSelectedTable(table); loadRows(table); }}
                  >
                    {table.schema}.{table.name}
                  </button>
                ))}
                {tables.length === 0 && <p className="text-sm text-on-surface-variant">{t("databases.noTables")}</p>}
              </div>
              <div className="space-y-2 pt-2 border-t border-outline-variant">
                <input className="msm-input" value={newTable.name} onChange={(e) => setNewTable({ ...newTable, name: e.target.value })} placeholder={t("databases.tableName")} />
                <textarea className="msm-input min-h-28 font-mono text-sm" value={newTable.columns} onChange={(e) => setNewTable({ ...newTable, columns: e.target.value })} />
                <button className="msm-btn-secondary w-full inline-flex items-center justify-center gap-2 py-2" onClick={createTable}>
                  <Plus className="w-4 h-4" />
                  {t("databases.createTable")}
                </button>
              </div>

              <div className="space-y-2 pt-2 border-t border-outline-variant">
                <h4 className="font-headline text-body-sm text-on-surface flex items-center gap-2">
                  <Package className="w-4 h-4" />
                  {t("databases.extensions")}
                </h4>
                {extensions.length === 0 ? (
                  <p className="text-sm text-on-surface-variant">{t("databases.noExtensions")}</p>
                ) : (
                  <div className="space-y-1">
                    {extensions.map((ext) => (
                      <div
                        key={ext.name}
                        className="flex items-center justify-between rounded-lg border border-outline-variant bg-surface-container px-3 py-2"
                      >
                        <div>
                          <div className="font-mono text-sm text-on-surface">{ext.name}</div>
                          {ext.version && (
                            <div className="font-mono text-xs text-on-surface-variant">v{ext.version}</div>
                          )}
                        </div>
                        <button
                          className="msm-btn-secondary px-2 py-1 text-status-error"
                          onClick={() => dropExtension(ext.name)}
                          disabled={busy === `drop-extension-${ext.name}`}
                          title={t("databases.dropExtensionHint", { name: ext.name })}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <select
                  className="msm-input"
                  value={newExtension}
                  onChange={(e) => setNewExtension(e.target.value)}
                  disabled={!selectedDbId || busy === "install-extension"}
                >
                  <option value="">{t("databases.selectExtension")}</option>
                  {AVAILABLE_EXTENSIONS.filter((ext) => !extensions.some((installed) => installed.name === ext.name)).map(
                    (ext) => (
                      <option key={ext.name} value={ext.name}>
                        {ext.name} — {ext.hint}
                      </option>
                    ),
                  )}
                </select>
                <button
                  className="msm-btn-secondary w-full inline-flex items-center justify-center gap-2 py-2"
                  onClick={installExtension}
                  disabled={!selectedDbId || !newExtension.trim() || busy === "install-extension"}
                >
                  <Plus className="w-4 h-4" />
                  {t("databases.installExtension")}
                </button>
              </div>
            </div>

            <div className="space-y-4">
              <div className="msm-card p-5">
                <div className="flex flex-col md:flex-row gap-3 md:items-center md:justify-between mb-4">
                  <h3 className="font-headline text-body-md text-on-surface">{selectedTable ? `${selectedTable.schema}.${selectedTable.name}` : t("databases.rows")}</h3>
                  <div className="flex gap-2">
                    <input className="msm-input" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder={t("databases.searchRows")} />
                    <button className="msm-btn-secondary px-3 inline-flex items-center gap-2" onClick={() => loadRows()}>
                      <Search className="w-4 h-4" />
                      {t("common.search")}
                    </button>
                  </div>
                </div>
                <ResultTable result={rows} emptyText={t("databases.selectTable")} />
              </div>

              <div className="msm-card p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-headline text-body-md text-on-surface">{t("databases.sqlConsole")}</h3>
                  <span className="rounded-full border border-status-warning/30 bg-status-warning/10 px-2 py-0.5 text-xs text-status-warning">5s / 500</span>
                </div>
                <textarea className="msm-input min-h-32 font-mono text-sm" value={sqlText} onChange={(e) => setSqlText(e.target.value)} />
                <button className="msm-btn-primary mt-3 inline-flex items-center gap-2 px-4 py-2" onClick={runSql} disabled={!selectedDbId || busy === "sql"}>
                  <Play className="w-4 h-4" />
                  {t("databases.runSql")}
                </button>
                <div className="mt-4">
                  <ResultTable result={sqlResult} emptyText={t("databases.noSqlResult")} />
                </div>
              </div>
            </div>
          </section>
        </>
      )}
      <PostgresCredentialsDialog credentials={credentials} onClose={() => setCredentials([])} />
    </div>
  );
}

function ResultTable({ result, emptyText }: { result: PostgresRowsResult | null; emptyText: string }) {
  if (!result) return <p className="text-sm text-on-surface-variant">{emptyText}</p>;
  if (!result.columns.length) {
    return <p className="font-mono text-sm text-on-surface-variant">{result.status || emptyText}</p>;
  }
  return (
    <div className="overflow-auto rounded-lg border border-outline-variant">
      <table className="min-w-full text-sm">
        <thead className="bg-surface-container-highest text-on-surface">
          <tr>
            {result.columns.map((column) => (
              <th key={column} className="px-3 py-2 text-left font-mono font-medium">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant">
          {result.rows.map((row, index) => (
            <tr key={index} className="bg-surface-container text-on-surface-variant">
              {result.columns.map((column) => (
                <td key={column} className="px-3 py-2 font-mono align-top">{String(row[column] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
