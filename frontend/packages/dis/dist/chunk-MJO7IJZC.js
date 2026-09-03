// src/core/errors.ts
var DisError = class extends Error {
  code;
  constructor(code, message) {
    super(message);
    this.name = "DisError";
    this.code = code;
    Object.setPrototypeOf(this, new.target.prototype);
  }
};
var DisInvalidArgumentError = class extends DisError {
  constructor(message) {
    super("INVALID_ARGUMENT", message);
    this.name = "DisInvalidArgumentError";
  }
};
var DisDecryptionError = class extends DisError {
  constructor(message = "Decryption failed") {
    super("DECRYPTION_FAILED", message);
    this.name = "DisDecryptionError";
  }
};
var DisUnsupportedFormatVersionError = class extends DisError {
  constructor(message) {
    super("UNSUPPORTED_FORMAT_VERSION", message);
    this.name = "DisUnsupportedFormatVersionError";
  }
};
var DisIntegrityError = class extends DisError {
  constructor(message = "Integrity check failed") {
    super("INTEGRITY_CHECK_FAILED", message);
    this.name = "DisIntegrityError";
  }
};
var DisLegacyPayloadError = class extends DisError {
  constructor(message = "Legacy payload requires explicit migration") {
    super("LEGACY_PAYLOAD_REQUIRES_MIGRATION", message);
    this.name = "DisLegacyPayloadError";
  }
};

export { DisDecryptionError, DisError, DisIntegrityError, DisInvalidArgumentError, DisLegacyPayloadError, DisUnsupportedFormatVersionError };
//# sourceMappingURL=chunk-MJO7IJZC.js.map
//# sourceMappingURL=chunk-MJO7IJZC.js.map