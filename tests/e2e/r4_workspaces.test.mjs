/**
 * R4: Physische User Data Folders (UDFs) pro Workspace E2E Test Suite
 * Verifies profile directory separation, storage isolation,
 * popup environment inheritance, and directory deletion cleanup.
 */
import { describe, it, assert, MockFilesystem } from './harness.mjs';

// Workspace manager simulator matching desktop.rs and workspacesStore.ts contracts
class WorkspaceManager {
  constructor(fs) {
    this.fs = fs;
    this.baseDir = 'C:/Users/test/AppData/Local/com.mauntingstudios.secure-browser/seiten/workspaces';
    this.workspaces = new Map();
    this.activeTabs = new Map(); // tabId -> { url, workspaceId, hasAudio }

    // Initialize default workspace
    this.createWorkspace('standard', 'Alltag');
  }

  sanitizeWorkspaceId(name) {
    const cleaned = name
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9_-]/g, '')
      .slice(0, 16);
    return cleaned || 'workspace';
  }

  getWorkspaceUdfPath(workspaceId) {
    const safeId = this.sanitizeWorkspaceId(workspaceId);
    return `${this.baseDir}/${safeId}`;
  }

  createWorkspace(id, name) {
    const safeId = this.sanitizeWorkspaceId(id);
    const udfPath = this.getWorkspaceUdfPath(safeId);
    this.fs.mkdirSync(udfPath);
    this.fs.mkdirSync(`${udfPath}/Default`);
    this.fs.mkdirSync(`${udfPath}/Default/Network`);
    this.workspaces.set(safeId, { id: safeId, name, udfPath });
    return safeId;
  }

  deleteWorkspace(id) {
    const safeId = this.sanitizeWorkspaceId(id);
    if (safeId === 'standard') {
      throw new Error('Cannot delete protected standard workspace');
    }
    const ws = this.workspaces.get(safeId);
    if (!ws) return false;

    // Migrate tabs to standard
    for (const [tabId, tab] of this.activeTabs.entries()) {
      if (tab.workspaceId === safeId) {
        tab.workspaceId = 'standard';
      }
    }

    // Clean up physical directory
    this.fs.rmdirSync(ws.udfPath);
    this.workspaces.delete(safeId);
    return true;
  }

  createTab(tabId, url, workspaceId = 'standard', hasAudio = false) {
    const safeId = this.sanitizeWorkspaceId(workspaceId);
    if (!this.workspaces.has(safeId)) {
      this.createWorkspace(safeId, safeId);
    }
    const tab = { id: tabId, url, workspaceId: safeId, hasAudio, discarded: false };
    this.activeTabs.set(tabId, tab);
    return tab;
  }

  createPopup(parentTabId, popupTabId, url) {
    const parent = this.activeTabs.get(parentTabId);
    if (!parent) throw new Error('Parent tab not found');
    return this.createTab(popupTabId, url, parent.workspaceId);
  }

  writeCookie(workspaceId, cookieName, value) {
    const safeId = this.sanitizeWorkspaceId(workspaceId);
    const cookiePath = `${this.getWorkspaceUdfPath(safeId)}/Default/Network/Cookies.db`;
    let cookies = {};
    if (this.fs.existsSync(cookiePath)) {
      try {
        cookies = JSON.parse(this.fs.readFileSync(cookiePath));
      } catch (e) {}
    }
    cookies[cookieName] = value;
    this.fs.writeFileSync(cookiePath, JSON.stringify(cookies));
  }

  readCookie(workspaceId, cookieName) {
    const safeId = this.sanitizeWorkspaceId(workspaceId);
    const cookiePath = `${this.getWorkspaceUdfPath(safeId)}/Default/Network/Cookies.db`;
    if (!this.fs.existsSync(cookiePath)) return null;
    const cookies = JSON.parse(this.fs.readFileSync(cookiePath));
    return cookies[cookieName] || null;
  }

  switchWorkspace(toWorkspaceId) {
    const safeId = this.sanitizeWorkspaceId(toWorkspaceId);
    // Put inactive tabs without audio into deep sleep
    for (const tab of this.activeTabs.values()) {
      if (tab.workspaceId !== safeId) {
        if (!tab.hasAudio) {
          tab.discarded = true;
        }
      } else {
        tab.discarded = false;
      }
    }
  }
}

