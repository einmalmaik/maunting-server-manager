/**
 * dis-random — secure random generation.
 *
 * Always sources entropy from the active crypto provider's CSPRNG
 * (`getRandomValues`). DIS never uses `Math.random` for any value that affects
 * confidentiality, integrity, or unpredictability.
 */
/** Returns `length` cryptographically secure random bytes. */
declare function randomBytes(length: number): Uint8Array;
/**
 * Fills an existing `ArrayBufferView` with cryptographically secure random
 * bytes in place. Used by callers that own a buffer they want to overwrite
 * (e.g. secure-memory scrubbing) without allocating a second array.
 */
declare function fillRandom<T extends ArrayBufferView>(view: T): T;
/**
 * Returns a cryptographically secure random integer in the inclusive range
 * `[min, max]` using rejection sampling to avoid modulo bias.
 *
 * Byte-for-byte equivalent to the `getSecureRandomInt` helpers in Singra Vault
 * (password generator, backup-code generator): it draws `ceil(log2(range)/8)`
 * bytes big-endian and rejects values above the largest multiple of `range`.
 */
declare function randomInt(min: number, max: number): number;
/** Returns a RFC 4122 v4 UUID using the provider's CSPRNG. */
declare function randomUuid(): string;

export { fillRandom, randomBytes, randomInt, randomUuid };
