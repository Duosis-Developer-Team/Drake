import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GuardrailsPanel } from "@/components/agents/GuardrailsPanel";
import type { Guardrails } from "@/lib/agents";
import { LocaleProvider } from "@/lib/i18n";

vi.mock("@/lib/session", () => ({
  useSession: () => ({
    state: {
      status: "authenticated",
      me: { csrf_token: "csrf-1", permissions: [] },
    },
    refresh: vi.fn(),
    signOut: vi.fn(),
    hasPermission: () => true,
  }),
}));

function respond(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  };
}

const unset = {
  set: false,
  threshold: null,
  severity: null,
  enabled: false,
  version: null,
  state: null,
  value: null,
  since: null,
  evaluated_at: null,
  incident_id: null,
};

function guardrails(over: Partial<Guardrails> = {}): Guardrails {
  return {
    agent_id: "a1",
    can_manage: true,
    pages: true,
    min_calls: 20,
    evaluating: true,
    items: [
      {
        ...unset,
        kind: "failure_ratio",
        set: true,
        threshold: 0.05,
        severity: "critical",
        enabled: true,
        version: 2,
        state: "firing",
        value: 0.12,
        since: "2026-11-21T09:00:00Z",
        incident_id: "inc-1",
      },
      { ...unset, kind: "avg_run_seconds" },
      { ...unset, kind: "cost_day" },
      { ...unset, kind: "silence_hours" },
    ],
    ...over,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const renderPanel = () =>
  render(
    <LocaleProvider>
      <GuardrailsPanel agentId="a1" />
    </LocaleProvider>,
  );

describe("Guardrails panel", () => {
  it("shows where the agent stands and links the incident it opened", async () => {
    fetchMock.mockResolvedValue(respond(guardrails()));
    renderPanel();
    const row = await screen.findByTestId("guard-failure_ratio");
    expect(within(row).getByText("Firing")).toBeTruthy();
    expect(within(row).getByText("now 12%")).toBeTruthy();
    expect(
      within(row)
        .getByRole("link", { name: "Open incident" })
        .getAttribute("href"),
    ).toBe("/incidents/inc-1");
    expect(
      within(screen.getByTestId("guard-cost_day")).getByText("Not set"),
    ).toBeTruthy();
  });

  it("sets a daily budget with the session's CSRF token", async () => {
    fetchMock.mockResolvedValueOnce(respond(guardrails()));
    renderPanel();
    fireEvent.click(await screen.findByTestId("guard-cost_day-edit"));
    fireEvent.change(screen.getByTestId("guard-cost_day-value"), {
      target: { value: "50" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "Critical" }));
    fetchMock.mockResolvedValueOnce(respond(guardrails()));
    fireEvent.click(screen.getByTestId("guard-cost_day-save"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [path, init] = fetchMock.mock.calls[1];
    expect(String(path)).toBe("/v1/agents/a1/guardrails/cost_day");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(init.body)).toEqual({
      threshold: "50",
      severity: "critical",
      enabled: true,
    });
  });

  it("enters the failure rate as a percentage", async () => {
    fetchMock.mockResolvedValueOnce(respond(guardrails()));
    renderPanel();
    fireEvent.click(await screen.findByTestId("guard-failure_ratio-edit"));
    expect(
      (screen.getByTestId("guard-failure_ratio-value") as HTMLInputElement)
        .value,
    ).toBe("5");
    fireEvent.change(screen.getByTestId("guard-failure_ratio-value"), {
      target: { value: "2.5" },
    });
    fetchMock.mockResolvedValueOnce(respond(guardrails()));
    fireEvent.click(screen.getByTestId("guard-failure_ratio-save"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).threshold).toBe("0.025");
  });

  it("is read-only for whoever may only look, and says when it cannot page", async () => {
    fetchMock.mockResolvedValue(
      respond(guardrails({ can_manage: false, pages: false })),
    );
    renderPanel();
    await screen.findByTestId("guard-failure_ratio");
    expect(screen.queryByTestId("guard-cost_day-edit")).toBeNull();
    expect(screen.getByText(/never page anyone/)).toBeTruthy();
  });
});
