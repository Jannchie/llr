// Minimal typed i18n. `en` is the source of truth: its keys define MessageKey,
// so every other locale is a Record<MessageKey, string> and a missing or
// misspelled translation is a vue-tsc error rather than a key leaking into the
// UI at runtime.
import { ref } from "vue";

export type Locale = "en" | "zh";
export const LOCALES: { value: Locale; label: string }[] = [
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
];

const en = {
  // Top bar
  "action.undo": "Undo (Ctrl+Z)",
  "action.redo": "Redo (Ctrl+Shift+Z)",
  "action.crop": "Crop & Straighten (R)",
  "action.compareOriginal": "Hold to compare original ( \\ )",
  "action.compareJpeg": "Hold to compare camera JPEG ( | )",
  "aria.undo": "Undo",
  "aria.redo": "Redo",
  "aria.crop": "Crop",
  "aria.compareOriginal": "Compare with original",
  "aria.compareJpeg": "Compare with camera JPEG",
  "aria.cameraJpeg": "Camera JPEG preview",
  "meta.noImage": "No image loaded",
  "action.export": "Export",
  "action.exporting": "Exporting…",

  // Viewport
  "aria.preview": "Preview",
  "dropzone.title": "Drop a RAW file to start",
  "dropzone.sub": "or click anywhere to browse",
  "drag.overlay": "Drop to import",
  "toast.dismiss": "Click to dismiss",
  "invalid.missingTitle": "Source file no longer available",
  "invalid.missingSub": "The server cache may have been cleared — re-import this photo",
  "invalid.webglTitle": "WebGL2 unavailable",
  "invalid.webglSub": "LLR renders entirely on the GPU — enable hardware acceleration or use a browser with WebGL2 support",

  // Status bar
  "status.label": "Status",
  "status.decoding": "Decoding…",
  "status.importing": "Importing…",
  "status.decodedIn": "Decoded in {ms}ms",
  "zoom.fit": "Fit",

  // Crop panel
  "panel.crop": "Crop & Straighten",
  "crop.aspect": "Aspect",
  "crop.custom": "Custom…",
  "crop.swap": "Swap orientation (X)",
  "aria.swapAspect": "Swap aspect",
  "crop.ratio": "Ratio",
  "crop.angle": "Angle",
  "crop.rotateLeft": "Rotate left 90°",
  "crop.rotateRight": "Rotate right 90°",
  "crop.flipH": "Flip horizontal",
  "crop.flipV": "Flip vertical",
  "crop.done": "Done",
  "aspect.free": "Free",
  "aspect.orig": "Original",

  // Settings panel
  "panel.settings": "Settings",
  "settings.language": "Language",
  "settings.engine": "Color Engine",
  "settings.engineHint": "Adobe renders through a DCP profile; Sony reproduces Imaging Edge from calibration inside the RAW (Sony bodies only)",
  "engine.adobe": "Adobe (DCP)",
  "engine.sony": "Sony Imaging Edge",
  "engine.sonyUnavailable": "This shot fell back to the DCP profile: Sepia is toned in a stage this pipeline does not reproduce, and a non-Sony RAW carries no Sony calibration at all.",
  "settings.creativeLook": "Creative Look",
  "settings.dcp": "DCP Style",
  "settings.cameraMatch": "Camera Match",
  "settings.cameraMatchHint": "Correct the profile toward this camera's own JPEG rendering",
  "settings.display": "Display",
  "display.p3": "Display-P3 (wide)",
  "settings.exportExif": "Export EXIF",
  "exif.hint": "Full keeps everything from the RAW; Private-safe strips GPS, serial numbers, owner name, and maker notes",
  "exif.full": "Full metadata",
  "exif.private": "Private-safe (no GPS/serials)",

  // Slider groups
  "panel.tone": "Tone",
  "panel.presence": "Presence",
  "panel.color": "Color",
  "panel.lens": "Lens Corrections",
  "slider.exposure": "Exposure",
  "slider.contrast": "Contrast",
  "slider.highlights": "Highlights",
  "slider.shadows": "Shadows",
  "slider.whites": "Whites",
  "slider.blacks": "Blacks",
  "slider.clarity": "Clarity",
  "slider.dehaze": "Dehaze",
  "slider.temperature": "Temp",
  "slider.tint": "Tint",
  "slider.vibrance": "Vibrance",
  "slider.saturation": "Saturation",
  "slider.lensDistortion": "Distortion",
  "slider.lensVignetting": "Vignetting",
  "slider.hint": "Click to select, then scroll to nudge (Shift ×10) · Double-click to reset",

  // The shot's in-camera Creative Look tweaks (Sony engine only). Named apart
  // from the Tone sliders on purpose: these drive the camera's own stages and
  // start at what the body recorded, not at zero.
  "panel.creativeLook": "Creative Look",
  // The look names themselves are not here: they are Sony's own menu entries
  // ("ST Standard"), shown untranslated so the picker reads like the body's
  // menu. See STYLE_NAMES in App.vue.
  "look.asShotSuffix": " · as shot",
  "look.borrowedHint": "This look is newer than the camera. Its tone curve is Sony's own, identical across bodies; its colour comes from a body that ships it.",
  "look.sharpening": "Sharpening",
  "look.sharpeningValue": "{level} · range {range}",
  "look.sharpeningHint": "The body's own sharpening, reproduced on the finished frame. SharpnessRange splits it between a coarse pass and a fine one (Spica); both are applied. Its kernel reaches three sensor pixels, so only an export at full size matches the camera exactly.",
  "look.sharpeningCoarseOnly": "The body's own sharpening, reproduced on the finished frame — coarse pass only, because SharpnessRange sends the whole effect there. Its kernel reaches three sensor pixels, so only an export at full size matches the camera exactly.",
  "look.droOff": "Off",
  "look.droAuto": "Auto",
  "look.droLevel": "Lv{n}",
  "look.droModeLabel": "DRO",
  "look.droAutoHint": "The curve the camera chose for this frame, read out of the RAW.",
  "look.droLevelHint": "One of Imaging Edge's ten built-in curves. Works on any frame, including one the camera wrote no curve for.",
  "look.droStrength": "Strength",
  "lookSlider.contrast": "Contrast",
  "lookSlider.highlights": "Highlights",
  "lookSlider.shadows": "Shadows",
  "lookSlider.fade": "Fade",
  "lookSlider.saturation": "Saturation",
  "lookSlider.clarity": "Clarity",

  // Detail panel
  "panel.detail": "Detail",
  "detail.denoising": "Denoising…",
  // Classical wavelet shrinkage, not a neural model — see denoise.py. Named
  // "AI Denoise" until the backend was checked; if a neural one ever ships it
  // gets its own control rather than quietly taking over this one.
  "detail.denoise": "Denoise",
  "detail.amount": "Amount",
  // Named after the controls Imaging Edge exposes, and on its 0..100 scale with
  // 50 neutral, so a value carries the same meaning across both applications.
  "detail.chromaNr": "Color NR",
  "detail.edgeNr": "Edge NR",

  // HSL panel
  "panel.hsl": "HSL / Color",
  "hsl.red": "Red",
  "hsl.orange": "Orange",
  "hsl.yellow": "Yellow",
  "hsl.green": "Green",
  "hsl.aqua": "Aqua",
  "hsl.blue": "Blue",
  "hsl.purple": "Purple",
  "hsl.magenta": "Magenta",

  // Color grading panel
  "panel.grading": "Color Grading",
  "grading.sh": "Shadows",
  "grading.md": "Midtones",
  "grading.hl": "Highlights",
  "grading.h": "H",
  "grading.s": "S",
  "grading.blend": "Blend",
  "grading.balance": "Balance",

  // Tone curve panel
  "panel.curve": "Tone Curve",
  "curve.parametric": "Param",
  "curve.rgb": "RGB",
  "curve.red": "R",
  "curve.green": "G",
  "curve.blue": "B",
  "curveRegion.highlights": "Highlights",
  "curveRegion.lights": "Lights",
  "curveRegion.darks": "Darks",
  "curveRegion.shadows": "Shadows",
  "curve.splits": "Range Splits",
  "curvePreset.linear": "Linear",
  "curvePreset.mediumContrast": "Medium Contrast",
  "curvePreset.strongContrast": "Strong Contrast",

  // Filmstrip
  "film.import": "Import",
  "film.stale": "Stale",
  "film.staleHint": "Source file no longer available — re-import it",
  "film.remove": "Remove from library (the original file on disk is untouched)",
  "aria.removeFromLibrary": "Remove from library",

  // Shared + errors
  "common.reset": "Reset",
  "error.sourceGone": "Source file no longer available (the server cache may have been cleared) — re-import this photo.",
  "error.exportSourceGone": "Source is no longer available server-side",
  "error.contextLost": "Graphics context lost — recovering…",
};

