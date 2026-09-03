/**
 * dis-secure-storage / secure-memory — memory-safe handling of key material.
 *
 * Ported from Singra Vault's SecureBuffer. Provides controlled, callback-scoped
 * access to sensitive bytes with explicit zeroing on destroy and a
 * FinalizationRegistry fallback. This is defense-in-depth: JavaScript cannot
 * guarantee memory wiping (GC is non-deterministic, strings are immutable), but
 * controlled access plus best-effort zeroing materially reduces exposure
 * (cf. KeePass CVE-2023-32784).
 */
/** A wrapper for sensitive binary data with controlled access and zeroing. */
declare class SecureBuffer {
    private buffer;
    private destroyed;
    constructor(size: number);
    /** Copies `bytes` into a new SecureBuffer. The source is NOT auto-zeroed. */
    static fromBytes(bytes: Uint8Array): SecureBuffer;
    /** Builds a SecureBuffer from a hex string (spaces/dashes allowed). */
    static fromHex(hex: string): SecureBuffer;
    /** Allocates a SecureBuffer filled with CSPRNG bytes. */
    static random(size: number): SecureBuffer;
    /** Synchronous controlled access. Do not retain the buffer past `fn`. */
    use<T>(fn: (data: Uint8Array) => T): T;
    /** Asynchronous controlled access. */
    useAsync<T>(fn: (data: Uint8Array) => Promise<T>): Promise<T>;
    get size(): number;
    get isDestroyed(): boolean;
    /** Zeros the buffer and marks it destroyed. Idempotent. */
    destroy(): void;
    /** Returns a mutable copy of the contents (caller must zero it). */
    toBytes(): Uint8Array;
    /** Constant-time equality comparison against another buffer. */
    equals(other: SecureBuffer | Uint8Array): boolean;
}
/** Runs `fn` with a temporary SecureBuffer that is always destroyed afterwards. */
declare function withSecureBuffer<T>(bytes: Uint8Array, fn: (secure: SecureBuffer) => Promise<T>): Promise<T>;
/** Zeros multiple buffers. Convenience for `finally` cleanup blocks. */
declare function zeroBuffers(...buffers: (Uint8Array | null | undefined)[]): void;

export { SecureBuffer, withSecureBuffer, zeroBuffers };
