import { describe, expect, it } from "vitest";

import { orderByBarycentre, ports } from "@/components/agents/dev/DevMap";
import type { DevPerson, DevRepository } from "@/lib/devAgents";

const person = (key: string) => ({ key, name: key }) as DevPerson;
const repo = (id: string, catalogued = true) => ({ id, name: id, catalogued, project_name: id }) as DevRepository;

describe("development map layout", () => {
  it("gives every arrow its own port, at least 3px clear of the next", () => {
    const at = ports([4, 4, 2, 1.5, 3], 100, 46);
    expect(at).toHaveLength(5);
    for (let i = 1; i < at.length; i += 1) {
      const gap = at[i].y - at[i].w / 2 - (at[i - 1].y + at[i - 1].w / 2);
      expect(gap).toBeGreaterThanOrEqual(3 - 1e-9);
    }
    // Centred on the card.
    expect((at[0].y - at[0].w / 2 + at[4].y + at[4].w / 2) / 2).toBeCloseTo(100);
  });

  it("thins arrows rather than overlapping them when a card has too many", () => {
    const at = ports(Array(12).fill(4), 50, 46);
    const span = at[11].y + at[11].w / 2 - (at[0].y - at[0].w / 2);
    expect(span).toBeLessThanOrEqual(46 + 1e-9);
    expect(Math.min(...at.map((p) => p.w))).toBeGreaterThanOrEqual(1);
  });

  it("orders both columns to uncross the arrows, catalogued repositories first", () => {
    // a→R2 and b→R1 cross in the given order; c only touches an outside repo.
    const { people, repositories } = orderByBarycentre(
      [person("a"), person("b"), person("c")],
      [repo("R1"), repo("R2"), repo("X", false)],
      [
        { person: "a", repository: "R2", commits: 10 },
        { person: "b", repository: "R1", commits: 10 },
        { person: "c", repository: "X", commits: 3 },
      ],
    );
    const p = people.map((x) => x.key);
    const r = repositories.map((x) => x.id);
    expect(r[2]).toBe("X");
    // Whatever comes first on one side, its partner comes first on the other.
    expect(p.indexOf("a") < p.indexOf("b")).toBe(r.indexOf("R2") < r.indexOf("R1"));
  });
});
