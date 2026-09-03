import { DisInvalidArgumentError, DisUnsupportedFormatVersionError } from './chunk-MJO7IJZC.js';

// src/format-versioning/index.ts
function formatEnvelope(spec, encryptedBase64) {
  if (!encryptedBase64) {
    throw new DisInvalidArgumentError(`Empty ${spec.subject} ciphertext`);
  }
  return `${spec.currentPrefix}${encryptedBase64}`;
}
function parseEnvelope(spec, encryptedData) {
  if (encryptedData.startsWith(spec.currentPrefix)) {
    const payload = encryptedData.slice(spec.currentPrefix.length);
    if (!payload) {
      throw new DisInvalidArgumentError(`Invalid ${spec.subject} encryption envelope`);
    }
    return { version: 1, payload };
  }
  if (encryptedData.startsWith(spec.familyPrefix)) {
    throw new DisUnsupportedFormatVersionError(
      `Unsupported ${spec.subject} encryption envelope version`
    );
  }
  return { version: "legacy", payload: encryptedData };
}
function isCurrentEnvelope(spec, encryptedData) {
  try {
    return parseEnvelope(spec, encryptedData).version === 1;
  } catch {
    return false;
  }
}

export { formatEnvelope, isCurrentEnvelope, parseEnvelope };
//# sourceMappingURL=chunk-SCHZI6YY.js.map
//# sourceMappingURL=chunk-SCHZI6YY.js.map