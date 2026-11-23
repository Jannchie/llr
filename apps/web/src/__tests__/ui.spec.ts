import { describe, expect, it } from "vitest";
import { exposureSummary } from "../ui";

describe("exposureSummary", () => {
  it("reads like a camera's own display", () => {
    expect(exposureSummary({ fnumber: 2.8, exposure: 1 / 250, iso: 200, focal: 35 })).toEqual(["ƒ/2.8", "1/250s", "ISO 200", "35mm"]);
  });

  it("writes whole apertures without a decimal and long exposures in seconds", () => {
    expect(exposureSummary({ fnumber: 8, exposure: 2.5, iso: 100, focal: 24.3 })).toEqual(["ƒ/8", "2.5s", "ISO 100", "24mm"]);
    expect(exposureSummary({ fnumber: 1.4, exposure: 0.5, iso: 6400, focal: 85 })).toEqual(["ƒ/1.4", "0.5s", "ISO 6400", "85mm"]);
    // Just under the switch-over, still a fraction.
    expect(exposureSummary({ fnumber: null, exposure: 1 / 3, iso: null, focal: null })).toEqual(["1/3s"]);
  });

  it("leaves out what the file did not record", () => {
    expect(exposureSummary({ fnumber: null, exposure: null, iso: 400, focal: null })).toEqual(["ISO 400"]);
    expect(exposureSummary({ fnumber: null, exposure: null, iso: null, focal: null })).toEqual([]);
    // A manual lens reports 0 for what it cannot know.
    expect(exposureSummary({ fnumber: 0, exposure: 1 / 60, iso: 800, focal: 0 })).toEqual(["1/60s", "ISO 800"]);
  });
});
