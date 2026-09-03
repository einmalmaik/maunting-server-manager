/**
 * dis-migrations — explicit, ordered transformation of encrypted payloads.
 *
 * Migrations are registered against a (subject, fromVersion) key and run in a
 * deterministic order. This gives applications a single, testable place to
 * evolve formats (e.g. re-wrap legacy no-AAD vault items, bump KDF parameters,
 * re-encrypt under a new cipher) without scattering ad-hoc upgrade code.
 *
 * DIS provides the framework and the crypto; the application decides which
 * migrations to register and how persistence happens.
 */
/** Context passed to a migration step. `key` material is caller-supplied. */
interface MigrationContext {
    readonly key: CryptoKey;
    /** Stable identifier the payload is bound to (e.g. entry id). */
    readonly bindingId?: string;
}
/** A single migration: transforms a payload from `fromVersion` to `toVersion`. */
interface Migration {
    readonly subject: string;
    readonly fromVersion: number | 'legacy';
    readonly toVersion: number;
    /** Returns the migrated payload string. Must be idempotent on its output. */
    migrate(payload: string, context: MigrationContext): Promise<string>;
}
/** Detects the current version of a payload for a subject. */
type VersionDetector = (payload: string) => number | 'legacy';
/** An ordered registry of migrations for one or more subjects. */
declare class MigrationRegistry {
    private readonly migrations;
    private keyOf;
    /** Registers a migration. Throws if one already exists for the same step. */
    register(migration: Migration): this;
    /**
     * Applies all applicable migrations in sequence until no further migration
     * exists for the payload's detected version. Guards against cycles.
     */
    migrateToLatest(subject: string, payload: string, detect: VersionDetector, context: MigrationContext): Promise<string>;
}

export { type Migration, type MigrationContext, MigrationRegistry, type VersionDetector };
