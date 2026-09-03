import { sha256JsonBase64, sha256Base64 } from './chunk-MPWYZXW7.js';
import { randomBytes } from './chunk-3HCT6A2P.js';
import { importAesGcmKey } from './chunk-OPHN2B3N.js';
import { encryptBytes, decryptBytes } from './chunk-U65A4HIY.js';
import { AES_KEY_LENGTH } from './chunk-BUFRR5PB.js';
import { DisInvalidArgumentError } from './chunk-MJO7IJZC.js';

// src/file-encryption/index.ts
var DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024;
var FILE_MANIFEST_V1_PREFIX = "sv-file-manifest-v1:";
function manifestAad(ctx) {
  return `sv-file-manifest-v1:${ctx.ownerId}:${ctx.vaultItemId}:${ctx.fileId}`;
}
function fileKeyAad(ctx) {
  return `sv-file-key-v1:${ctx.ownerId}:${ctx.vaultItemId}:${ctx.fileId}`;
}
function chunkAad(ctx, fileRevision, manifestRoot, chunkIndex, chunkCount) {
  return `sv-file-chunk-v1:${ctx.ownerId}:${ctx.vaultItemId}:${ctx.fileId}:${fileRevision}:${manifestRoot}:${chunkIndex}:${chunkCount}`;
}
function generateFileKeyBytes() {
  return randomBytes(AES_KEY_LENGTH);
}
function importFileKey(rawKey) {
  return importAesGcmKey(rawKey);
}
async function encryptChunk(plaintext, fileKey, aad) {
  try {
    return await encryptBytes(plaintext, fileKey, aad);
  } finally {
    plaintext.fill(0);
  }
}
async function decryptChunk(encryptedBase64, fileKey, aad) {
  return decryptBytes(encryptedBase64, fileKey, aad);
}
async function computeManifestRoot(input) {
  return sha256JsonBase64({
    file_id: input.fileId,
    file_revision: input.fileRevision,
    chunk_size: input.chunkSize,
    chunk_count: input.chunkCount,
    chunks: input.chunks.map((c) => ({
      index: c.index,
      storage_path: void 0,
      plaintext_size: c.plaintext_size
    }))
  });
}
function chunkCiphertextHash(ciphertextBase64) {
  return sha256Base64(new TextEncoder().encode(ciphertextBase64));
}
async function encryptAttachment(input) {
  const chunkSize = input.chunkSize ?? DEFAULT_CHUNK_SIZE;
  if (chunkSize <= 0) throw new DisInvalidArgumentError("chunkSize must be positive");
  const fileRevision = input.fileRevision ?? 1;
  const chunkCount = Math.max(1, Math.ceil(input.totalSize / chunkSize));
  const ctx = input.context;
  const plannedChunks = Array.from({ length: chunkCount }, (_, index) => {
    const start = index * chunkSize;
    const end = Math.min(input.totalSize, start + chunkSize);
    return { index, plaintext_size: end - start };
  });
  const manifestRoot = await computeManifestRoot({
    fileId: ctx.fileId,
    fileRevision,
    chunkSize,
    chunkCount,
    chunks: plannedChunks
  });
  const fileKeyBytes = generateFileKeyBytes();
  const fileKey = await importFileKey(fileKeyBytes);
  const chunks = [];
  try {
    const wrappedFileKey = await input.wrapFileKey(fileKeyBytes, fileKeyAad(ctx));
    for (let index = 0; index < chunkCount; index += 1) {
      const start = index * chunkSize;
      const end = Math.min(input.totalSize, start + chunkSize);
      const plaintext = await input.readChunk(start, end);
      const aad = chunkAad(ctx, fileRevision, manifestRoot, index, chunkCount);
      const ciphertext = await encryptChunk(plaintext, fileKey, aad);
      const ciphertextSize = await input.writeChunk(index, ciphertext);
      chunks.push({
        index,
        plaintext_size: plannedChunks[index].plaintext_size,
        ciphertext_size: ciphertextSize,
        ciphertext_sha256: await chunkCiphertextHash(ciphertext)
      });
    }
    const manifest = {
      version: 1,
      algorithm: "AES-256-GCM",
      file_id: ctx.fileId,
      file_revision: fileRevision,
      previous_manifest_hash: null,
      manifest_root: manifestRoot,
      owner_id: ctx.ownerId,
      vault_item_id: ctx.vaultItemId,
      original_name: input.metadata.original_name,
      mime_type: input.metadata.mime_type,
      original_size: input.totalSize,
      last_modified: input.metadata.last_modified,
      uploaded_at: (/* @__PURE__ */ new Date()).toISOString(),
      chunk_size: chunkSize,
      chunk_count: chunkCount,
      wrapped_file_key: wrappedFileKey,
      chunks,
      preview: null,
      notes: null
    };
    return { manifest, manifestRoot };
  } finally {
    fileKeyBytes.fill(0);
  }
}
async function decryptAttachment(input) {
  const { manifest, context: ctx } = input;
  const verifyHashes = input.verifyChunkHashes ?? true;
  const fileKeyBytes = await input.unwrapFileKey(manifest.wrapped_file_key, fileKeyAad(ctx));
  const fileKey = await importFileKey(fileKeyBytes);
  try {
    fileKeyBytes.fill(0);
    for (const chunkMeta of manifest.chunks) {
      const ciphertext = await input.readChunk(chunkMeta.index, chunkMeta.ciphertext_sha256);
      if (verifyHashes) {
        const actual = await chunkCiphertextHash(ciphertext);
        if (actual !== chunkMeta.ciphertext_sha256) {
          throw new DisInvalidArgumentError(
            `Chunk ${chunkMeta.index} ciphertext hash mismatch`
          );
        }
      }
      const aad = chunkAad(
        ctx,
        manifest.file_revision,
        manifest.manifest_root,
        chunkMeta.index,
        manifest.chunk_count
      );
      const plaintext = await decryptChunk(ciphertext, fileKey, aad);
      try {
        await input.writeChunk(chunkMeta.index, plaintext);
      } finally {
        plaintext.fill(0);
      }
    }
  } finally {
    fileKeyBytes.fill(0);
  }
}

export { DEFAULT_CHUNK_SIZE, FILE_MANIFEST_V1_PREFIX, chunkAad, chunkCiphertextHash, computeManifestRoot, decryptAttachment, decryptChunk, encryptAttachment, encryptChunk, fileKeyAad, generateFileKeyBytes, importFileKey, manifestAad };
//# sourceMappingURL=chunk-EOXWR7DS.js.map
//# sourceMappingURL=chunk-EOXWR7DS.js.map