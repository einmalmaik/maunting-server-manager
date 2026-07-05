/**
 * FileTree – recursive tree view of extracted backup contents.
 *
 * Renders folders and files with icons, human-readable sizes, and
 * expand/collapse for directories. `manifest.json` is visually highlighted
 * with a distinct badge so users can quickly spot the backup manifest
 * (VAL-EXTRACT-002, VAL-EXTRACT-005).
 *
 * Clicking a file calls `onFileSelect` with the node so the parent can load
 * a preview via the `read_text_file` Rust command.
 */

import { useState, useMemo } from 'react';
import type { FileTreeNode } from '@/lib/tauri-commands';
import { useLanguage } from '@/lib/useLanguage';

/** Convert byte count to a human-readable string (B / KB / MB). */
export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${bytes} B`;
}

/** Folder icon (chevron + folder glyph). */
function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg
      className="size-4 shrink-0 text-accent"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.8}
      aria-hidden="true"
    >
      {open ? (
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M3.75 9.776c.001-.024.001-.048 0-.072m0 0V6.75A2.25 2.25 0 016 4.5h3.879a1.5 1.5 0 011.06.44l2.122 2.12a1.5 1.5 0 001.06.44H18A2.25 2.25 0 0120.25 9v8.25A2.25 2.25 0 0118 19.5H6A2.25 2.25 0 013.75 17.25V9.776Z"
        />
      ) : (
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
        />
      )}
    </svg>
  );
}

/** File icon (document glyph). */
function FileIcon() {
  return (
    <svg
      className="size-4 shrink-0 text-muted-foreground"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.8}
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
      />
    </svg>
  );
}

/** Manifest icon – star/document to highlight manifest.json (VAL-EXTRACT-005). */
function ManifestIcon() {
  return (
    <svg
      className="size-4 shrink-0 text-warning"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.8}
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M11.48 3.499a.562.562 0 011.04 0l2.125 5.111a.563.563 0 00.475.345l5.518.442c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 00-.182.557l1.285 5.385a.562.562 0 01-.84.61l-4.725-2.885a.563.563 0 00-.586 0L6.982 20.54a.562.562 0 01-.84-.61l1.285-5.386a.562.562 0 00-.182-.557l-4.204-3.602a.563.563 0 01.321-.988l5.518-.442a.563.563 0 00.475-.345L11.48 3.5z"
      />
    </svg>
  );
}

/** Check if a node is the manifest.json file. */
function isManifest(node: FileTreeNode): boolean {
  return !node.is_dir && node.name === 'manifest.json';
}

interface TreeNodeProps {
  node: FileTreeNode;
  depth: number;
  selectedPath: string | null;
  onFileSelect: (node: FileTreeNode) => void;
}

/** Recursive tree node – handles both directory and file rendering. */
function TreeNode({ node, depth, selectedPath, onFileSelect }: TreeNodeProps) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(depth < 1); // top-level dirs open by default
  const manifest = isManifest(node);
  const selected = selectedPath === node.path;

  if (node.is_dir) {
    return (
      <div className="select-none">
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm text-foreground transition-colors hover:bg-muted/40"
          style={{ paddingLeft: `${depth * 1.1 + 0.5}rem` }}
          data-testid={`tree-node-${node.name}`}
        >
          <svg
            className={
              'size-3 shrink-0 text-muted-foreground transition-transform ' +
              (open ? 'rotate-90' : '')
            }
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2.5}
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <FolderIcon open={open} />
          <span className="truncate font-medium">{node.name}</span>
        </button>
        {open ? (
          <div className="msm-tree-children" data-testid={`tree-children-${node.name}`}>
            {node.children.map((child) => (
              <TreeNode
                key={child.path}
                node={child}
                depth={depth + 1}
                selectedPath={selectedPath}
                onFileSelect={onFileSelect}
              />
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onFileSelect(node)}
      className={
        'flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm transition-colors ' +
        (selected
          ? 'bg-primary/15 text-foreground ring-1 ring-ring/30'
          : 'text-muted-foreground hover:bg-muted/40 hover:text-foreground') +
        (manifest ? ' msm-manifest-row' : '')
      }
      style={{ paddingLeft: `${depth * 1.1 + 1.8}rem` }}
      data-testid={`tree-file-${node.name}`}
      data-manifest={manifest ? 'true' : undefined}
    >
      {manifest ? <ManifestIcon /> : <FileIcon />}
      <span className="truncate">{node.name}</span>
      {manifest ? (
        <span
          className="msm-manifest-badge"
          data-testid="manifest-badge"
        >
          {t('tree.manifest')}
        </span>
      ) : null}
      <span className="ml-auto shrink-0 text-xs text-muted-foreground/60">
        {formatFileSize(node.size)}
      </span>
    </button>
  );
}

export interface FileTreeProps {
  /** Root file tree node from the Rust `extract_tar_gz` command. */
  tree: FileTreeNode;
  /** Currently selected file path (for highlight). */
  selectedPath: string | null;
  /** Called when a file is clicked. */
  onFileSelect: (node: FileTreeNode) => void;
}

/** Top-level FileTree component. */
export function FileTree({ tree, selectedPath, onFileSelect }: FileTreeProps) {
  const { t } = useLanguage();

  // Memoize the recursive render so large trees don't re-render on every state tick.
  const renderedChildren = useMemo(
    () =>
      tree.children.map((child) => (
        <TreeNode
          key={child.path}
          node={child}
          depth={0}
          selectedPath={selectedPath}
          onFileSelect={onFileSelect}
        />
      )),
    [tree, selectedPath, onFileSelect],
  );

  return (
    <div
      className="msm-card flex flex-col gap-1 overflow-auto p-3"
      data-testid="file-tree"
      role="tree"
      aria-label={t('tree.aria')}
    >
      <p className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground/60">
        {t('tree.heading')}
      </p>
      {tree.children.length === 0 ? (
        <p className="px-2 py-4 text-center text-sm text-muted-foreground/60">
          {t('tree.empty')}
        </p>
      ) : (
        renderedChildren
      )}
    </div>
  );
}
