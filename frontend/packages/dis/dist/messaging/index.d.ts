/**
 * dis-messaging — types for the Signal Double Ratchet state machine.
 *
 * Every type here is a plain, JSON-representable data holder. The ratchet is a
 * pure state transition: the caller owns the state, persists it, and decides
 * when to destroy it. Nothing in this module reads or writes storage.
 *
 * Binary fields are `Uint8Array` in memory and base64 on the wire (see
 * `serializeRatchetState`). Key material held here is long-lived and therefore
 * caller-owned — the same convention as `deriveRawKey`, which documents that
 * the caller must wipe the buffer it receives. Use `destroyRatchetState()` on
 * a state that has been superseded.
 */
/** Wire prefix of a serialised {@link RatchetMessage}. Format-frozen. */
declare const RATCHET_MESSAGE_V1_PREFIX = "sv-dr-msg-v1:";
/** Wire prefix of a serialised {@link RatchetState}. Format-frozen. */
declare const RATCHET_STATE_V1_PREFIX = "sv-dr-state-v1:";
/** Version tag carried inside every message header. Format-frozen. */
declare const RATCHET_MESSAGE_V1_TAG = "sv-dr-msg-v1";
/**
 * Default cap on retained skipped message keys. Bounds memory against a peer
 * that never delivers the messages it announces, and (reused as a per-step
 * bound) bounds CPU against a header claiming an absurd message number.
 */
declare const DEFAULT_MAX_SKIPPED_KEYS = 1000;
/**
 * The DH algorithm a state was created with.
 *
 * Only `ECDH-P-256` exists in v1. It is recorded in the state so a future
 * `sv-dr-state-v2` can introduce X25519 without ambiguity, and so a v1 state
 * can never be silently reinterpreted under a different curve.
 */
type RatchetDhAlgorithm = 'ECDH-P-256';
/** A ratchet DH key pair, serialised in the exact form the state stores. */
interface RatchetDhKeyPair {
    readonly algorithm: RatchetDhAlgorithm;
    /** Raw (uncompressed point) public key — {@link DH_PUBLIC_KEY_LENGTH} bytes. */
    readonly publicKey: Uint8Array;
    /** PKCS#8 private key — {@link DH_PRIVATE_KEY_LENGTH} bytes. Secret. */
    readonly privateKey: Uint8Array;
}
/**
 * Public, authenticated message header.
 *
 * Every field is bound into the AEAD's associated data, so tampering with any
 * of them makes decryption fail with {@link DisDecryptionError}.
 */
interface RatchetHeader {
    readonly v: typeof RATCHET_MESSAGE_V1_TAG;
    /** base64 of the sender's current ratchet public key (raw, 65 bytes). */
    readonly dh: string;
    /** Number of messages in the sender's previous sending chain. */
    readonly pn: number;
    /** Message number within the sender's current sending chain. */
    readonly n: number;
}
/** One sealed message: authenticated header plus base64 `ciphertext || tag`. */
interface RatchetMessage {
    readonly header: RatchetHeader;
    /**
     * base64 of `ciphertext || authTag`. There is no IV prefix — the nonce is
     * derived deterministically from the single-use message key.
     */
    readonly ciphertext: string;
}
/**
 * A message key retained for a message that was announced but not yet
 * delivered. Consuming it removes it from the state irrevocably.
 */
interface SkippedMessageKey {
    /** base64 public key identifying the chain this key belongs to. */
    readonly dh: string;
    /** Message number within that chain. */
    readonly n: number;
    /** The message key — {@link RATCHET_KEY_LENGTH} bytes. Secret. */
    readonly messageKey: Uint8Array;
}
/**
 * The complete Double Ratchet session state.
 *
 * Fully serialisable and never mutated by `encryptMessage`/`decryptMessage` —
 * both return a fresh `nextState` and leave their input untouched. Persist the
 * returned state, then call `destroyRatchetState()` on the one it replaced.
 */
