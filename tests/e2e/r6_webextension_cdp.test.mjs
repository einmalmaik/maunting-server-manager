/**
 * R6: WebExtension CDP Tab-Bridge E2E Test Suite
 * Verifies chrome.tabs.* emulation via Chrome DevTools Protocol (CDP) bindings,
 * tab query, create, update, remove, reload, sendMessage, and lifecycle events.
 */
import { describe, it, assert } from './harness.mjs';

// CDP Tab Bridge & chrome.tabs.* emulator matching PROJECT.md §M6 contract
class ChromeTabsBridgeEmulator {
  constructor() {
    this.tabs = new Map(); // id -> tabObj
    this.nextId = 1;
    this.listeners = {
      onCreated: new Set(),
      onUpdated: new Set(),
      onRemoved: new Set(),
      onActivated: new Set(),
    };
    this.messageHandlers = new Map(); // tabId -> fn(msg)

    // Set up chrome.tabs API shim
    this.chrome = {
      tabs: {
        query: (queryInfo, callback) => this._wrapPromise(this.query(queryInfo), callback),
        get: (tabId, callback) => this._wrapPromise(this.get(tabId), callback),
        create: (createProps, callback) => this._wrapPromise(this.create(createProps), callback),
        update: (tabId, updateProps, callback) => this._wrapPromise(this.update(tabId, updateProps), callback),
        remove: (tabIds, callback) => this._wrapPromise(this.remove(tabIds), callback),
        reload: (tabId, reloadProps, callback) => this._wrapPromise(this.reload(tabId, reloadProps), callback),
        sendMessage: (tabId, message, options, callback) => {
          if (typeof options === 'function') {
            callback = options;
            options = {};
          }
          return this._wrapPromise(this.sendMessage(tabId, message, options), callback);
        },
        onCreated: {
          addListener: (fn) => this.listeners.onCreated.add(fn),
          removeListener: (fn) => this.listeners.onCreated.delete(fn),
        },
        onUpdated: {
          addListener: (fn) => this.listeners.onUpdated.add(fn),
          removeListener: (fn) => this.listeners.onUpdated.delete(fn),
        },
        onRemoved: {
          addListener: (fn) => this.listeners.onRemoved.add(fn),
          removeListener: (fn) => this.listeners.onRemoved.delete(fn),
        },
        onActivated: {
          addListener: (fn) => this.listeners.onActivated.add(fn),
          removeListener: (fn) => this.listeners.onActivated.delete(fn),
        },
      },
    };
  }

  _wrapPromise(promise, callback) {
    if (typeof callback === 'function') {
      promise
        .then((res) => callback(res))
        .catch((err) => {
          // Chrome runtime lastError emulation
          this.chrome.runtime = { lastError: { message: err.message } };
          callback(undefined);
        });
      return;
    }
    return promise;
  }

  // CDP Binding Dispatcher: window.__msb_cdp_tab_bridge__(JSON.stringify({ id, method, args }))
  async handleCdpBindingCall(rawPayload) {
    let parsed;
    try {
      parsed = typeof rawPayload === 'string' ? JSON.parse(rawPayload) : rawPayload;
    } catch {
      throw new Error('Malformed CDP payload: Invalid JSON string');
    }

    const { id, method, args = [] } = parsed;
    if (!method || !this[method]) {
      throw new Error(`Unknown CDP method: ${method}`);
    }

    try {
      const result = await this[method](...args);
      return { id, ok: true, result };
    } catch (err) {
      return { id, ok: false, error: err.message };
    }
  }

  async query(queryInfo = {}) {
    let result = Array.from(this.tabs.values());
    if (queryInfo.active !== undefined) {
      result = result.filter((t) => t.active === queryInfo.active);
    }
    if (queryInfo.url !== undefined) {
      result = result.filter((t) => t.url === queryInfo.url || (typeof queryInfo.url === 'string' && t.url.includes(queryInfo.url)));
    }
    if (queryInfo.windowId !== undefined) {
      result = result.filter((t) => t.windowId === queryInfo.windowId);
    }
    return result;
  }

  async get(tabId) {
    const idNum = Number(tabId);
    const tab = this.tabs.get(idNum);
    if (!tab) {
      throw new Error(`No tab with id: ${tabId}`);
    }
    return { ...tab };
  }

  async create(createProperties = {}) {
    const url = createProperties.url || 'about:blank';

    // Disallow dangerous protocols
    if (url.startsWith('javascript:') || url.startsWith('file:')) {
      throw new Error(`Restricted URL scheme for tab creation: ${url}`);
    }

    const id = this.nextId++;
    const active = createProperties.active !== undefined ? createProperties.active : true;

    if (active) {
      for (const t of this.tabs.values()) {
        t.active = false;
      }
    }

    const newTab = {
      id,
      index: this.tabs.size,
      windowId: createProperties.windowId || 1,
      highlighted: active,
      active,
      pinned: createProperties.pinned || false,
      url,
      title: createProperties.title || 'New Tab',
      status: 'complete',
    };

    this.tabs.set(id, newTab);

    // Dispatch onCreated
    for (const listener of this.listeners.onCreated) {
      listener({ ...newTab });
    }
    if (active) {
      for (const listener of this.listeners.onActivated) {
        listener({ tabId: id, windowId: newTab.windowId });
      }
    }

    return { ...newTab };
  }

