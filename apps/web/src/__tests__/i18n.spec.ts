import { afterEach, describe, expect, it } from "vitest";
import { LOCALES, MESSAGE_KEYS, locale, setLocale, t } from "../i18n";

// Catalog *completeness* is a vue-tsc concern (zh is a Record<MessageKey,
// string>, so a missing or misspelled key fails the build). These cover the
// runtime bits types can't reach.

const original = locale.value;
afterEach(() => setLocale(original));

describe("t", () => {
  it("switches catalog with the locale", () => {
    setLocale("en");
    expect(t("action.export")).toBe("Export");
    setLocale("zh");
    expect(t("action.export")).toBe("导出");
  });

  it("substitutes named placeholders", () => {
    setLocale("en");
    expect(t("status.decodedIn", { ms: 412 })).toBe("Decoded in 412ms");
    setLocale("zh");
    expect(t("status.decodedIn", { ms: 412 })).toBe("解码耗时 412 毫秒");
  });

  it("leaves a placeholder untouched when no value is supplied", () => {
    setLocale("en");
    expect(t("status.decodedIn", {})).toBe("Decoded in {ms}ms");
  });
});

describe("catalog", () => {
  // A blank message renders as an invisible control — worse than falling back
  // to English, and the type system is happy with "".
  it("has no empty messages in any locale", () => {
    const blank: string[] = [];
    for (const { value } of LOCALES) {
      setLocale(value);
      for (const key of MESSAGE_KEYS) if (t(key).trim() === "") blank.push(`${value}/${key}`);
    }
    expect(blank).toEqual([]);
  });

  // Placeholders are part of the contract: a locale that drops {ms} silently
  // loses the number, and one that invents {msec} renders the braces.
  it("uses the same placeholders across locales", () => {
    const names = (s: string) => (s.match(/\{(\w+)\}/g) ?? []).sort().join(",");
    const mismatched: string[] = [];
    for (const key of MESSAGE_KEYS) {
      setLocale("en");
      const expected = names(t(key));
      for (const { value } of LOCALES) {
        setLocale(value);
        if (names(t(key)) !== expected) mismatched.push(`${value}/${key}`);
      }
    }
    expect(mismatched).toEqual([]);
  });
});
