import { getCryptoProvider } from './chunk-CYIGDF63.js';
import { DisInvalidArgumentError } from './chunk-MJO7IJZC.js';

// src/random/index.ts
function randomBytes(length) {
  if (!Number.isInteger(length) || length <= 0) {
    throw new DisInvalidArgumentError("randomBytes length must be a positive integer");
  }
  const out = new Uint8Array(length);
  getCryptoProvider().getRandomValues(out);
  return out;
}
function fillRandom(view) {
  return getCryptoProvider().getRandomValues(view);
}
function randomInt(min, max) {
  if (!Number.isInteger(min) || !Number.isInteger(max)) {
    throw new DisInvalidArgumentError("randomInt bounds must be integers");
  }
  if (max < min) {
    throw new DisInvalidArgumentError("randomInt max must be >= min");
  }
  const range = max - min + 1;
  if (range === 1) {
    return min;
  }
  const bytesNeeded = Math.ceil(Math.log2(range) / 8) || 1;
  const maxValid = Math.floor(256 ** bytesNeeded / range) * range - 1;
  const buffer = new Uint8Array(bytesNeeded);
  let randomValue;
  do {
    getCryptoProvider().getRandomValues(buffer);
    randomValue = 0;
    for (let i = 0; i < bytesNeeded; i++) {
      randomValue = randomValue << 8 | buffer[i];
    }
  } while (randomValue > maxValid);
  return min + randomValue % range;
}
function randomUuid() {
  const provider = getCryptoProvider();
  if (typeof provider.randomUUID === "function") {
    return provider.randomUUID();
  }
  const bytes = randomBytes(16);
  bytes[6] = bytes[6] & 15 | 64;
  bytes[8] = bytes[8] & 63 | 128;
  const hex = [];
  for (let i = 0; i < 16; i++) hex.push(bytes[i].toString(16).padStart(2, "0"));
  return `${hex[0]}${hex[1]}${hex[2]}${hex[3]}-${hex[4]}${hex[5]}-${hex[6]}${hex[7]}-${hex[8]}${hex[9]}-${hex[10]}${hex[11]}${hex[12]}${hex[13]}${hex[14]}${hex[15]}`;
}

export { fillRandom, randomBytes, randomInt, randomUuid };
//# sourceMappingURL=chunk-3HCT6A2P.js.map
//# sourceMappingURL=chunk-3HCT6A2P.js.map