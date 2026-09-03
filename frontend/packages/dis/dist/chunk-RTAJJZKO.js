import { getCryptoProvider } from './chunk-CYIGDF63.js';
import { DisInvalidArgumentError, DisError } from './chunk-MJO7IJZC.js';

// src/secure-memory/index.ts
var cleanupRegistry = new FinalizationRegistry((buffer) => {
  try {
    buffer.fill(0);
  } catch {
  }
});
function assertLive(destroyed) {
  if (destroyed) {
    throw new DisError("USE_AFTER_DESTROY", "SecureBuffer has been destroyed");
  }
}
var SecureBuffer = class _SecureBuffer {
  buffer;
  destroyed = false;
  constructor(size) {
    if (size <= 0 || !Number.isInteger(size)) {
      throw new DisInvalidArgumentError("SecureBuffer size must be a positive integer");
    }
    this.buffer = new Uint8Array(size);
    cleanupRegistry.register(this, this.buffer, this);
  }
  /** Copies `bytes` into a new SecureBuffer. The source is NOT auto-zeroed. */
  static fromBytes(bytes) {
    const secure = new _SecureBuffer(bytes.length || 1);
    if (bytes.length === 0) {
      secure.destroy();
      throw new DisInvalidArgumentError("Cannot create SecureBuffer from empty bytes");
    }
    secure.buffer.set(bytes);
    return secure;
  }
  /** Builds a SecureBuffer from a hex string (spaces/dashes allowed). */
  static fromHex(hex) {
    const cleanHex = hex.replace(/[\s-]/g, "");
    if (cleanHex.length === 0 || cleanHex.length % 2 !== 0) {
      throw new DisInvalidArgumentError("Hex string must have a positive even length");
    }
    const secure = new _SecureBuffer(cleanHex.length / 2);
    for (let i = 0; i < secure.buffer.length; i++) {
      const value = parseInt(cleanHex.substr(i * 2, 2), 16);
      if (Number.isNaN(value)) {
        secure.destroy();
        throw new DisInvalidArgumentError(`Invalid hex byte at position ${i * 2}`);
      }
      secure.buffer[i] = value;
    }
    return secure;
  }
  /** Allocates a SecureBuffer filled with CSPRNG bytes. */
  static random(size) {
    const secure = new _SecureBuffer(size);
    getCryptoProvider().getRandomValues(secure.buffer);
    return secure;
  }
  /** Synchronous controlled access. Do not retain the buffer past `fn`. */
  use(fn) {
    assertLive(this.destroyed);
    return fn(this.buffer);
  }
  /** Asynchronous controlled access. */
  async useAsync(fn) {
    assertLive(this.destroyed);
    return fn(this.buffer);
  }
  get size() {
    assertLive(this.destroyed);
    return this.buffer.length;
  }
  get isDestroyed() {
    return this.destroyed;
  }
  /** Zeros the buffer and marks it destroyed. Idempotent. */
  destroy() {
    if (this.destroyed) return;
    this.buffer.fill(0);
    cleanupRegistry.unregister(this);
    this.destroyed = true;
  }
  /** Returns a mutable copy of the contents (caller must zero it). */
  toBytes() {
    assertLive(this.destroyed);
    return new Uint8Array(this.buffer);
  }
  /** Constant-time equality comparison against another buffer. */
  equals(other) {
    assertLive(this.destroyed);
    const otherBytes = other instanceof _SecureBuffer ? other.buffer : other;
    if (this.buffer.length !== otherBytes.length) return false;
    let result = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      result |= this.buffer[i] ^ otherBytes[i];
    }
    return result === 0;
  }
};
async function withSecureBuffer(bytes, fn) {
  const secure = SecureBuffer.fromBytes(bytes);
  try {
    return await fn(secure);
  } finally {
    secure.destroy();
  }
}
function zeroBuffers(...buffers) {
  for (const buffer of buffers) {
    buffer?.fill(0);
  }
}

export { SecureBuffer, withSecureBuffer, zeroBuffers };
//# sourceMappingURL=chunk-RTAJJZKO.js.map
//# sourceMappingURL=chunk-RTAJJZKO.js.map