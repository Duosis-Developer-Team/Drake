import { describe, expect, it } from "vitest";

import type { ApplyResult } from "@/lib/onboarding";

/**
 * The apply response has two legal shapes, and the type has to admit both.
 *
 * A receipt written before migration 0020 never recorded the four extended
 * counters, so a replay of one reports `null` — "not recorded" — instead of
 * a zero that would read as a measurement. The interface used to declare
 * those four as `number`, which meant every consumer was told a value would
 * be there when sometimes it is not.
 *
 * The type-level half of this is enforced by `pnpm typecheck`: both fixtures
 * below are annotated `ApplyResult`, so narrowing the four fields back to
 * `number` fails the build rather than failing quietly at runtime.
 */

const FRESH: ApplyResult = {
  outcome: "applied",
  project_id: "6f1b6f1e-2f7f-4a3a-9a4f-1f2b3c4d5e6f",
  created_entities: 7,
  linked_entities: 1,
  unchanged_entities: 2,
  no_change_count: 2,
  metadata_updated: 3,
  slo_definitions_created: 1,
  slo_definitions_updated: 0,
  bindings_created: 2,
};

/** The same request replayed against a receipt written before 0020. */
const LEGACY_REPLAY: ApplyResult = {
  outcome: "applied",
  project_id: "6f1b6f1e-2f7f-4a3a-9a4f-1f2b3c4d5e6f",
  created_entities: 7,
  linked_entities: 1,
  unchanged_entities: 2,
  no_change_count: 2,
  metadata_updated: null,
  slo_definitions_created: null,
  slo_definitions_updated: null,
  bindings_created: null,
};

const EXTENDED = [
  "metadata_updated",
  "slo_definitions_created",
  "slo_definitions_updated",
  "bindings_created",
] as const;

describe("ApplyResult contract", () => {
  it("carries numbers for a receipt the current schema recorded", () => {
    for (const field of EXTENDED) {
      expect(typeof FRESH[field]).toBe("number");
    }
    // Including a real zero, which is a measurement and must stay one.
    expect(FRESH.slo_definitions_updated).toBe(0);
  });

  it("carries null for counters a pre-0020 receipt never recorded", () => {
    for (const field of EXTENDED) {
      expect(LEGACY_REPLAY[field]).toBeNull();
    }
  });

  it("does not let an unrecorded counter be mistaken for zero", () => {
    for (const field of EXTENDED) {
      // The distinction this whole contract exists for: `null == 0` is
      // false in JS, but `Number(null)` and `null ?? 0` are both 0, so a
      // consumer that normalises loses it. Nothing here normalises.
      expect(LEGACY_REPLAY[field]).not.toBe(0);
      expect(LEGACY_REPLAY[field]).not.toBe(FRESH[field]);
    }
  });

  it("keeps the three original counters non-nullable in both shapes", () => {
    for (const result of [FRESH, LEGACY_REPLAY]) {
      expect(typeof result.created_entities).toBe("number");
      expect(typeof result.linked_entities).toBe("number");
      expect(typeof result.unchanged_entities).toBe("number");
    }
  });

  it("replays the recorded outcome rather than reporting a new one", () => {
    // A retry is not an operation that changed nothing; it is the committed
    // answer to an operation that changed things, sent back verbatim.
    expect(LEGACY_REPLAY.outcome).toBe(FRESH.outcome);
  });
});
