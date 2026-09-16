<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { clamp } from "../ui";

// A right-click menu, at the pointer: the file-manager half of the grid. The
// caller owns what is in it (`menu.items`, already labelled and localized) and
// what a pick means (`select`); this component owns where it lands, which row
// the keyboard is on, and when it goes away.
//
// One level of submenu is enough for what the grid asks of it ("move to…"),
// and it is opened by hover or Right, entered by Down/Up, and left by Left or
// Escape — the keys a menu is expected to answer to. It closes on a click
// anywhere else, on scroll or resize (a fixed menu would part company with the
// cells it points at) and on the window losing focus.

/** One row. A row whose id is `-` is a rule between groups of items. */
export type MenuItem = {
  id: string;
  label?: string;
  disabled?: boolean;
  danger?: boolean;
  children?: MenuItem[];
};

const props = defineProps<{
  /** Where the menu is, and what is in it; null when there is no menu. */
  menu: { x: number; y: number; items: MenuItem[] } | null;
}>();

const emit = defineEmits<{
  (e: "select", id: string): void;
  (e: "close"): void;
}>();

const EDGE = 8;

const rootEl = ref<HTMLDivElement | null>(null);
const subEl = ref<HTMLDivElement | null>(null);
const placed = ref(false);
const subPlaced = ref(false);
// The menu's own box, measured once when it opens: placing a submenu off it is
// then arithmetic instead of another forced layout.
let rootBox: DOMRect | null = null;
const style = ref<Record<string, string>>({});
const subStyle = ref<Record<string, string>>({});
// The keyboard cursor: a row of the menu, or, once a submenu is open, of that.
const active = ref(-1);
const subOpen = ref(-1);
const subActive = ref(-1);

const items = computed(() => props.menu?.items ?? []);
const subItems = computed(() => (subOpen.value >= 0 ? items.value[subOpen.value]?.children ?? [] : []));

function enabled(list: MenuItem[], from: number, dir: 1 | -1): number {
  for (let i = from; i >= 0 && i < list.length; i += dir) {
    if (!list[i].disabled) return i;
  }
  return -1;
}

const firstEnabled = (list: MenuItem[]) => enabled(list, 0, 1);

/* ---------- placement ---------- */

// Measured after mount: the menu's width is content-driven, so how much has to
// be pulled back off the right (and bottom) edge is only known once it is up.
async function place(): Promise<void> {
  const anchor = props.menu;
  if (!anchor) return;
  // The watcher is pre-flush, so the menu is not in the DOM yet: the element
  // has to be looked up once the render it triggers has gone through.
  await nextTick();
  const el = rootEl.value;
  if (!el) return;
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  style.value = {
    left: `${clamp(anchor.x, EDGE, window.innerWidth - EDGE - w)}px`,
    top: `${clamp(anchor.y, EDGE, window.innerHeight - EDGE - h)}px`,
    maxHeight: `${Math.max(120, window.innerHeight - 2 * EDGE)}px`,
  };
  closeSub();
  rootBox = el.getBoundingClientRect();
  placed.value = true;
}

// A submenu sits beside its row, flipped to the left when it would run off the
// window — the row it belongs to never moves either way. It is fixed like the
// menu it hangs off rather than a child of it: a menu long enough to scroll
// would otherwise clip it. `rowTop` is the row's top when the pointer opened it;
// the keyboard opens submenus too, and then the row is looked up.
async function placeSub(rowTop: number | null): Promise<void> {
  await nextTick();
  const sub = subEl.value;
  if (!sub || !rootBox) return;
  const r = rootBox;
  const top = rowTop ?? rootEl.value?.querySelectorAll<HTMLElement>(".ctx-item")[subOpen.value]?.getBoundingClientRect().top ?? r.top;
  const w = sub.offsetWidth;
  const h = sub.offsetHeight;
  const flip = window.innerWidth - EDGE - (r.right + 2) < w && r.left - 2 - w > EDGE;
  subStyle.value = {
    top: `${clamp(top - 7, EDGE, window.innerHeight - EDGE - h)}px`,
    left: flip ? `${Math.max(EDGE, r.left - 2 - w)}px` : `${r.right + 2}px`,
    maxHeight: `${Math.max(120, window.innerHeight - 2 * EDGE)}px`,
  };
  subPlaced.value = true;
}

function close(): void {
  emit("close");
}

/* ---------- keyboard and pointer ---------- */

function onKeyDown(e: KeyboardEvent): void {
  const inSub = subOpen.value >= 0;
  const list = inSub ? subItems.value : items.value;
  const cursor = inSub ? subActive : active;
  switch (e.key) {
    case "Escape":
      e.preventDefault();
      if (inSub) closeSub();
      else close();
      return;
    case "ArrowDown":
    case "ArrowUp": {
      e.preventDefault();
      const next = enabled(list, cursor.value + (e.key === "ArrowDown" ? 1 : -1), e.key === "ArrowDown" ? 1 : -1);
      if (next < 0) return;
      cursor.value = next;
      return;
    }
    case "Home":
    case "End": {
      e.preventDefault();
      const next = e.key === "Home" ? firstEnabled(list) : enabled(list, list.length - 1, -1);
      if (next >= 0) cursor.value = next;
      return;
    }
    case "ArrowRight": {
      if (inSub) return;
      e.preventDefault();
      openSub(active.value);
      return;
    }
    case "ArrowLeft": {
      if (!inSub) return;
      e.preventDefault();
      closeSub();
      return;
    }
    case "Enter":
    case " ": {
      e.preventDefault();
      const item = list[cursor.value];
      if (!item || item.disabled) return;
      if (!inSub && item.children?.length) openSub(cursor.value);
      else pick(item);
      return;
    }
    default:
  }
}