interface RatchetState {
    readonly version: 1;
    /** This party's current ratchet key pair (DHs). */
    readonly dhSelf: RatchetDhKeyPair;
    /** The peer's current ratchet public key (DHr); `null` until first receive. */
    readonly dhRemote: Uint8Array | null;
    /** Root key (RK) — {@link RATCHET_KEY_LENGTH} bytes. Secret. */
    readonly rootKey: Uint8Array;
    /** Sending chain key (CKs); `null` before the first DH ratchet. Secret. */
    readonly sendingChainKey: Uint8Array | null;
    /** Receiving chain key (CKr); `null` before the first DH ratchet. Secret. */
    readonly receivingChainKey: Uint8Array | null;
    /** Messages sent in the current sending chain (Ns). */
    readonly sendCount: number;
    /** Messages received in the current receiving chain (Nr). */
    readonly receiveCount: number;
    /** Length of the previous sending chain (PN). */
    readonly previousSendCount: number;
    /** Retained out-of-order message keys, oldest first (FIFO eviction). */
    readonly skippedMessageKeys: readonly SkippedMessageKey[];
    /** Cap on retained skipped keys, and on skips performed per step. */
    readonly maxSkippedKeys: number;
    /**
     * Optional session binding mixed into every message's AAD — e.g. the
     * identity material from an X3DH handshake. Establishing it is out of
     * scope for DIS; it is treated here as opaque bytes.
     */
    readonly associatedData: Uint8Array | null;
}
/** Result of {@link encryptMessage}. */
interface EncryptMessageResult {
    readonly nextState: RatchetState;
    readonly message: RatchetMessage;
}
/** Result of {@link decryptMessage}. The plaintext is secret — wipe it. */
interface DecryptMessageResult {
    readonly nextState: RatchetState;
    readonly plaintext: Uint8Array;
}
/** Input for {@link initSenderState} (the party that speaks first). */
interface InitSenderStateInput {
    /** Shared secret from the handshake — {@link RATCHET_KEY_LENGTH} bytes. */
    readonly sharedSecret: Uint8Array;
    /** The peer's published ratchet public key (raw, 65 bytes). */
    readonly remotePublicKey: Uint8Array;
    readonly associatedData?: Uint8Array;
    readonly maxSkippedKeys?: number;
}
/** Input for {@link initReceiverState} (the party that published a key pair). */
interface InitReceiverStateInput {
    /** Shared secret from the handshake — {@link RATCHET_KEY_LENGTH} bytes. */
    readonly sharedSecret: Uint8Array;
    /** The key pair whose public half the sender used. */
    readonly dhKeyPair: RatchetDhKeyPair;
    readonly associatedData?: Uint8Array;
    readonly maxSkippedKeys?: number;
}

/**
 * dis-messaging — the Signal Double Ratchet as a pure state machine.
 *
 * Model:
 *   - A **symmetric ratchet** advances a chain key with HKDF-SHA-256 for every
 *     message, yielding a single-use message key that is destroyed after use.
 *     This gives forward secrecy: a key recovered today opens nothing sent
 *     before it.
 *   - A **DH ratchet** mixes a fresh ECDH P-256 agreement into the root key
 *     whenever the conversation changes direction. This gives post-compromise
 *     security: one uncompromised round trip heals the session.
 *   - Each message is sealed with AES-256-GCM under a key and nonce derived
 *     from its message key, with the header bound in as associated data.
 *
 * DIS does not invent any of this — it is RFC 5869 HKDF, NIST P-256 ECDH and
 * AES-256-GCM, composed as specified by the Double Ratchet algorithm.
 *
 * Purity contract: `encryptMessage` and `decryptMessage` never mutate, wipe or
 * retain their input state. They return a fresh `nextState`; the caller
 * persists it and then calls {@link destroyRatchetState} on the state it
 * replaced. Decryption in particular works on a private clone and commits only
 * after the AEAD tag verifies, so a forged message can never advance — or
 * wedge — a live session.
 *
 * Key exchange (X3DH or any other handshake) is explicitly out of scope: the
 * initial `sharedSecret` is supplied by the caller.
 */