export type MessageKey = keyof typeof en;

/** Every key in the catalog. Exported so tests can sweep all locales. */
export const MESSAGE_KEYS = Object.keys(en) as MessageKey[];

const zh: Record<MessageKey, string> = {
  "action.undo": "撤销 (Ctrl+Z)",
  "action.redo": "重做 (Ctrl+Shift+Z)",
  "action.crop": "裁剪并拉直 (R)",
  "action.compareOriginal": "按住对比原图 ( \\ )",
  "action.compareJpeg": "按住对比相机 JPEG ( | )",
  "aria.undo": "撤销",
  "aria.redo": "重做",
  "aria.crop": "裁剪",
  "aria.compareOriginal": "与原图对比",
  "aria.compareJpeg": "与相机 JPEG 对比",
  "aria.cameraJpeg": "相机 JPEG 预览",
  "meta.noImage": "未载入图片",
  "action.export": "导出",
  "action.exporting": "导出中…",

  "aria.preview": "预览",
  "dropzone.title": "拖入 RAW 文件开始",
  "dropzone.sub": "或点击任意位置浏览",
  "drag.overlay": "松开以导入",
  "toast.dismiss": "点击关闭",
  "invalid.missingTitle": "源文件已不可用",
  "invalid.missingSub": "服务器缓存可能已被清理——请重新导入这张照片",
  "invalid.webglTitle": "WebGL2 不可用",
  "invalid.webglSub": "LLR 完全依赖 GPU 渲染——请启用硬件加速，或改用支持 WebGL2 的浏览器",

  "status.label": "状态",
  "status.decoding": "解码中…",
  "status.importing": "导入中…",
  "status.decodedIn": "解码耗时 {ms} 毫秒",
  "zoom.fit": "适应",

  "panel.crop": "裁剪并拉直",
  "crop.aspect": "长宽比",
  "crop.custom": "自定义…",
  "crop.swap": "切换横竖 (X)",
  "aria.swapAspect": "切换长宽比方向",
  "crop.ratio": "比例",
  "crop.angle": "角度",
  "crop.rotateLeft": "向左旋转 90°",
  "crop.rotateRight": "向右旋转 90°",
  "crop.flipH": "水平翻转",
  "crop.flipV": "垂直翻转",
  "crop.done": "完成",
  "aspect.free": "自由",
  "aspect.orig": "原始比例",

  "panel.settings": "设置",
  "settings.language": "语言",
  "settings.engine": "颜色引擎",
  "settings.engineHint": "Adobe 走 DCP 配置文件；Sony 用 RAW 内的相机标定复刻 Imaging Edge 的渲染（仅索尼机身）",
  "engine.adobe": "Adobe（DCP）",
  "engine.sony": "Sony Imaging Edge",
  "engine.sonyUnavailable": "这张已回落到 DCP 配置文件：棕褐色的染色发生在本管线未复刻的阶段，而非索尼 RAW 根本不带 Sony 标定。",
  "settings.creativeLook": "创意外观",
  "settings.dcp": "DCP 样式",
  "settings.cameraMatch": "相机匹配",
  "settings.cameraMatchHint": "将配置文件校正到该相机自己的 JPEG 直出效果",
  "settings.display": "显示",
  "display.p3": "Display-P3（广色域）",
  "settings.exportExif": "导出 EXIF",
  "exif.hint": "「完整」保留 RAW 中的全部信息；「隐私安全」会剔除 GPS、序列号、所有者姓名和厂商注释",
  "exif.full": "完整元数据",
  "exif.private": "隐私安全（无 GPS／序列号）",

  "panel.tone": "影调",
  "panel.presence": "偏好",
  "panel.color": "颜色",
  "panel.lens": "镜头校正",
  "slider.exposure": "曝光",
  "slider.contrast": "对比度",
  "slider.highlights": "高光",
  "slider.shadows": "阴影",
  "slider.whites": "白色色阶",
  "slider.blacks": "黑色色阶",
  "slider.clarity": "清晰度",
  "slider.dehaze": "去朦胧",
  "slider.temperature": "色温",
  "slider.tint": "色调",
  "slider.vibrance": "自然饱和度",
  "slider.saturation": "饱和度",
  "slider.lensDistortion": "扭曲度",
  "slider.lensVignetting": "晕影",
  "slider.hint": "点击选中后可滚轮微调（Shift ×10）· 双击复位",

  "panel.creativeLook": "创意外观",
  "look.asShotSuffix": " · 拍摄时",
  "look.borrowedHint": "该外观比这台机身新。色调曲线是索尼原厂的（跨机身逐字节一致），色彩取自搭载它的机身。",
  "look.sharpening": "机内锐化",
  "look.sharpeningValue": "{level} · 范围 {range}",
  "look.sharpeningHint": "机身自己的锐化，在成片上复现。锐化范围把它拆成粗细两段（细端为 Spica），两段都已应用。核的跨度是三个传感器像素，因此只有全尺寸导出才与相机完全一致。",
  "look.sharpeningCoarseOnly": "机身自己的锐化，在成片上复现。当前锐化范围把全部效果都给了粗端，细端不参与。核的跨度是三个传感器像素，因此只有全尺寸导出才与相机完全一致。",
  "look.droOff": "关",
  "look.droAuto": "自动",
  "look.droLevel": "Lv{n}",
  "look.droModeLabel": "DRO",
  "look.droAutoHint": "相机为这一帧选定的曲线，从 RAW 里读出。",
  "look.droLevelHint": "Imaging Edge 内置的十条曲线之一。任何一帧都能用，包括相机没写曲线的那些。",
  "look.droStrength": "强度",
  "lookSlider.contrast": "对比度",
  "lookSlider.highlights": "高光",
  "lookSlider.shadows": "阴影",
  "lookSlider.fade": "褪色",
  "lookSlider.saturation": "饱和度",
  "lookSlider.clarity": "清晰度",

  "panel.detail": "细节",
  "detail.denoising": "降噪中…",
  "detail.denoise": "降噪",
  "detail.amount": "数量",
  "detail.chromaNr": "色彩降噪",
  "detail.edgeNr": "边缘降噪",

  "panel.hsl": "HSL / 颜色",
  "hsl.red": "红色",
  "hsl.orange": "橙色",
  "hsl.yellow": "黄色",
  "hsl.green": "绿色",
  "hsl.aqua": "浅绿色",
  "hsl.blue": "蓝色",
  "hsl.purple": "紫色",
  "hsl.magenta": "洋红",

  "panel.grading": "颜色分级",
  "grading.sh": "阴影",
  "grading.md": "中间调",
  "grading.hl": "高光",
  "grading.h": "H",
  "grading.s": "S",
  "grading.blend": "混合",
  "grading.balance": "平衡",

  "panel.curve": "色调曲线",
  "curve.parametric": "参数",
  "curve.rgb": "RGB",
  "curve.red": "R",
  "curve.green": "G",
  "curve.blue": "B",
  "curveRegion.highlights": "高光",
  "curveRegion.lights": "亮调",
  "curveRegion.darks": "暗调",
  "curveRegion.shadows": "阴影",
  "curve.splits": "范围分割",
  "curvePreset.linear": "线性",
  "curvePreset.mediumContrast": "中对比度",
  "curvePreset.strongContrast": "强对比度",

  "film.import": "导入",
  "film.stale": "已失效",
  "film.staleHint": "源文件已不可用——请重新导入",
  "film.remove": "从图库移除（磁盘上的原文件不受影响）",
  "aria.removeFromLibrary": "从图库移除",

  "common.reset": "复位",
  "error.sourceGone": "源文件已不可用（服务器缓存可能已被清理）——请重新导入这张照片。",
  "error.exportSourceGone": "源文件在服务端已不可用",
  "error.contextLost": "图形上下文丢失——正在恢复…",
};

