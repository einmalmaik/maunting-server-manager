import { DisInvalidArgumentError, DisError } from './chunk-MJO7IJZC.js';

// src/migrations/index.ts
var MigrationRegistry = class {
  migrations = /* @__PURE__ */ new Map();
  keyOf(subject, fromVersion) {
    return `${subject}@${fromVersion}`;
  }
  /** Registers a migration. Throws if one already exists for the same step. */
  register(migration) {
    const key = this.keyOf(migration.subject, migration.fromVersion);
    if (this.migrations.has(key)) {
      throw new DisInvalidArgumentError(`Duplicate migration for ${key}`);
    }
    this.migrations.set(key, migration);
    return this;
  }
  /**
   * Applies all applicable migrations in sequence until no further migration
   * exists for the payload's detected version. Guards against cycles.
   */
  async migrateToLatest(subject, payload, detect, context) {
    let current = payload;
    const seen = /* @__PURE__ */ new Set();
    for (; ; ) {
      const version = detect(current);
      if (seen.has(version)) {
        throw new DisError(
          "INVALID_ARGUMENT",
          `Migration cycle detected for ${subject}@${version}`
        );
      }
      seen.add(version);
      const migration = this.migrations.get(this.keyOf(subject, version));
      if (!migration) return current;
      current = await migration.migrate(current, context);
    }
  }
};

export { MigrationRegistry };
//# sourceMappingURL=chunk-FUDDBD2G.js.map
//# sourceMappingURL=chunk-FUDDBD2G.js.map