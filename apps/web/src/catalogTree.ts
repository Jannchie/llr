// The folder tree as the browser sees it. The API hands over a flat list of
// folders (each with its direct photo count); these pure helpers turn that
// into the nested, sorted structure the sidebar renders, and answer the two
// questions drag-and-drop asks: what is under this folder, and where is it.

export type FolderRow = {
  id: number;
  parentId: number | null;
  name: string;
  /** Photos directly in the folder. */
  count: number;
};

export type FolderNode = FolderRow & {
  depth: number;
  children: FolderNode[];
  /** Photos in the folder and every folder below it. */
  total: number;
};

const byName = (a: FolderRow, b: FolderRow) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });

/** Nest `rows` under their parents; roots are the rows whose parent is missing. */
export function buildTree(rows: FolderRow[]): FolderNode[] {
  const nodes = new Map<number, FolderNode>();
  for (const r of rows) nodes.set(r.id, { ...r, depth: 0, children: [], total: r.count });
  const roots: FolderNode[] = [];
  for (const n of nodes.values()) {
    const parent = n.parentId == null ? undefined : nodes.get(n.parentId);
    if (parent) parent.children.push(n);
    else roots.push(n);
  }
  const finish = (n: FolderNode, depth: number): number => {
    n.depth = depth;
    n.children.sort(byName);
    for (const c of n.children) n.total += finish(c, depth + 1);
    return n.total;
  };
  roots.sort(byName);
  for (const r of roots) finish(r, 0);
  return roots;
}

/** Depth-first flattening of the expanded part of the tree — one row per visible folder. */
export function visibleRows(roots: FolderNode[], expanded: ReadonlySet<number>): FolderNode[] {
  const out: FolderNode[] = [];
  const walk = (n: FolderNode) => {
    out.push(n);
    if (expanded.has(n.id)) for (const c of n.children) walk(c);
  };
  for (const r of roots) walk(r);
  return out;
}

/** `id` and every folder below it. */
export function descendantIds(rows: FolderRow[], id: number): Set<number> {
  const children = new Map<number, number[]>();
  for (const r of rows) {
    if (r.parentId == null) continue;
    let list = children.get(r.parentId);
    if (!list) children.set(r.parentId, list = []);
    list.push(r.id);
  }
  const out = new Set<number>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop()!;
    if (out.has(cur)) continue;
    out.add(cur);
    for (const c of children.get(cur) ?? []) stack.push(c);
  }
  return out;
}

/** Ancestors of `id` from the root down, ending with `id` itself. */
export function pathOf(rows: FolderRow[], id: number): FolderRow[] {
  const byId = new Map(rows.map(r => [r.id, r]));
  const out: FolderRow[] = [];
  let cur = byId.get(id);
  const seen = new Set<number>();
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    out.unshift(cur);
    cur = cur.parentId == null ? undefined : byId.get(cur.parentId);
  }
  return out;
}