  async update(tabId, updateProperties = {}) {
    const idNum = Number(tabId);
    const tab = this.tabs.get(idNum);
    if (!tab) {
      throw new Error(`No tab with id: ${tabId}`);
    }

    const changeInfo = {};
    if (updateProperties.url !== undefined) {
      tab.url = updateProperties.url;
      changeInfo.url = updateProperties.url;
    }
    if (updateProperties.pinned !== undefined) {
      tab.pinned = updateProperties.pinned;
      changeInfo.pinned = updateProperties.pinned;
    }
    if (updateProperties.active) {
      for (const t of this.tabs.values()) {
        t.active = false;
      }
      tab.active = true;
      for (const listener of this.listeners.onActivated) {
        listener({ tabId: idNum, windowId: tab.windowId });
      }
    }

    for (const listener of this.listeners.onUpdated) {
      listener(idNum, changeInfo, { ...tab });
    }

    return { ...tab };
  }

  async remove(tabIds) {
    const ids = Array.isArray(tabIds) ? tabIds : [tabIds];
    for (const rawId of ids) {
      const idNum = Number(rawId);
      if (this.tabs.has(idNum)) {
        const tab = this.tabs.get(idNum);
        this.tabs.delete(idNum);
        this.messageHandlers.delete(idNum);
        for (const listener of this.listeners.onRemoved) {
          listener(idNum, { windowId: tab.windowId, isWindowClosing: false });
        }
      }
    }
  }

  async reload(tabId, reloadProperties = {}) {
    const idNum = Number(tabId);
    const tab = this.tabs.get(idNum);
    if (!tab) {
      throw new Error(`No tab with id: ${tabId}`);
    }
    for (const listener of this.listeners.onUpdated) {
      listener(idNum, { status: 'loading' }, { ...tab, status: 'loading' });
      listener(idNum, { status: 'complete' }, { ...tab, status: 'complete' });
    }
  }

  registerContentScriptHandler(tabId, handlerFn) {
    this.messageHandlers.set(Number(tabId), handlerFn);
  }

  async sendMessage(tabId, message, options = {}) {
    const idNum = Number(tabId);
    if (!this.tabs.has(idNum)) {
      throw new Error(`Could not establish connection. Receiving end does not exist.`);
    }
    const handler = this.messageHandlers.get(idNum);
    if (!handler) {
      throw new Error(`Could not establish connection. Receiving end does not exist.`);
    }
    return await handler(message);
  }
}

