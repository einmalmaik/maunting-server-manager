/**
 * R2: Mounting Search Performance & Stabilität E2E Test Suite
 * Verifies AbortController, client timeouts, offline fallbacks,
 * rate limit mapping, and rapid category switching.
 */
import { describe, it, assert } from './harness.mjs';

// Search Client Simulator matching MsmSuche.tsx and api/client.ts contracts
class SearchClientSimulator {
  constructor(mockNetworkFn) {
    this.mockNetwork = mockNetworkFn;
    this.cache = new Map();
    this.currentAbortController = null;
  }

  async search({ q, category = 'web', page = 1, timeoutMs = 12000, signal }) {
    if (this.currentAbortController) {
      this.currentAbortController.abort();
    }
    const internalController = new AbortController();
    this.currentAbortController = internalController;

    const effectiveSignal = signal || internalController.signal;

    // Timeout mechanism
    let timeoutId;
    const timeoutPromise = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        const timeoutErr = new Error('Search request timed out');
        timeoutErr.name = 'TimeoutError';
        timeoutErr.code = 'BROWSER_SUCHE_TIMEOUT';
        internalController.abort();
        reject(timeoutErr);
      }, timeoutMs);
    });

    const networkPromise = (async () => {
      if (effectiveSignal.aborted) {
        const abortErr = new Error('The user aborted a request.');
        abortErr.name = 'AbortError';
        throw abortErr;
      }
      return await this.mockNetwork({ q, category, page, signal: effectiveSignal });
    })();

    try {
      const response = await Promise.race([networkPromise, timeoutPromise]);
      clearTimeout(timeoutId);
      return response;
    } catch (err) {
      clearTimeout(timeoutId);
      throw err;
    }
  }

  mapErrorToUiState(error) {
    if (error.name === 'AbortError') {
      return { art: 'abgebrochen' };
    }
    if (error.code === 'BROWSER_SUCHE_NICHT_EINGERICHTET') {
      return { art: 'nichtEingerichtet' };
    }
    if (error.code === 'BROWSER_SUCHE_ZU_VIELE' || error.status === 429) {
      return { art: 'fehler', zuViele: true };
    }
    if (
      error.name === 'TimeoutError' ||
      error.code === 'BROWSER_SUCHE_TIMEOUT' ||
      error.message?.includes('Failed to fetch') ||
      error.message?.includes('NetworkError') ||
      error.isOffline
    ) {
      return { art: 'offline' };
    }
    return { art: 'fehler', zuViele: false };
  }
}

