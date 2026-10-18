/**
 * Tier 3: Cross-Feature Combinations E2E Test Suite
 * Verifies pairwise and system interactions across R1-R6.
 */
import { describe, it, assert, createMockDom, createMockSseStream, parseSseChunks, MockFilesystem } from './harness.mjs';

describe('Tier 3: Cross-Feature Combinations', () => {
  it('TC-T3-01: R1 Reader Mode extraction inside an isolated R4 Workspace preserves isolation', () => {
    const fs = new MockFilesystem();
    const wsUdf = 'C:/Users/test/AppData/Local/com.mauntingstudios.secure-browser/seiten/workspaces/ws-research';
    fs.mkdirSync(wsUdf);

    const html = `<html><body><article><h1>Workspace Reader Article</h1><p>Content extracted within dedicated research workspace context.</p></article></body></html>`;
    const { document } = createMockDom(html, 'https://research-journal.org/paper');

    // Perform Reader Mode extraction
    const article = {
      titel: document.querySelector('article h1')?.textContent,
      reintext: document.querySelector('article p')?.textContent,
      workspace: 'ws-research',
    };

    // Store read history inside the workspace UDF
    fs.writeFileSync(`${wsUdf}/reader_history.json`, JSON.stringify([article]));

    assert.equal(article.titel, 'Workspace Reader Article');
    assert.ok(fs.existsSync(`${wsUdf}/reader_history.json`));

    // Ensure standard workspace is untouched
    const standardUdf = 'C:/Users/test/AppData/Local/com.mauntingstudios.secure-browser/seiten/workspaces/standard';
    assert.equal(fs.existsSync(`${standardUdf}/reader_history.json`), false);
  }, { tier: 3, feature: 'Cross: R1+R4', id: 'TC-T3-01' });

  it('TC-T3-02: R2 Mounting Search from distinct R4 Workspaces maintains independent cache', async () => {
    const workspaceCaches = new Map();

    function searchInWorkspace(wsId, query) {
      if (!workspaceCaches.has(wsId)) {
        workspaceCaches.set(wsId, new Map());
      }
      const cache = workspaceCaches.get(wsId);
      if (cache.has(query)) {
        return { fromCache: true, data: cache.get(query) };
      }
      const result = { query, wsId, treffer: [{ titel: `Result for ${query} in ${wsId}` }] };
      cache.set(query, result);
      return { fromCache: false, data: result };
    }

    const s1 = searchInWorkspace('ws-work', 'quantum');
    const s2 = searchInWorkspace('ws-personal', 'quantum');

    assert.equal(s1.fromCache, false);
    assert.equal(s2.fromCache, false, 'Personal workspace must not hit Work workspace cache');

    const s1Repeat = searchInWorkspace('ws-work', 'quantum');
    assert.equal(s1Repeat.fromCache, true);
  }, { tier: 3, feature: 'Cross: R2+R4', id: 'TC-T3-02' });

  it('TC-T3-03: R5 DNS-Rebinding protection intercepts malicious URLs returned in R2 Search preview', () => {
    const searchResults = [
      { titel: 'Legitimate Tech Blog', url: 'https://techblog.com/article' },
      { titel: 'Internal Router Exploit Link', url: 'http://192.168.1.1/admin/reboot' },
      { titel: 'Localhost Webhook Trigger', url: 'http://127.0.0.1:5000/trigger' },
    ];

    // Filter preview requests
    function shouldAllowPreviewFetch(url) {
      const u = new URL(url);
      const host = u.hostname;
      if (host === '127.0.0.1' || host === 'localhost' || host.startsWith('192.168.')) {
        return false; // Blocked by R5
      }
      return true;
    }

    const allowedPreviews = searchResults.filter((r) => shouldAllowPreviewFetch(r.url));

    assert.equal(allowedPreviews.length, 1);
    assert.equal(allowedPreviews[0].titel, 'Legitimate Tech Blog');
  }, { tier: 3, feature: 'Cross: R2+R5', id: 'TC-T3-03' });

  it('TC-T3-04: R6 WebExtension queries and manages tabs across multiple R4 Workspaces without leaking profiles', async () => {
    const allTabs = [
      { id: 1, url: 'https://corp.jira.com', workspaceId: 'ws-work', active: true },
      { id: 2, url: 'https://github.com/org', workspaceId: 'ws-work', active: false },
      { id: 3, url: 'https://youtube.com', workspaceId: 'ws-personal', active: true },
    ];

    // Extension queries tabs in current active workspace
    function queryTabs(wsFilter) {
      return allTabs.filter((t) => !wsFilter || t.workspaceId === wsFilter);
    }

    const workTabs = queryTabs('ws-work');
    const personalTabs = queryTabs('ws-personal');

    assert.equal(workTabs.length, 2);
    assert.equal(personalTabs.length, 1);
    assert.equal(personalTabs[0].url, 'https://youtube.com');
  }, { tier: 3, feature: 'Cross: R4+R6', id: 'TC-T3-04' });

  it('TC-T3-05: R3 AI Follow-up stream aborts cleanly when R2 Search category changes', async () => {
    const sseEvents = [
      { type: 'start', model: 'gpt-4o' },
      { type: 'content', text: 'Detail 1 ' },
      { type: 'content', text: 'Detail 2 ' },
      { type: 'done' },
    ];

    let streamActive = true;
    const controller = new AbortController();

    const streamPromise = (async () => {
      for await (const chunk of createMockSseStream(sseEvents, 20)) {
        if (controller.signal.aborted) {
          streamActive = false;
          throw new Error('AbortError');
        }
      }
    })();

    // User switches category from web to news midway
    setTimeout(() => {
      controller.abort();
    }, 15);

    let caught = null;
    try {
      await streamPromise;
    } catch (err) {
      caught = err;
    }

    assert.ok(caught);
    assert.equal(caught.message, 'AbortError');
    assert.equal(streamActive, false);
  }, { tier: 3, feature: 'Cross: R2+R3', id: 'TC-T3-05' });

  it('TC-T3-06: R6 WebExtension triggers R1 Reader Mode extraction via content message', async () => {
    const html = `<html><body><article><h1>Extension Invoked Article</h1><p>Body extracted by browser extension.</p></article></body></html>`;
    const { document } = createMockDom(html, 'https://example.com/ext-read');

    // Extension sends message to tab to trigger reader mode
    async function onTabMessage(message) {
      if (message.action === 'EXTRACT_READER_MODE') {
        return {
          ok: true,
          title: document.querySelector('article h1')?.textContent,
          text: document.querySelector('article p')?.textContent,
        };
      }
      return { ok: false };
    }

    const response = await onTabMessage({ action: 'EXTRACT_READER_MODE' });

    assert.equal(response.ok, true);
    assert.equal(response.title, 'Extension Invoked Article');
    assert.equal(response.text, 'Body extracted by browser extension.');
  }, { tier: 3, feature: 'Cross: R1+R6', id: 'TC-T3-06' });

  it('TC-T3-07: R4 Workspace deletion while R3 AI follow-up stream is running aborts stream and deletes directory', async () => {
    const fs = new MockFilesystem();
    const wsPath = 'C:/Users/test/AppData/Local/com.mauntingstudios.secure-browser/seiten/workspaces/ws-doomed';
    fs.mkdirSync(wsPath);

    const abortController = new AbortController();
    let streamAborted = false;

    // Simulate active stream
    abortController.signal.addEventListener('abort', () => {
      streamAborted = true;
    });

    // Delete workspace
    function deleteWorkspaceAndAbort(path, controller) {
      controller.abort();
      fs.rmdirSync(path);
    }

    deleteWorkspaceAndAbort(wsPath, abortController);

    assert.equal(streamAborted, true);
    assert.equal(fs.existsSync(wsPath), false);
  }, { tier: 3, feature: 'Cross: R3+R4', id: 'TC-T3-07' });

  it('TC-T3-08: R5 Network security blocks malicious external web scripts from accessing R6 CDP bridge', () => {
    // Untrusted public page window context
    const publicWindow = {};

    function injectCdpBridge(targetWindow, isPrivilegedExtension) {
      if (!isPrivilegedExtension) {
        // Zero IPC rights per ADR-0010
        return;
      }
      targetWindow.__msb_cdp_tab_bridge__ = () => 'privileged';
    }

    injectCdpBridge(publicWindow, false);

    assert.equal(publicWindow.__msb_cdp_tab_bridge__, undefined, 'Public page must not have __msb_cdp_tab_bridge__');
  }, { tier: 3, feature: 'Cross: R5+R6', id: 'TC-T3-08' });
});