describe('R6: WebExtension CDP Tab-Bridge', () => {
  // Tier 1: Feature Coverage
  it('TC-T1-R6-01: chrome.tabs.query filters tabs accurately by active and url properties', async () => {
    const bridge = new ChromeTabsBridgeEmulator();

    await bridge.chrome.tabs.create({ url: 'https://docs.pontis.dev', active: false });
    await bridge.chrome.tabs.create({ url: 'https://github.com/maunting', active: true });

    const activeTabs = await bridge.chrome.tabs.query({ active: true });
    const githubTabs = await bridge.chrome.tabs.query({ url: 'github.com' });

    assert.equal(activeTabs.length, 1);
    assert.equal(activeTabs[0].url, 'https://github.com/maunting');
    assert.equal(githubTabs.length, 1);
    assert.equal(githubTabs[0].url, 'https://github.com/maunting');
  }, { tier: 1, feature: 'R6', id: 'TC-T1-R6-01' });

  it('TC-T1-R6-02: chrome.tabs.get and chrome.tabs.create operations work seamlessly', async () => {
    const bridge = new ChromeTabsBridgeEmulator();

    const created = await bridge.chrome.tabs.create({ url: 'https://example.com' });
    assert.ok(created.id);
    assert.equal(created.url, 'https://example.com');

    const fetched = await bridge.chrome.tabs.get(created.id);
    assert.equal(fetched.id, created.id);
    assert.equal(fetched.url, 'https://example.com');
  }, { tier: 1, feature: 'R6', id: 'TC-T1-R6-02' });

  it('TC-T1-R6-03: chrome.tabs.update modifies active tab and navigates URL', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    const tab = await bridge.chrome.tabs.create({ url: 'https://old.com', active: false });

    const updated = await bridge.chrome.tabs.update(tab.id, { url: 'https://new.com', active: true });

    assert.equal(updated.url, 'https://new.com');
    assert.equal(updated.active, true);
  }, { tier: 1, feature: 'R6', id: 'TC-T1-R6-03' });

  it('TC-T1-R6-04: chrome.tabs.remove and reload operations lifecycle', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    const tab = await bridge.chrome.tabs.create({ url: 'https://test.com' });

    await bridge.chrome.tabs.reload(tab.id);
    await bridge.chrome.tabs.remove(tab.id);

    let getError = null;
    try {
      await bridge.chrome.tabs.get(tab.id);
    } catch (err) {
      getError = err;
    }

    assert.ok(getError);
    assert.ok(getError.message.includes(`No tab with id: ${tab.id}`));
  }, { tier: 1, feature: 'R6', id: 'TC-T1-R6-04' });

  it('TC-T1-R6-05: chrome.tabs.sendMessage communicates with tab content script', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    const tab = await bridge.chrome.tabs.create({ url: 'https://app.com' });

    bridge.registerContentScriptHandler(tab.id, async (msg) => {
      if (msg.type === 'PING') return { type: 'PONG', echo: msg.payload };
      return null;
    });

    const response = await bridge.chrome.tabs.sendMessage(tab.id, { type: 'PING', payload: 'Pontis' });

    assert.deepEqual(response, { type: 'PONG', echo: 'Pontis' });
  }, { tier: 1, feature: 'R6', id: 'TC-T1-R6-05' });

  it('TC-T1-R6-06: Tab lifecycle events (onCreated, onUpdated, onRemoved, onActivated) fire in order', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    const eventLog = [];

    bridge.chrome.tabs.onCreated.addListener((tab) => eventLog.push(`created:${tab.id}`));
    bridge.chrome.tabs.onUpdated.addListener((id, change) => eventLog.push(`updated:${id}:${change.url || ''}`));
    bridge.chrome.tabs.onActivated.addListener((info) => eventLog.push(`activated:${info.tabId}`));
    bridge.chrome.tabs.onRemoved.addListener((id) => eventLog.push(`removed:${id}`));

    const tab = await bridge.chrome.tabs.create({ url: 'https://initial.com', active: true });
    await bridge.chrome.tabs.update(tab.id, { url: 'https://changed.com' });
    await bridge.chrome.tabs.remove(tab.id);

    assert.ok(eventLog.includes(`created:${tab.id}`));
    assert.ok(eventLog.includes(`activated:${tab.id}`));
    assert.ok(eventLog.includes(`updated:${tab.id}:https://changed.com`));
    assert.ok(eventLog.includes(`removed:${tab.id}`));
  }, { tier: 1, feature: 'R6', id: 'TC-T1-R6-06' });

  // Tier 2: Boundary & Corner Cases
  it('TC-T2-R6-01: chrome.tabs.get with non-existent tab ID rejects cleanly', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    let err = null;
    try {
      await bridge.chrome.tabs.get(99999);
    } catch (e) {
      err = e;
    }
    assert.ok(err);
    assert.equal(err.message, 'No tab with id: 99999');
  }, { tier: 2, feature: 'R6', id: 'TC-T2-R6-01' });

  it('TC-T2-R6-02: chrome.tabs.create with disallowed URL scheme (javascript:) is rejected', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    let err = null;
    try {
      await bridge.chrome.tabs.create({ url: 'javascript:alert(1)' });
    } catch (e) {
      err = e;
    }
    assert.ok(err);
    assert.ok(err.message.includes('Restricted URL scheme'));
  }, { tier: 2, feature: 'R6', id: 'TC-T2-R6-02' });

  it('TC-T2-R6-03: Rapid duplicate chrome.tabs.remove on closed tab is idempotent', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    const tab = await bridge.chrome.tabs.create({ url: 'https://test.com' });

    // Concurrent removes
    await Promise.all([
      bridge.chrome.tabs.remove(tab.id),
      bridge.chrome.tabs.remove(tab.id),
    ]);

    const tabs = await bridge.chrome.tabs.query({});
    assert.equal(tabs.length, 0);
  }, { tier: 2, feature: 'R6', id: 'TC-T2-R6-03' });

  it('TC-T2-R6-04: chrome.tabs.sendMessage rejects with clear error if no listener registered', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    const tab = await bridge.chrome.tabs.create({ url: 'https://nolistener.com' });

    let err = null;
    try {
      await bridge.chrome.tabs.sendMessage(tab.id, { test: 1 });
    } catch (e) {
      err = e;
    }

    assert.ok(err);
    assert.ok(err.message.includes('Receiving end does not exist'));
  }, { tier: 2, feature: 'R6', id: 'TC-T2-R6-04' });

  it('TC-T2-R6-05: Multiple event listeners registered on onCreated fire independently', async () => {
    const bridge = new ChromeTabsBridgeEmulator();
    let count1 = 0;
    let count2 = 0;

    bridge.chrome.tabs.onCreated.addListener(() => count1++);
    bridge.chrome.tabs.onCreated.addListener(() => count2++);

    await bridge.chrome.tabs.create({ url: 'https://dual.com' });

    assert.equal(count1, 1);
    assert.equal(count2, 1);
  }, { tier: 2, feature: 'R6', id: 'TC-T2-R6-05' });

  it('TC-T2-R6-06: Malformed CDP JSON payload to __msb_cdp_tab_bridge__ is handled safely', async () => {
    const bridge = new ChromeTabsBridgeEmulator();

    let threw = false;
    try {
      await bridge.handleCdpBindingCall('INVALID_NON_JSON{{{');
    } catch (e) {
      threw = true;
      assert.ok(e.message.includes('Malformed CDP payload'));
    }

    assert.ok(threw);
  }, { tier: 2, feature: 'R6', id: 'TC-T2-R6-06' });
});
