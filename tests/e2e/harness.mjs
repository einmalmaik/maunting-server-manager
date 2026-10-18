/**
 * E2E Test Harness & Execution Engine
 * Provides test registration, DOM simulation, network mocking, and diagnostic reporting.
 */
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { JSDOM } from '../../frontend/node_modules/jsdom/lib/api.js';

export { assert };

export const suites = [];
let currentSuite = null;

export function describe(name, fn) {
  const suite = {
    name,
    tests: [],
  };
  suites.push(suite);
  const prevSuite = currentSuite;
  currentSuite = suite;
  try {
    fn();
  } finally {
    currentSuite = prevSuite;
  }
}

export function it(name, fn, meta = {}) {
  if (!currentSuite) {
    throw new Error(`Test "${name}" must be inside a describe block`);
  }
  currentSuite.tests.push({
    name,
    fn,
    tier: meta.tier || 1,
    feature: meta.feature || 'General',
    id: meta.id || `TC-${Math.random().toString(36).substring(2, 7)}`,
  });
}

/**
 * Creates a sandboxed JSDOM environment with custom HTML and location URL
 */
export function createMockDom(html, url = 'https://example.com/article') {
  const dom = new JSDOM(html, {
    url,
    runScripts: 'outside-only',
  });
  return {
    window: dom.window,
    document: dom.window.document,
    evaluate: (code) => {
      const script = new vm.Script(code);
      const context = dom.getInternalVMContext();
      return script.runInContext(context);
    },
  };
}

/**
 * Simulates SSE token streaming
 */
export async function* createMockSseStream(events, delayMs = 5) {
  for (const event of events) {
    if (delayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    yield `data: ${JSON.stringify(event)}\n\n`;
  }
}

/**
 * Reassembles raw chunks into parsed SSE events
 */
export async function parseSseChunks(asyncIterable) {
  const events = [];
  let buffer = '';

  for await (const chunk of asyncIterable) {
    buffer += chunk;
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) continue;
      const jsonStr = trimmed.slice(5).trim();
      if (!jsonStr) continue;
      try {
        events.push(JSON.parse(jsonStr));
      } catch (err) {
        // Skip malformed/incomplete JSON chunks
      }
    }
  }
  return events;
}

/**
 * Memory File System Mock for UDF Isolation Tests
 */
export class MockFilesystem {
  constructor() {
    this.directories = new Set();
    this.files = new Map();
  }

  mkdirSync(dirPath, options = {}) {
    const normalized = dirPath.replace(/\\/g, '/');
    this.directories.add(normalized);
  }

  writeFileSync(filePath, data) {
    const normalized = filePath.replace(/\\/g, '/');
    const parentDir = normalized.substring(0, normalized.lastIndexOf('/'));
    if (parentDir) this.directories.add(parentDir);
    this.files.set(normalized, data);
  }

  readFileSync(filePath) {
    const normalized = filePath.replace(/\\/g, '/');
    if (!this.files.has(normalized)) {
      const err = new Error(`ENOENT: no such file or directory, open '${filePath}'`);
      err.code = 'ENOENT';
      throw err;
    }
    return this.files.get(normalized);
  }

  existsSync(path) {
    const normalized = path.replace(/\\/g, '/');
    if (this.directories.has(normalized)) return true;
    if (this.files.has(normalized)) return true;
    for (const d of this.directories) {
      if (d.startsWith(normalized + '/')) return true;
    }
    for (const f of this.files.keys()) {
      if (f.startsWith(normalized + '/')) return true;
    }
    return false;
  }

  rmdirSync(dirPath, options = {}) {
    const normalized = dirPath.replace(/\\/g, '/');
    this.directories.delete(normalized);
    for (const d of Array.from(this.directories)) {
      if (d === normalized || d.startsWith(normalized + '/')) {
        this.directories.delete(d);
      }
    }
    for (const f of Array.from(this.files.keys())) {
      if (f.startsWith(normalized + '/')) {
        this.files.delete(f);
      }
    }
  }
}
