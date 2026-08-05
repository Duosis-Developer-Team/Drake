import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Provenance } from "@/components/provenance/Provenance";

describe("Provenance", () => {
  it("shows explicit not-configured fallbacks instead of hiding fields", () => {
    render(<Provenance />);
    const node = screen.getByTestId("provenance");
    expect(node).toHaveTextContent("Source:");
    expect(node).toHaveTextContent("Method:");
    expect(node).toHaveTextContent("Confidence:");
    expect(node.textContent?.match(/not configured/g)?.length).toBe(6);
  });

  it("renders provided values verbatim", () => {
    render(
      <Provenance
        source="tenant-adapter"
        asOf="2026-08-06T00:00:00Z"
        freshness="5m"
        scope="project/beta"
        measurementMethod="logical_rollup_exact"
        confidence="exact"
      />,
    );
    const node = screen.getByTestId("provenance");
    expect(node).toHaveTextContent("tenant-adapter");
    expect(node).toHaveTextContent("logical_rollup_exact");
    expect(node).toHaveTextContent("exact");
    expect(node).not.toHaveTextContent("not configured");
  });
});
