import { computed, onBeforeUnmount, ref, watch } from "vue";
import { fetchModelStatus, type ModelStatus } from "../api";
import { t } from "../i18n";

/**
 * A per-decode analysis the worker makes with a model of its own (relight
 * geometry, AI mattes): fetched once per frame and only while `wanted`, with
 * the model downloaded first if it is missing (once per failure: a failed
 * download waits for the user) and polled until it lands.
 */
export function useWorkerAnalysis<T>(opts: {
  model: "relight" | "ai";
  /** The frame the result would belong to; null when there is nothing to analyse. */
  key: () => string | null;
  wanted: () => boolean;
  /** Whether the result for `key` is already in hand. */
  has: (key: string) => boolean;
  fetch: () => Promise<T | null>;
  /** Hand over a result, still current, for `key`. */
  apply: (key: string, value: T) => void;
}) {
  const busy = ref(false);
  const error = ref<string | null>(null);
  const status = ref<ModelStatus | null>(null);
  let inflight: Promise<void> | null = null;
  let poll: number | undefined;

  async function ensure(): Promise<void> {
    const key = opts.key();
    if (key === null || !opts.wanted() || opts.has(key)) return;
    if (inflight) return inflight;
    inflight = (async () => {
      busy.value = true;
      error.value = null;
      try {
        let st = await fetchModelStatus(opts.model);
        if (st.reason === "models-missing" && !st.detail) st = await fetchModelStatus(opts.model, true);
        status.value = st;
        window.clearTimeout(poll);
        if (!st.available) {
          if (st.reason === "downloading") poll = window.setTimeout(() => void ensure(), 1000);
          return;
        }
        const value = await opts.fetch();
        // A different photo (or a different erase) took over while this ran.
        if (value && opts.key() === key) opts.apply(key, value);
      } catch (err) {
        error.value = err instanceof Error ? err.message : String(err);
      } finally {
        busy.value = false;
        inflight = null;
      }
    })();
    return inflight;
  }

  watch(opts.wanted, on => { if (on) void ensure(); });
  onBeforeUnmount(() => window.clearTimeout(poll));

  const notice = computed(() => {
    const st = status.value;
    if (error.value) return t(`${opts.model}.failed`, { detail: error.value });
    if (st?.reason === "downloading") {
      const got = st.received ?? 0, total = st.total ?? 0;
      return t(`${opts.model}.downloading`, { pct: total ? Math.floor((100 * got) / total) : 0 });
    }
    if (st && !st.available) return t(`${opts.model}.unavailable`, { detail: st.detail ?? st.reason ?? "" });
    if (busy.value) return t(`${opts.model}.analyzing`);
    return null;
  });

  return { busy, error, status, notice, ensure };
}
