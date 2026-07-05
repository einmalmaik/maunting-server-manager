/**
 * FileTree component tests (VAL-EXTRACT-002, VAL-EXTRACT-005).
 *
 * Verifies that the file tree renders folders and files with sizes,
 * supports expand/collapse, and highlights manifest.json specially.
 */

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { LanguageProvider } from '@/lib/useLanguage';
import { FileTree, formatFileSize } from './FileTree';
import type { FileTreeNode } from '@/lib/tauri-commands';

const MOCK_TREE: FileTreeNode = {
  name: 'root',
  path: '/tmp/root',
  is_dir: true,
  size: 0,
  children: [
    { name: 'a.txt', path: '/tmp/root/a.txt', is_dir: false, size: 2048, children: [] },
    {
      name: 'sub',
      path: '/tmp/root/sub',
      is_dir: true,
      size: 0,
      children: [
        { name: 'b.json', path: '/tmp/root/sub/b.json', is_dir: false, size: 42, children: [] },
        { name: 'manifest.json', path: '/tmp/root/sub/manifest.json', is_dir: false, size: 128, children: [] },
      ],
    },
  ],
};

afterEach(() => {
  cleanup();
});

describe('VAL-EXTRACT-002: File tree renders folders and files with sizes', () => {
  it('renders the tree container', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('file-tree')).toBeDefined();
  });

  it('renders folder nodes', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('tree-node-sub')).toBeDefined();
  });

  it('renders file nodes with size labels', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    const aTxt = screen.getByTestId('tree-file-a.txt');
    expect(aTxt.textContent).toContain('2.0 KB');
  });

  it('folders are expandable (top-level open by default)', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    // sub/ is top-level, should be open, so b.json should be visible
    expect(screen.getByTestId('tree-file-b.json')).toBeDefined();
  });

  it('collapsing a folder hides its children', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    // Click the sub folder to collapse
    fireEvent.click(screen.getByTestId('tree-node-sub'));
    expect(screen.queryByTestId('tree-file-b.json')).toBeNull();
  });

  it('clicking a file calls onFileSelect', () => {
    const onSelect = vi.fn();
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={onSelect} />
      </LanguageProvider>,
    );
    fireEvent.click(screen.getByTestId('tree-file-a.txt'));
    expect(onSelect).toHaveBeenCalledOnce();
    const selectedNode = onSelect.mock.calls[0][0] as FileTreeNode;
    expect(selectedNode.name).toBe('a.txt');
    expect(selectedNode.path).toBe('/tmp/root/a.txt');
  });

  it('shows empty message when tree has no children', () => {
    const emptyTree: FileTreeNode = {
      name: 'root',
      path: '/tmp/empty',
      is_dir: true,
      size: 0,
      children: [],
    };
    render(
      <LanguageProvider>
        <FileTree tree={emptyTree} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('file-tree').textContent).toContain('Keine Dateien');
  });
});

describe('VAL-EXTRACT-005: manifest.json is highlighted specially', () => {
  it('manifest.json has a data-manifest attribute', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    const manifestEl = screen.getByTestId('tree-file-manifest.json');
    expect(manifestEl.getAttribute('data-manifest')).toBe('true');
  });

  it('manifest.json has a manifest badge', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('manifest-badge')).toBeDefined();
    expect(screen.getByTestId('manifest-badge').textContent).toContain('Manifest');
  });

  it('non-manifest files do not have data-manifest attribute', () => {
    render(
      <LanguageProvider>
        <FileTree tree={MOCK_TREE} selectedPath={null} onFileSelect={() => {}} />
      </LanguageProvider>,
    );
    const bJson = screen.getByTestId('tree-file-b.json');
    expect(bJson.getAttribute('data-manifest')).toBeNull();
  });
});

describe('formatFileSize', () => {
  it('formats bytes', () => {
    expect(formatFileSize(0)).toBe('0 B');
    expect(formatFileSize(512)).toBe('512 B');
  });

  it('formats kilobytes', () => {
    expect(formatFileSize(1024)).toBe('1.0 KB');
    expect(formatFileSize(2048)).toBe('2.0 KB');
  });

  it('formats megabytes', () => {
    expect(formatFileSize(1024 * 1024)).toBe('1.00 MB');
    expect(formatFileSize(1024 * 1024 * 5)).toBe('5.00 MB');
  });
});