const CATALOG: Record<Locale, Record<MessageKey, string>> = { en, zh };
const STORE_KEY = "llr.locale";

function detect(): Locale {
  try {
    // Validated against the catalog rather than a hardcoded pair, so adding a
    // locale can't silently leave the persisted choice falling back to browser
    // detection.
    const saved = localStorage.getItem(STORE_KEY);
    if (saved && saved in CATALOG) return saved as Locale;
    return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
  } catch {
    return "en"; // no DOM (tests, SSR)
  }
}

function applyLang(l: Locale): void {
  try {
    document.documentElement.lang = l === "zh" ? "zh-CN" : "en";
  } catch { /* no DOM */ }
}

export const locale = ref<Locale>(detect());
// index.html ships lang="en"; correct it before first paint so a zh session
// gets the right font stack and screen-reader voice.
applyLang(locale.value);

export function setLocale(next: Locale): void {
  locale.value = next;
  applyLang(next);
  try {
    localStorage.setItem(STORE_KEY, next);
  } catch { /* private mode / no DOM */ }
}

/** Translate `key`, substituting `{name}` placeholders from `vars`. */
export function t(key: MessageKey, vars?: Record<string, string | number>): string {
  const s = CATALOG[locale.value][key];
  return vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => String(vars[k] ?? m)) : s;
}