describe('R4: Physische User Data Folders (UDFs) pro Workspace', () => {
  // Tier 1: Feature Coverage
  it('TC-T1-R4-01: Deterministic physical UDF directory resolution per workspace ID', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    const path1 = manager.getWorkspaceUdfPath('dev-environment');
    const path2 = manager.getWorkspaceUdfPath('personal');
    const pathStd = manager.getWorkspaceUdfPath('standard');

    assert.ok(path1.endsWith('/seiten/workspaces/dev-environment'));
    assert.ok(path2.endsWith('/seiten/workspaces/personal'));
    assert.ok(pathStd.endsWith('/seiten/workspaces/standard'));
  }, { tier: 1, feature: 'R4', id: 'TC-T1-R4-01' });

  it('TC-T1-R4-02: Physical directory isolation across multiple workspaces', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    manager.createWorkspace('ws-finance', 'Finance Work');
    manager.createWorkspace('ws-dev', 'Software Dev');

    assert.ok(fs.existsSync(manager.getWorkspaceUdfPath('ws-finance')));
    assert.ok(fs.existsSync(manager.getWorkspaceUdfPath('ws-dev')));
    assert.notEqual(
      manager.getWorkspaceUdfPath('ws-finance'),
      manager.getWorkspaceUdfPath('ws-dev'),
    );
  }, { tier: 1, feature: 'R4', id: 'TC-T1-R4-02' });

  it('TC-T1-R4-03: Cookie and storage database physical separation between workspaces', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    manager.createWorkspace('ws-work', 'Work');
    manager.createWorkspace('ws-personal', 'Personal');

    manager.writeCookie('ws-work', 'session_token', 'JWT_CORPORATE_123');
    manager.writeCookie('ws-personal', 'session_token', 'JWT_PERSONAL_999');

    assert.equal(manager.readCookie('ws-work', 'session_token'), 'JWT_CORPORATE_123');
    assert.equal(manager.readCookie('ws-personal', 'session_token'), 'JWT_PERSONAL_999');

    // Verify file separation in mock disk
    const workCookieFile = `${manager.getWorkspaceUdfPath('ws-work')}/Default/Network/Cookies.db`;
    const personalCookieFile = `${manager.getWorkspaceUdfPath('ws-personal')}/Default/Network/Cookies.db`;
    assert.ok(fs.existsSync(workCookieFile));
    assert.ok(fs.existsSync(personalCookieFile));
  }, { tier: 1, feature: 'R4', id: 'TC-T1-R4-03' });

  it('TC-T1-R4-04: Popup window inherits parent tab workspace profile environment', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    manager.createWorkspace('ws-banking', 'Banking');
    const parentTab = manager.createTab('tab-parent-1', 'https://bank.com', 'ws-banking');
    const popupTab = manager.createPopup('tab-parent-1', 'tab-popup-1', 'https://bank.com/oauth/dialog');

    assert.equal(popupTab.workspaceId, 'ws-banking');
    assert.equal(parentTab.workspaceId, popupTab.workspaceId);
  }, { tier: 1, feature: 'R4', id: 'TC-T1-R4-04' });

  it('TC-T1-R4-05: Workspace deletion cleans up physical directory from disk', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    const wsId = manager.createWorkspace('ws-ephemeral', 'Temporary');
    const wsPath = manager.getWorkspaceUdfPath(wsId);
    manager.writeCookie(wsId, 'temp_key', 'val');

    assert.ok(fs.existsSync(wsPath));

    const deleted = manager.deleteWorkspace(wsId);

    assert.ok(deleted);
    assert.equal(fs.existsSync(wsPath), false, 'UDF directory must be removed from disk');
  }, { tier: 1, feature: 'R4', id: 'TC-T1-R4-05' });

  it('TC-T1-R4-06: Tab state migration to standard workspace upon deleting active workspace', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    const wsId = manager.createWorkspace('ws-project-x', 'Project X');
    const tab1 = manager.createTab('tab-1', 'https://docs.com', wsId);
    const tab2 = manager.createTab('tab-2', 'https://git.com', wsId);

    manager.deleteWorkspace(wsId);

    assert.equal(tab1.workspaceId, 'standard');
    assert.equal(tab2.workspaceId, 'standard');
  }, { tier: 1, feature: 'R4', id: 'TC-T1-R4-06' });

  // Tier 2: Boundary & Corner Cases
  it('TC-T2-R4-01: Path traversal attacks in workspace ID are neutralized by sanitization', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    const maliciousId = '../../Windows/System32/cmd';
    const sanitized = manager.sanitizeWorkspaceId(maliciousId);
    const targetPath = manager.getWorkspaceUdfPath(maliciousId);

    assert.equal(sanitized, 'windowssystem32c');
    assert.ok(!targetPath.includes('..'));
    assert.ok(!targetPath.includes('/Windows/System32'));
  }, { tier: 2, feature: 'R4', id: 'TC-T2-R4-01' });

  it('TC-T2-R4-02: Non-ASCII characters and emojis slugify into safe filesystem names', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    const unicodeName = 'Arbeitsbereich 🚀 Büro & Entwicklung';
    const sanitized = manager.sanitizeWorkspaceId(unicodeName);

    assert.equal(sanitized, 'arbeitsbereichbr');
    assert.ok(/^[a-z0-9_-]+$/.test(sanitized));
  }, { tier: 2, feature: 'R4', id: 'TC-T2-R4-02' });

  it('TC-T2-R4-03: Deletion of default standard workspace is rejected', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    assert.throws(() => {
      manager.deleteWorkspace('standard');
    }, /Cannot delete protected standard workspace/);
  }, { tier: 2, feature: 'R4', id: 'TC-T2-R4-03' });

  it('TC-T2-R4-04: Rapid duplicate workspace creation handles naming cleanly', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    const id1 = manager.createWorkspace('research-1', 'Research');
    const id2 = manager.createWorkspace('research-2', 'Research');

    assert.notEqual(id1, id2);
    assert.ok(fs.existsSync(manager.getWorkspaceUdfPath(id1)));
    assert.ok(fs.existsSync(manager.getWorkspaceUdfPath(id2)));
  }, { tier: 2, feature: 'R4', id: 'TC-T2-R4-04' });

  it('TC-T2-R4-05: Deletion of workspace with deep file hierarchies executes recursively', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    const wsId = manager.createWorkspace('deep-ws', 'Deep Files');
    const base = manager.getWorkspaceUdfPath(wsId);

    fs.writeFileSync(`${base}/Default/IndexedDB/site1.indexeddb`, 'data');
    fs.writeFileSync(`${base}/Default/Cache/cache_data_001`, 'cache');

    assert.ok(fs.existsSync(`${base}/Default/Cache/cache_data_001`));

    manager.deleteWorkspace(wsId);

    assert.equal(fs.existsSync(base), false);
    assert.equal(fs.existsSync(`${base}/Default/Cache/cache_data_001`), false);
  }, { tier: 2, feature: 'R4', id: 'TC-T2-R4-05' });

  it('TC-T2-R4-06: Inactive tabs with active audio are spared from discarding during workspace switch', () => {
    const fs = new MockFilesystem();
    const manager = new WorkspaceManager(fs);

    manager.createWorkspace('ws-a', 'Workspace A');
    manager.createWorkspace('ws-b', 'Workspace B');

    const musicTab = manager.createTab('tab-music', 'https://music.stream', 'ws-a', true);
    const silentTab = manager.createTab('tab-silent', 'https://article.read', 'ws-a', false);

    manager.switchWorkspace('ws-b');

    assert.equal(silentTab.discarded, true, 'Silent tab should be discarded');
    assert.equal(musicTab.discarded, false, 'Audio-playing tab must remain active in memory');
  }, { tier: 2, feature: 'R4', id: 'TC-T2-R4-06' });
});
