/**
 * A static binary asset under public/, fetched at most once per page and
 * shared by every consumer — the preview's renderer and the off-screen one
 * each export builds, say. The tables that come this way (Sony's 3-D LUT, the
 * tone operator family) are the engine's rather than a shot's, which is why
 * they are assets and not something the decode carries.
 */
export class StaticAsset<T> {
  private data: T | null = null;
  private pending: Promise<T | null> | null = null;

  /**
   * `parse` turns the bytes into the table, or returns null for bytes that
   * are not its shape. `label` names the stage in the one warning a failed
   * fetch logs — after which `load` keeps answering null, so the stage stays
   * inert with a single line rather than one per redraw.
   */
  constructor(private readonly url: string, private readonly parse: (buf: ArrayBuffer) => T | null,
              private readonly label: string) {}

  /** The table if a fetch has landed; the synchronous form for callers that cannot wait. */
  get loaded(): T | null {
    return this.data;
  }

  load(): Promise<T | null> {
    if (this.data) return Promise.resolve(this.data);
    this.pending ??= fetch(`${import.meta.env.BASE_URL}${this.url}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const parsed = this.parse(await res.arrayBuffer());
        if (!parsed) throw new Error("not the table's shape");
        this.data = parsed;
        return parsed;
      })
      .catch((err: unknown) => {
        console.warn(`[llr] ${this.label} unavailable:`, err);
        return null;
      });
    return this.pending;
  }
}