/**
 * Generates a fresh ratchet key pair, exported to the byte form the state
 * stores. The private key is generated extractable because a Double Ratchet
 * state must be persistable across restarts — it is exported once here and
 * from then on only ever held as bytes the caller can wipe.
 */
declare function generateRatchetKeyPair(): Promise<RatchetDhKeyPair>;
/**
 * Initialises the party that sends first (Alice).
 *
 * She already holds the peer's published ratchet public key, so she generates
 * her own pair and performs the first DH ratchet immediately — her sending
 * chain is ready before the peer has said anything.
 */
declare function initSenderState(input: InitSenderStateInput): Promise<RatchetState>;
/**
 * Initialises the party whose ratchet key was published (Bob).
 *
 * He has no chains yet: the shared secret is his root key, and his first DH
 * ratchet happens when the first message arrives. He therefore cannot send
 * until he has received.
 */
declare function initReceiverState(input: InitReceiverStateInput): Promise<RatchetState>;
/**
 * Encrypts one message, advancing the sending chain.
 *
 * Returns a fresh state; `state` itself is left untouched. Throws
 * {@link DisInvalidArgumentError} if this party has no sending chain yet — the
 * receiving side must decrypt its first inbound message before it can reply.
 */
declare function encryptMessage(state: RatchetState, plaintext: Uint8Array): Promise<EncryptMessageResult>;
/**
 * Decrypts one message, advancing (or ratcheting) the receiving side.
 *
 * All work happens on a private clone which is only returned once the GCM tag
 * has verified — a forged or replayed message leaves `state` byte-for-byte
 * unchanged, so it can neither advance the ratchet nor wedge the session.
 * Every failure mode collapses to {@link DisDecryptionError}.
 */
declare function decryptMessage(state: RatchetState, message: RatchetMessage): Promise<DecryptMessageResult>;
/**
 * Zeroes every secret a state holds. Call this on a state that has been
 * replaced by a `nextState`, once the replacement is safely persisted —
 * keeping the old one alive keeps its chain keys alive, which is exactly the
 * forward secrecy the ratchet exists to provide.
 *
 * Idempotent and safe on an already-destroyed state.
 */
declare function destroyRatchetState(state: RatchetState): void;
/** Serialises a state to `sv-dr-state-v1:` + JSON with base64 binary fields. */
declare function serializeRatchetState(state: RatchetState): string;
/**
 * Parses a serialised state.
 *
 * Fails closed on anything unexpected: a future in-family version raises
 * {@link DisUnsupportedFormatVersionError} via `parseEnvelope`, a foreign
 * payload or any structurally invalid field raises
 * {@link DisInvalidArgumentError}. Byte lengths are checked so a malformed
 * state can never reach a crypto primitive.
 */
declare function deserializeRatchetState(json: string): RatchetState;
/** Serialises a message to `sv-dr-msg-v1:` + JSON for transport. */
declare function serializeRatchetMessage(message: RatchetMessage): string;
/**
 * Parses a transported message. This only restores structure — authenticity is
 * decided by {@link decryptMessage}, which is the single place that can tell a
 * genuine message from a forged one.
 */
declare function deserializeRatchetMessage(wire: string): RatchetMessage;

export { DEFAULT_MAX_SKIPPED_KEYS, type DecryptMessageResult, type EncryptMessageResult, type InitReceiverStateInput, type InitSenderStateInput, RATCHET_MESSAGE_V1_PREFIX, RATCHET_STATE_V1_PREFIX, type RatchetDhAlgorithm, type RatchetDhKeyPair, type RatchetHeader, type RatchetMessage, type RatchetState, type SkippedMessageKey, decryptMessage, deserializeRatchetMessage, deserializeRatchetState, destroyRatchetState, encryptMessage, generateRatchetKeyPair, initReceiverState, initSenderState, serializeRatchetMessage, serializeRatchetState };
