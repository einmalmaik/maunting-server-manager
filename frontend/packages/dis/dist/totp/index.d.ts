/**
 * dis-totp — time-based one-time passwords (RFC 6238).
 *
 * Thin wrapper over the audited `otpauth` library so applications never embed
 * the OTP primitive directly. Parameters are pinned to the values Singra Vault
 * uses (SHA-1, 6 digits, 30-second period, 160-bit secrets) so existing
 * enrolled authenticators keep working unchanged.
 *
 * SHA-1 here is the standardised HMAC inside the TOTP construction (RFC 6238),
 * which every authenticator app implements; it is not used as a hash for any
 * security decision elsewhere.
 */
/** TOTP parameters, fixed to remain compatible with enrolled authenticators. */
declare const TOTP_ALGORITHM: "SHA1";
declare const TOTP_DIGITS = 6;
declare const TOTP_PERIOD_SECONDS = 30;
/** Secret size in bytes (160-bit) as produced by {@link generateTotpSecret}. */
declare const TOTP_SECRET_SIZE = 20;
/** HMAC algorithms RFC 6238 permits and authenticator apps implement. */
type TotpAlgorithm = 'SHA1' | 'SHA256' | 'SHA512';
/**
 * Parameters for third-party TOTP entries (password-manager authenticator
 * storage). Unlike Singra's own 2FA enrolment — which is pinned to
 * SHA-1 / 6 digits / 30 s — imported entries carry whatever parameters the
 * issuing service chose, so each knob is explicit here.
 */
interface TotpCodeOptions {
    /** HMAC algorithm. Defaults to {@link TOTP_ALGORITHM}. */
    readonly algorithm?: TotpAlgorithm;
    /** Code length. Defaults to {@link TOTP_DIGITS}. */
    readonly digits?: number;
    /** Time step in seconds. Defaults to {@link TOTP_PERIOD_SECONDS}. */
    readonly period?: number;
    /** Epoch milliseconds to generate for. Defaults to the current time. */
    readonly timestamp?: number;
}
interface TotpParams {
    /** Issuer label shown in the authenticator app. */
    readonly issuer: string;
    /** Account label (typically the user's email). */
    readonly label: string;
    /** Base32-encoded shared secret. */
    readonly secret: string;
}
/** Generates a new base32-encoded 160-bit TOTP secret. */
declare function generateTotpSecret(): string;
/** Builds the `otpauth://` provisioning URI for a QR code. */
declare function buildTotpUri(params: TotpParams): string;
/**
 * Generates the TOTP code for a base32 `secret` with explicit parameters —
 * the primitive behind a password manager's authenticator view. Throws
 * {@link DisInvalidArgumentError} on a malformed secret so callers can decide
 * how to surface the error.
 */
declare function generateTotpCode(secret: string, options?: TotpCodeOptions): string;
/**
 * Builds the `otpauth://` provisioning URI for an entry with explicit
 * parameters (third-party authenticator entries). Singra's own 2FA enrolment
 * should keep using {@link buildTotpUri}, which pins the Singra parameter set.
 */
declare function buildTotpUriWithOptions(params: TotpParams, options?: Omit<TotpCodeOptions, 'timestamp'>): string;
/**
 * Verifies a TOTP `code` against a base32 `secret`, allowing `window` periods
 * of clock drift on either side (default 1 = ±30s). Returns `true` on a match.
 * Malformed secrets/codes return `false` rather than throwing.
 */
declare function verifyTotpCode(secret: string, code: string, window?: number): boolean;

export { TOTP_ALGORITHM, TOTP_DIGITS, TOTP_PERIOD_SECONDS, TOTP_SECRET_SIZE, type TotpAlgorithm, type TotpCodeOptions, type TotpParams, buildTotpUri, buildTotpUriWithOptions, generateTotpCode, generateTotpSecret, verifyTotpCode };
