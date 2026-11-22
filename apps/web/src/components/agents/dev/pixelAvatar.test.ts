import { afterEach, describe, expect, it, vi } from "vitest";

import { avatarUrl, loadAvatar, mayHaveAvatar, quantize } from "@/components/agents/dev/pixelAvatar";
import { installFetchMock } from "@/test/mock-api";

afterEach(() => vi.unstubAllGlobals());

describe("arcade avatars", () => {
  it("asks Tracesis, never GitHub, by the person's key", () => {
    expect(avatarUrl("gencay")).toBe("/v1/agents/dev/people/gencay/avatar");
    expect(avatarUrl("a b")).toBe("/v1/agents/dev/people/a%20b/avatar");
  });

  it("only asks for people who can have a GitHub picture", () => {
    expect(mayHaveAvatar("gencaycoskuntr")).toBe(true);
    expect(mayHaveAvatar("dependabot[bot]", true)).toBe(false);
    expect(mayHaveAvatar("name:enes")).toBe(false);
    expect(mayHaveAvatar("claude:abc123")).toBe(false);
    expect(mayHaveAvatar("unknown")).toBe(false);
  });

  it("snaps every channel to four levels", () => {
    const levels = new Set(Array.from({ length: 256 }, (_, v) => quantize(v)));
    expect([...levels].sort((a, b) => a - b)).toEqual([0, 85, 170, 255]);
    expect(quantize(0)).toBe(0);
    expect(quantize(255)).toBe(255);
  });

  it("draws the initials when there is no picture (204)", async () => {
    const calls = installFetchMock({ "/v1/agents/dev/people/nopicture/avatar": { status: 204, body: null } });
    expect(await loadAvatar("nopicture")).toBeNull();
    expect(await loadAvatar("nopicture")).toBeNull();
    expect(calls).toHaveLength(1); // one load per person per page
    expect(calls[0].path).toBe("/v1/agents/dev/people/nopicture/avatar");
  });
});
