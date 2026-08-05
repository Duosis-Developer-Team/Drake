import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DISTINCT_STATE_KINDS, DataState } from "@/components/state/DataState";

describe("DataState", () => {
  it("renders every kind with a distinct testid", () => {
    for (const kind of DISTINCT_STATE_KINDS) {
      const { unmount } = render(<DataState kind={kind} />);
      expect(screen.getByTestId(`state-${kind}`)).toBeInTheDocument();
      unmount();
    }
  });

  it("keeps no-data, zero, and error semantically distinct", () => {
    render(<DataState kind="no-data" />);
    render(<DataState kind="zero" />);
    render(<DataState kind="error" />);

    expect(screen.getByTestId("state-no-data")).toHaveTextContent("No data");
    expect(screen.getByTestId("state-zero")).toHaveTextContent("actual value of 0");
    expect(screen.getByTestId("state-error")).toHaveTextContent("Query failed");
    expect(screen.getByTestId("state-no-data")).not.toHaveTextContent("0");
  });

  it("never describes unknown as healthy or zero", () => {
    render(<DataState kind="unknown" />);
    const node = screen.getByTestId("state-unknown");
    expect(node).toHaveTextContent(/unknown/i);
    expect(node.textContent?.toLowerCase()).not.toContain("healthy");
    expect(node.textContent).not.toContain("0");
  });

  it("loading is a skeleton without textual content", () => {
    render(<DataState kind="loading" />);
    const node = screen.getByTestId("state-loading");
    expect(node).toHaveAttribute("aria-busy", "true");
    expect(node.textContent).toBe("Loading"); // screen-reader-only label
  });

  it("stale exposes the last successful update time", () => {
    render(<DataState kind="stale" lastSuccessAt="2026-08-06T00:00:00Z" />);
    expect(screen.getByTestId("state-stale")).toHaveTextContent("Last successful update");
    expect(screen.getByTestId("state-stale")).toHaveTextContent("2026-08-06T00:00:00Z");
  });

  it("error offers retry when a handler is provided", async () => {
    const onRetry = vi.fn();
    render(<DataState kind="error" onRetry={onRetry} />);
    screen.getByRole("button", { name: /retry/i }).click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("permission denied does not leak data language", () => {
    render(<DataState kind="permission-denied" />);
    expect(screen.getByTestId("state-permission-denied")).toHaveTextContent(
      "Permission required",
    );
  });
});