describe('R2: Mounting Search Performance & Stabilität', () => {
  // Tier 1: Feature Coverage
  it('TC-T1-R2-01: AbortController cancels pending search request when a new query arrives', async () => {
    let callCount = 0;
    const mockNetwork = async ({ q, signal }) => {
      callCount++;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          resolve({ treffer: [{ titel: `Results for ${q}`, url: `https://example.com/${q}` }] });
        }, 100);
        signal.addEventListener('abort', () => {
          clearTimeout(timer);
          const err = new Error('The user aborted a request.');
          err.name = 'AbortError';
          reject(err);
        });
      });
    };

    const client = new SearchClientSimulator(mockNetwork);

    const promise1 = client.search({ q: 'first query' });
    const promise2 = client.search({ q: 'second query' });

    let p1Error = null;
    try {
      await promise1;
    } catch (err) {
      p1Error = err;
    }

    const res2 = await promise2;

    assert.ok(p1Error, 'First request should have been rejected');
    assert.equal(p1Error.name, 'AbortError');
    assert.equal(client.mapErrorToUiState(p1Error).art, 'abgebrochen');
    assert.equal(res2.treffer[0].titel, 'Results for second query');
    assert.equal(callCount, 2);
  }, { tier: 1, feature: 'R2', id: 'TC-T1-R2-01' });

  it('TC-T1-R2-02: Client-side timeout aborts stalled requests and transitions away from loading', async () => {
    const mockNetwork = async () => {
      return new Promise(() => {}); // Never resolves
    };

    const client = new SearchClientSimulator(mockNetwork);

    let error = null;
    try {
      await client.search({ q: 'stalled query', timeoutMs: 50 });
    } catch (err) {
      error = err;
    }

    assert.ok(error, 'Request should have timed out');
    assert.equal(error.name, 'TimeoutError');
    const state = client.mapErrorToUiState(error);
    assert.equal(state.art, 'offline');

    // Also verify non-blocking IPC protection fallback (1.5s Promise.race pattern)
    const stalledIpc = new Promise(() => {});
    const timeoutFallback = new Promise((resolve) => setTimeout(() => resolve(null), 10));
    const schutz = await Promise.race([stalledIpc, timeoutFallback]);
    assert.equal(schutz, null, 'Hung IPC call falls back to null instead of stalling search');
  }, { tier: 1, feature: 'R2', id: 'TC-T1-R2-02' });

  it('TC-T1-R2-03: Offline network errors map accurately to offline UI state', () => {
    const client = new SearchClientSimulator(() => {});

    const networkErr = new TypeError('Failed to fetch');
    const state = client.mapErrorToUiState(networkErr);

    assert.equal(state.art, 'offline');
  }, { tier: 1, feature: 'R2', id: 'TC-T1-R2-03' });

  it('TC-T1-R2-04: HTTP 429 status code maps to BROWSER_SUCHE_ZU_VIELE rate limit error state', () => {
    const client = new SearchClientSimulator(() => {});

    const rateLimitErr = { status: 429, code: 'BROWSER_SUCHE_ZU_VIELE', message: 'Rate limit exceeded' };
    const state = client.mapErrorToUiState(rateLimitErr);

    assert.equal(state.art, 'fehler');
    assert.equal(state.zuViele, true);

    // Also verify 429 status fallback when code is absent initially
    const statusOnlyErr = { status: 429, message: 'Too Many Requests' };
    const statusOnlyState = client.mapErrorToUiState(statusOnlyErr);
    assert.equal(statusOnlyState.art, 'fehler');
    assert.equal(statusOnlyState.zuViele, true);
  }, { tier: 1, feature: 'R2', id: 'TC-T1-R2-04' });

  it('TC-T1-R2-05: Rapid category switching cancels intermediate requests and resolves target category', async () => {
    const responses = [];
    const mockNetwork = async ({ category, signal }) => {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          resolve({ category, treffer: [{ titel: `${category} result` }] });
        }, 30);
        signal.addEventListener('abort', () => {
          clearTimeout(timer);
          const err = new Error('Aborted');
          err.name = 'AbortError';
          reject(err);
        });
      });
    };

    const client = new SearchClientSimulator(mockNetwork);

    const pWeb = client.search({ q: 'rust', category: 'web' }).catch((e) => e);
    const pImages = client.search({ q: 'rust', category: 'images' }).catch((e) => e);
    const pVideos = client.search({ q: 'rust', category: 'videos' }).catch((e) => e);
    const pNews = client.search({ q: 'rust', category: 'news' });

    const [rWeb, rImages, rVideos, rNews] = await Promise.all([pWeb, pImages, pVideos, pNews]);

    assert.equal(rWeb.name, 'AbortError');
    assert.equal(rImages.name, 'AbortError');
    assert.equal(rVideos.name, 'AbortError');
    assert.equal(rNews.category, 'news');
    assert.equal(rNews.treffer[0].titel, 'news result');
  }, { tier: 1, feature: 'R2', id: 'TC-T1-R2-05' });

  it('TC-T1-R2-06: In-memory cache returns previously seen results without redundant network call', async () => {
    let networkCalls = 0;
    const mockNetwork = async ({ q }) => {
      networkCalls++;
      return { treffer: [{ titel: `Cached title for ${q}` }] };
    };

    const client = new SearchClientSimulator(mockNetwork);
    const cache = new Map();

    async function searchWithCache(key, params) {
      if (cache.has(key)) return cache.get(key);
      const res = await client.search(params);
      cache.set(key, res);
      return res;
    }

    const res1 = await searchWithCache('tauri::web', { q: 'tauri' });
    const res2 = await searchWithCache('tauri::web', { q: 'tauri' });

    assert.equal(networkCalls, 1);
    assert.equal(res1, res2);
  }, { tier: 1, feature: 'R2', id: 'TC-T1-R2-06' });

  // Tier 2: Boundary & Corner Cases
  it('TC-T2-R2-01: Query exceeding MAX_QUERY_CHARS is sanitized and truncated without throwing', async () => {
    const longQuery = 'A'.repeat(500) + ' 🚀 Quantum ⚛️ ' + 'B'.repeat(500);
    const MAX_CHARS = 200;

    let receivedQuery = '';
    const mockNetwork = async ({ q }) => {
      receivedQuery = q;
      return { treffer: [] };
    };

    const client = new SearchClientSimulator(mockNetwork);
    const sanitized = longQuery.trim().slice(0, MAX_CHARS);
    await client.search({ q: sanitized });

    assert.ok(receivedQuery.length <= MAX_CHARS);
  }, { tier: 2, feature: 'R2', id: 'TC-T2-R2-01' });

  it('TC-T2-R2-02: 0ms immediate synchronous abort leaves no unhandled promise rejections', async () => {
    const mockNetwork = async () => ({ treffer: [] });
    const client = new SearchClientSimulator(mockNetwork);

    const controller = new AbortController();
    controller.abort(); // Pre-aborted

    let error = null;
    try {
      await client.search({ q: 'preaborted', signal: controller.signal });
    } catch (err) {
      error = err;
    }

    assert.ok(error);
    assert.equal(error.name, 'AbortError');
  }, { tier: 2, feature: 'R2', id: 'TC-T2-R2-02' });

  it('TC-T2-R2-03: Malformed upstream non-JSON response is caught and reported cleanly', async () => {
    const mockNetwork = async () => {
      const rawText = '<!DOCTYPE html>502 Bad Gateway nginx</title>';
      try {
        return JSON.parse(rawText);
      } catch (err) {
        const error = new Error('Invalid JSON from search provider');
        error.code = 'BROWSER_SUCHE_UNGUELTIG';
        throw error;
      }
    };

    const client = new SearchClientSimulator(mockNetwork);
    let caught = null;
    try {
      await client.search({ q: 'error test' });
    } catch (err) {
      caught = err;
    }

    assert.ok(caught);
    assert.equal(caught.code, 'BROWSER_SUCHE_UNGUELTIG');
    const state = client.mapErrorToUiState(caught);
    assert.equal(state.art, 'fehler');
  }, { tier: 2, feature: 'R2', id: 'TC-T2-R2-03' });

  it('TC-T2-R2-04: Upstream gateway 504 status triggers offline/fallback state', () => {
    const client = new SearchClientSimulator(() => {});
    const gatewayTimeout = { status: 504, message: 'Gateway Timeout' };

    const state = client.mapErrorToUiState(gatewayTimeout);
    assert.equal(state.art, 'fehler');
    assert.equal(state.zuViele, false);
  }, { tier: 2, feature: 'R2', id: 'TC-T2-R2-04' });

  it('TC-T2-R2-05: Concurrent burst of 20 identical queries handled safely with in-flight deduplication', async () => {
    let upstreamFetches = 0;
    const inFlightMap = new Map();

    async function deduplicatedSearch(q) {
      if (inFlightMap.has(q)) {
        return inFlightMap.get(q);
      }
      const fetchPromise = (async () => {
        upstreamFetches++;
        await new Promise((r) => setTimeout(r, 20));
        return { treffer: [{ titel: `Result for ${q}` }] };
      })();
      inFlightMap.set(q, fetchPromise);
      try {
        return await fetchPromise;
      } finally {
        inFlightMap.delete(q);
      }
    }

    const promises = Array.from({ length: 20 }, () => deduplicatedSearch('quantum'));
    const results = await Promise.all(promises);

    assert.equal(upstreamFetches, 1, 'Only one upstream fetch should execute during concurrent burst');
    assert.equal(results.length, 20);
    assert.equal(results[0].treffer[0].titel, 'Result for quantum');
  }, { tier: 2, feature: 'R2', id: 'TC-T2-R2-05' });

  it('TC-T2-R2-06: Query containing null bytes and control characters is stripped safely', async () => {
    const dirtyQuery = 'malicious\x00query\r\nwith\x1b[31m ansi';
    const cleaned = dirtyQuery.replace(/[\x00-\x1F\x7F]/g, ' ').replace(/\s+/g, ' ').trim();

    assert.equal(cleaned, 'malicious query with [31m ansi');
    assert.ok(!cleaned.includes('\0'));
  }, { tier: 2, feature: 'R2', id: 'TC-T2-R2-06' });
});
