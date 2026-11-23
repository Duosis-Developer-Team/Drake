import { describe, expect, it } from "vitest";

import { slugify } from "./ConnectCluster";

describe("slugify", () => {
  it("turns a display name into a DNS-label cluster id", () => {
    expect(slugify("Acme Production")).toBe("acme-production");
    expect(slugify("  Üretim Kümesi #2 ")).toBe("uretim-kumesi-2");
    expect(slugify("İstanbul ışık")).toBe("istanbul-isik");
    expect(slugify("---")).toBe("");
  });

  it("never ends on a dash after truncation", () => {
    const id = slugify("a".repeat(62) + " b");
    expect(id.length).toBeLessThanOrEqual(63);
    expect(id.endsWith("-")).toBe(false);
  });
});
