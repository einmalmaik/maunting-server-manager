/**
 * FilePreview component tests (VAL-EXTRACT-003, VAL-EXTRACT-004).
 *
 * Verifies that text files show content and JSON files show formatted content
 * with syntax highlighting.
 */

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { LanguageProvider } from '@/lib/useLanguage';
import { FilePreview } from './FilePreview';
import type { FileTreeNode } from '@/lib/tauri-commands';

afterEach(() => {
  cleanup();
});

const TXT_FILE: FileTreeNode = {
  name: 'readme.txt',
  path: '/tmp/readme.txt',
  is_dir: false,
  size: 20,
  children: [],
};

const JSON_FILE: FileTreeNode = {
  name: 'data.json',
  path: '/tmp/data.json',
  is_dir: false,
  size: 50,
  children: [],
};

const BINARY_FILE: FileTreeNode = {
  name: 'image.png',
  path: '/tmp/image.png',
  is_dir: false,
  size: 1024,
  children: [],
};

describe('VAL-EXTRACT-003: text file preview shows content', () => {
  it('shows empty message when no file is selected', () => {
    render(
      <LanguageProvider>
        <FilePreview file={null} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('file-preview').textContent).toContain('Wählen Sie');
  });

  it('loads and displays text file content', async () => {
    const mockRead = vi.fn().mockResolvedValue('Hello, World!\nSecond line.');
    render(
      <LanguageProvider>
        <FilePreview file={TXT_FILE} readFileFn={mockRead} />
      </LanguageProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('preview-text')).toBeDefined();
    });
    expect(screen.getByTestId('preview-text').textContent).toContain('Hello, World!');
    expect(screen.getByTestId('preview-text').textContent).toContain('Second line.');
    expect(mockRead).toHaveBeenCalledWith('/tmp/readme.txt');
  });

  it('shows loading state while fetching', async () => {
    const mockRead = vi.fn().mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve('content'), 100)),
    );
    render(
      <LanguageProvider>
        <FilePreview file={TXT_FILE} readFileFn={mockRead} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('preview-loading')).toBeDefined();
    await waitFor(() => {
      expect(screen.getByTestId('preview-text')).toBeDefined();
    });
  });

  it('shows error message on read failure', async () => {
    const mockRead = vi.fn().mockRejectedValue('Datei nicht lesbar');
    render(
      <LanguageProvider>
        <FilePreview file={TXT_FILE} readFileFn={mockRead} />
      </LanguageProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('preview-error')).toBeDefined();
    });
    expect(screen.getByTestId('preview-error').textContent).toContain('nicht lesbar');
  });
});

describe('VAL-EXTRACT-004: JSON file preview shows formatted content', () => {
  it('displays formatted (pretty-printed) JSON', async () => {
    const minified = '{"name":"test","value":42,"active":true,"items":[1,2,3]}';
    const mockRead = vi.fn().mockResolvedValue(minified);
    const { container } = render(
      <LanguageProvider>
        <FilePreview file={JSON_FILE} readFileFn={mockRead} />
      </LanguageProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('preview-json')).toBeDefined();
    });
    // Pretty-printed JSON is rendered as multiple .json-line divs (one per line)
    const lines = container.querySelectorAll('.json-line');
    expect(lines.length).toBeGreaterThan(1);
    // Each line should contain some content (indentation or key-value)
    const allText = Array.from(lines).map((l) => l.textContent ?? '').join('\n');
    expect(allText).toContain('name');
    expect(allText).toContain('test');
    expect(allText).toContain('42');
  });

  it('shows JSON badge for valid JSON files', async () => {
    const mockRead = vi.fn().mockResolvedValue('{"key":"value"}');
    render(
      <LanguageProvider>
        <FilePreview file={JSON_FILE} readFileFn={mockRead} />
      </LanguageProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('preview-json-badge')).toBeDefined();
    });
    expect(screen.getByTestId('preview-json-badge').textContent).toBe('JSON');
  });

  it('syntax highlights JSON keys, strings, and numbers', async () => {
    const json = '{"name":"test","count":42}';
    const mockRead = vi.fn().mockResolvedValue(json);
    const { container } = render(
      <LanguageProvider>
        <FilePreview file={JSON_FILE} readFileFn={mockRead} />
      </LanguageProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('preview-json')).toBeDefined();
    });
    // Check that syntax highlight classes are applied
    expect(container.querySelector('.json-key')).not.toBeNull();
    expect(container.querySelector('.json-string')).not.toBeNull();
    expect(container.querySelector('.json-number')).not.toBeNull();
  });

  it('handles invalid JSON gracefully (shows raw text)', async () => {
    const invalidJson = 'not valid json {{{';
    const mockRead = vi.fn().mockResolvedValue(invalidJson);
    render(
      <LanguageProvider>
        <FilePreview file={JSON_FILE} readFileFn={mockRead} />
      </LanguageProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('preview-json')).toBeDefined();
    });
    // Should still render the raw content even if JSON parsing fails
    expect(screen.getByTestId('preview-json').textContent).toContain('not valid json');
    // Should NOT show the JSON badge for invalid JSON
    expect(screen.queryByTestId('preview-json-badge')).toBeNull();
  });
});

describe('binary file handling', () => {
  it('shows binary message for non-text files', () => {
    render(
      <LanguageProvider>
        <FilePreview file={BINARY_FILE} readFileFn={vi.fn()} />
      </LanguageProvider>,
    );
    expect(screen.getByTestId('preview-error').textContent).toContain('binär');
    // Should not call readTextFile for binary files
  });
});
