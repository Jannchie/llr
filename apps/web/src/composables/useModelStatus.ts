import { onBeforeUnmount, ref } from "vue";
import { fetchModelStatus, type ModelStatus, type WorkerModel } from "../api";

/**
 * Whether the worker can run one of its models, kept current. The model's
 * life is the worker's — missing → downloading (polled here once a second) →
 * available, or a failed download (`models-missing` with a `detail`) that
 * waits for the user to retry, or a runtime that cannot load it — and this is
 * the one place the browser follows it:
 *
 * - A plain refresh starts the download of a model that is simply missing, but
 *   not of one whose last download failed; `refresh(true)` is the retry.
 * - `onAvailable` fires on the transition into available (not on a first
 *   answer that already is), which is when work that waited on it can run.
 * - Polling stops once `keepPolling` says nobody is watching; the next
 *   refresh picks it up again.
 * - `refresh` never throws: an unreachable worker keeps the last answer.
 */
export function useModelStatus(model: WorkerModel, opts: { onAvailable?: () => void; keepPolling?: () => boolean } = {}) {
  const status = ref<ModelStatus | null>(null);
  let poll: number | undefined;

  async function refresh(download = false): Promise<ModelStatus | null> {
    let st: ModelStatus;
    try {
      st = await fetchModelStatus(model, download);
      if (st.reason === "models-missing" && !st.detail && !download) st = await fetchModelStatus(model, true);
    } catch {
      return status.value;
    }
    const was = status.value;
    status.value = st;
    window.clearTimeout(poll);
    if (st.reason === "downloading" && (opts.keepPolling?.() ?? true)) poll = window.setTimeout(() => void refresh(), 1000);
    if (st.available && was && !was.available) opts.onAvailable?.();
    return st;
  }

  onBeforeUnmount(() => window.clearTimeout(poll));
  return { status, refresh };
}