function openSub(i: number, rowTop: number | null = null): void {
  const item = items.value[i];
  if (!item || item.disabled || !item.children?.length) {
    closeSub();
    return;
  }
  active.value = i;
  subOpen.value = i;
  subActive.value = firstEnabled(item.children);
  void placeSub(rowTop);
}

function closeSub(): void {
  if (subOpen.value < 0) return;
  subOpen.value = -1;
  subActive.value = -1;
  subStyle.value = {};
  subPlaced.value = false;
}

// The pointer entered a row: move the cursor onto it and open its submenu (or
// close the one that was open). The row comes from the event, so placing the
// submenu needs no DOM query.
function hover(i: number, target: EventTarget | null): void {
  if (items.value[i]?.disabled) return;
  if (subOpen.value !== i) openSub(i, (target as HTMLElement | null)?.getBoundingClientRect().top ?? null);
  else active.value = i;
}

function pick(item: MenuItem): void {
  if (item.disabled) return;
  close();
  emit("select", item.id);
}

// The menu is opened by a contextmenu event, whose default action (and the
// pointerdown that follows on some platforms) would close it again or start a
// text selection under it.
function onDocPointerDown(e: PointerEvent): void {
  const target = e.target as Node | null;
  // Both boxes count as inside: the submenu is a sibling of the menu, not a
  // child of it, so a press on a folder there would otherwise close the menu
  // out from under the click.
  if (target && (rootEl.value?.contains(target) || subEl.value?.contains(target))) return;
  close();
}

let removeWindow: (() => void) | null = null;

watch(() => props.menu, (menu) => {
  removeWindow?.();
  removeWindow = null;
  placed.value = false;
  closeSub();
  active.value = -1;
  if (!menu) return;
  void place().then(() => {
    active.value = firstEnabled(menu.items);
    rootEl.value?.focus();
  });
  const onScrollOrResize = () => close();
  window.addEventListener("resize", onScrollOrResize);
  // Capture: an ancestor scrolling (the grid) does not bubble.
  window.addEventListener("scroll", onScrollOrResize, true);
  document.addEventListener("pointerdown", onDocPointerDown, true);
  window.addEventListener("blur", onScrollOrResize);
  removeWindow = () => {
    window.removeEventListener("resize", onScrollOrResize);
    window.removeEventListener("scroll", onScrollOrResize, true);
    document.removeEventListener("pointerdown", onDocPointerDown, true);
    window.removeEventListener("blur", onScrollOrResize);
  };
}, { immediate: true });

onBeforeUnmount(() => removeWindow?.());
</script>

<template>
  <!-- Body-level so the grid's scrolling and stacking contexts cannot clip it. -->
  <Teleport to="body">
    <div v-if="menu" ref="rootEl" class="ctx-menu" :class="{ 'is-placed': placed }" :style="style"
      role="menu" tabindex="-1" @keydown="onKeyDown" @contextmenu.prevent>
      <template v-for="(item, i) in items" :key="`${i}:${item.id}`">
        <div v-if="item.id === '-'" class="ctx-sep" role="separator" />
        <div v-else class="ctx-item" :class="{ 'is-active': i === active, 'is-danger': item.danger, 'is-disabled': item.disabled }"
          role="menuitem" :aria-disabled="item.disabled || undefined" :aria-haspopup="item.children?.length ? 'menu' : undefined"
          @pointerenter="hover(i, $event.currentTarget)" @click="item.children?.length ? openSub(i) : pick(item)">
          <span class="ctx-label">{{ item.label }}</span>
          <svg v-if="item.children?.length" class="ctx-arrow" viewBox="0 0 6 10" aria-hidden="true">
            <path d="M1 1l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
              stroke-linejoin="round" />
          </svg>
        </div>
      </template>
    </div>
    <!-- A sibling of the menu, not a child: the menu scrolls when it is long. -->
    <div v-if="menu && subOpen >= 0 && subItems.length" ref="subEl" class="ctx-menu ctx-sub"
      :class="{ 'is-placed': subPlaced }" :style="subStyle" role="menu" @keydown="onKeyDown">
      <template v-for="(item, i) in subItems" :key="`${i}:${item.id}`">
        <div v-if="item.id === '-'" class="ctx-sep" role="separator" />
        <div v-else class="ctx-item" :class="{ 'is-active': i === subActive, 'is-disabled': item.disabled }"
          role="menuitem" :aria-disabled="item.disabled || undefined"
          @pointerenter="!item.disabled && (subActive = i)" @click="pick(item)">
          <span class="ctx-label">{{ item.label }}</span>
        </div>
      </template>
    </div>
  </Teleport>
</template>
