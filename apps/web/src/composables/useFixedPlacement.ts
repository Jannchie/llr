import { nextTick, onBeforeUnmount, ref, type Ref } from "vue";

/**
 * Where a floating popup goes: under its trigger, flipped above when that side
 * has more room, and pulled back in off the window's edges. The popup is fixed,
 * so it also has to be re-placed whenever the page scrolls or the window
 * changes size — otherwise it parts company with the control it belongs to.
 *
 * The popup must already be in the DOM (v-if="open") before `show()`: its width
 * is measured to decide the left edge, and `placed` stays false until then so
 * the popup can fade in where it actually landed instead of flashing.
 */
export function useFixedPlacement(
  trigger: Ref<HTMLElement | null>,
  list: Ref<HTMLElement | null>,
  opts: { maxHeight?: number; minHeight?: number; gap?: number; edge?: number } = {},
) {
  const GAP = opts.gap ?? 4;      // between trigger and popup
  const EDGE = opts.edge ?? 8;    // keep off the window edges
  const MAX_H = opts.maxHeight ?? 420;  // ~14 rows: the longest list here (aspect ratios) is 13
  const MIN_H = opts.minHeight ?? 96;

  const placed = ref(false);
  const style = ref<Record<string, string>>({});

  function place(): void {
    const t = trigger.value;
    const l = list.value;
    if (!t || !l) return;
    const r = t.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - GAP - EDGE;
    const above = r.top - GAP - EDGE;
    // Prefer dropping down; flip up only when that genuinely buys more room.
    const up = below < Math.min(MAX_H, above) && above > below;
    const next: Record<string, string> = {
      minWidth: `${r.width}px`,
      maxHeight: `${Math.max(MIN_H, Math.min(MAX_H, up ? above : below))}px`,
    };
    if (up) next.bottom = `${window.innerHeight - r.top + GAP}px`;
    else next.top = `${r.bottom + GAP}px`;
    // Width is content-driven (a label can be wider than the trigger), so the
    // left edge is only known after a measure — pull it back in off the window.
    const w = l.offsetWidth;
    next.left = `${Math.max(EDGE, Math.min(r.left, window.innerWidth - EDGE - w))}px`;
    style.value = next;
  }

  // Called as the popup opens. Capture, because an ancestor scrolling (the
  // settings rail) does not bubble.
  async function show(): Promise<void> {
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    await nextTick();
    place();
    placed.value = true;
  }

  function hide(): void {
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
    placed.value = false;
  }

  onBeforeUnmount(hide);
  return { placed, style, place, show, hide };
}
