/**
 * The alert-rules screen renders what the API says, in both languages:
 * the firing readout, the register with its 24-hour lanes, the inspector,
 * and a drawer whose preview is the server's replay.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AlertRulesPage from "@/app/alert-rules/page";
import { LocaleProvider } from "@/lib/i18n";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/alert-rules",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const AS_OF = "2026-11-17T12:00:00Z";

function rule(overrides: Record<string, unknown> = {}) {
  return {
    id: "r1",
    project_id: "p1",
    environment_id: "e1",
    service_id: "s1",
    name: "Checkout errors",
    signal: "error_ratio",
    operator: ">",
    threshold: 0.05,
    for_seconds: 300,
    severity: "critical",
    enabled: true,
    muted_until: null,
    version: 1,
    created_at: "2026-11-16T09:00:00Z",
    updated_at: "2026-11-16T09:00:00Z",
    target: { project: "hermes", environment: "hermes-test", service: "core-service" },
    can_manage: true,
    states: [
      {
        binding_id: "b1",
        state: "firing",
        firing_since: "2026-11-17T11:40:00Z",
        last_value: 0.124,
        evaluated_at: AS_OF,
        incident_id: "inc-9",
        environment: "hermes-test",
        service: "core-service",
      },
    ],
    episodes: [
      {
        binding_id: "b1",
        environment: "hermes-test",
        service: "core-service",
        fired_at: "2026-11-17T03:00:00Z",
        resolved_at: "2026-11-17T03:20:00Z",
        peak: 0.09,
        incident_id: "inc-8",
      },
      {
        binding_id: "b1",
        environment: "hermes-test",
        service: "core-service",
        fired_at: "2026-11-17T11:40:00Z",
        resolved_at: null,
        peak: 0.124,
        incident_id: "inc-9",
      },
    ],
    status: "firing",
    ...overrides,
  };
}

const QUIET = rule({
  id: "r2",
  name: "Memory almost full",
  signal: "memory_utilization",
  threshold: 0.85,
  severity: "warning",
  target: { project: "hermes", environment: null, service: null },
  states: [
    {
      binding_id: "b1",
      state: "ok",
      firing_since: null,
      last_value: 0.61,
      evaluated_at: AS_OF,
      incident_id: null,
      environment: "hermes-test",
      service: "core-service",
    },
  ],
  episodes: [],
  status: "ok",
});

const LIST = {
  as_of: AS_OF,
  rules: [rule(), QUIET],
  summary: { total: 2, firing: 1, pending: 0, ok: 1, muted: 0, disabled: 0, no_data: 0, fired_24h: 2 },
};

const HISTORY = {
  rule_id: "r1",
  signal: "error_ratio",
  since: "2026-11-16T12:00:00Z",
  bindings: [
    {
      binding_id: "b1",
      environment: "hermes-test",
      service: "core-service",
      series: [
        { at: "2026-11-17T11:50:00Z", value: 0.1 },
        { at: "2026-11-17T11:55:00Z", value: 0.11 },
        { at: "2026-11-17T12:00:00Z", value: 0.124 },
      ],
    },
  ],
};

const OPTIONS = {
  signals: ["error_ratio"],
  evaluation_interval_seconds: 120,
  projects: [
    {
      id: "p1",
      key: "hermes",
      environments: [{ id: "e1", key: "hermes-test", services: [{ id: "s1", key: "core-service" }] }],
    },
  ],
};

const PREVIEW = {
  count: 3,
  minutes: 42,
  episodes: [
    {
      binding_id: "b1",
      environment: "hermes-test",
      service: "core-service",
      fired_at: "2026-11-17T03:00:00Z",
      resolved_at: "2026-11-17T03:20:00Z",
      minutes: 20,
      peak: 0.09,
    },
  ],
  suggestion: { threshold: 0.0625, count: 1 },
  bindings: 1,
  history_hours: 24,
  series: HISTORY.bindings[0],
};

function routes() {
  return installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions: ["alert.view", "alert_rule.manage"] }) },
    "/v1/alert-rules": { status: 200, body: LIST },
    "/v1/alert-rules/options": { status: 200, body: OPTIONS },
    "/v1/alert-rules/r1/history": { status: 200, body: HISTORY },
    "/v1/alert-rules/r2/history": { status: 200, body: { ...HISTORY, rule_id: "r2", bindings: [] } },
    "/v1/alert-rules/preview": { status: 200, body: PREVIEW },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("alert rules screen", () => {
  it("leads with what is firing and lists every rule with its state", async () => {
    routes();
    render(
      <SessionProvider>
        <AlertRulesPage />
      </SessionProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Alert rules");
    const firing = await screen.findByRole("region", { name: "Firing now" });
    expect(within(firing).getAllByText("Checkout errors").length).toBeGreaterThan(0);
    expect(within(firing).getByText("12.4%")).toBeInTheDocument();
    expect(within(firing).getByText(/2 firings in the last 24 hours/)).toBeInTheDocument();

    const register = screen.getByRole("region", { name: "Rules" });
    expect(within(register).getByText("Memory almost full")).toBeInTheDocument();
    expect(within(register).getByRole("button", { name: /Firing\s*1/ })).toBeInTheDocument();
  });

  it("opens the inspector with the rule in words and its event log", async () => {
    routes();
    render(
      <SessionProvider>
        <AlertRulesPage />
      </SessionProvider>,
    );
    const register = await screen.findByRole("region", { name: "Rules" });
    const row = within(register)
      .getAllByRole("button")
      .find((button) => button.getAttribute("aria-expanded") !== null && button.textContent?.includes("Checkout errors"));
    fireEvent.click(row as HTMLElement);
    expect(
      await screen.findByText(/If Error rate stays above 5% for 5m/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Open incident" })[0]).toHaveAttribute(
      "href",
      "/incidents/inc-9",
    );
    expect(screen.getByRole("button", { name: "Mute for 1 hour" })).toBeInTheDocument();
  });

  it("previews a draft with the server's replay before saving", async () => {
    const calls = routes();
    render(
      <SessionProvider>
        <AlertRulesPage />
      </SessionProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Create rule/ }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByText("3")).toBeInTheDocument());
    expect(within(dialog).getByText(/times it would have fired/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Use 6.3%" })).toBeInTheDocument();
    const posted = calls.find((call) => call.path === "/v1/alert-rules/preview");
    expect(JSON.parse(String(posted?.init?.body))).toMatchObject({
      project_id: "p1",
      signal: "error_ratio",
      operator: ">",
      threshold: 0.05,
      for_seconds: 300,
      severity: "critical",
    });
  });

  it("offers log and trace signals, and a template that watches fatal logs", async () => {
    const calls = routes();
    render(
      <SessionProvider>
        <AlertRulesPage />
      </SessionProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Create rule/ }));
    const dialog = await screen.findByRole("dialog");
    const picker = within(dialog).getByLabelText("Signal");
    const groups = Array.from(picker.querySelectorAll("optgroup")).map((g) => g.getAttribute("label"));
    expect(groups).toEqual(["Metrics", "Logs", "Traces"]);
    // The deployment's own interval, not a default.
    expect(within(dialog).getByText("Checked every 2m")).toBeInTheDocument();
    expect(within(picker).getByRole("option", { name: "Fatal log lines (5 min)" })).toBeInTheDocument();
    expect(within(picker).getByRole("option", { name: "Slowest endpoint p95" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /A fatal log line/ }));
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.path === "/v1/alert-rules/preview" &&
            JSON.parse(String(call.init?.body)).signal === "fatal_logs",
        ),
      ).toBe(true),
    );
    const last = calls.filter((call) => call.path === "/v1/alert-rules/preview").at(-1);
    expect(JSON.parse(String(last?.init?.body))).toMatchObject({
      signal: "fatal_logs",
      operator: ">",
      threshold: 0,
      for_seconds: 0,
      severity: "critical",
    });
  });

  it("watches a text in the logs only once the text is given", async () => {
    const calls = routes();
    render(
      <SessionProvider>
        <AlertRulesPage />
      </SessionProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Create rule/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /A text in the logs/ }));
    expect(await within(dialog).findByText(/Type the text to look for/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Lines containing"), {
      target: { value: "payment failed" },
    });
    await waitFor(() =>
      expect(
        calls.some((call) => {
          if (call.path !== "/v1/alert-rules/preview") return false;
          const body = JSON.parse(String(call.init?.body));
          return body.signal === "matching_logs" && body.log_contains === "payment failed";
        }),
      ).toBe(true),
    );
    expect(within(dialog).getByText(/“payment failed”/)).toBeInTheDocument();
  });

  it("judges against normal with a sensitivity instead of a threshold", async () => {
    const calls = routes();
    render(
      <SessionProvider>
        <AlertRulesPage />
      </SessionProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Create rule/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Unusual error logs/ }));
    expect(await within(dialog).findByLabelText("Sensitivity")).toBeInTheDocument();
    expect(within(dialog).getByText("3 × the usual spread")).toBeInTheDocument();
    expect(within(dialog).getByText(/rises above its normal|stays above its normal/)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.path === "/v1/alert-rules/preview" &&
            JSON.parse(String(call.init?.body)).operator === "above_normal",
        ),
      ).toBe(true),
    );
  });
});

describe("alert rules screen in Turkish", () => {
  it("renders the console and register from the catalogue", async () => {
    routes();
    render(
      <LocaleProvider locale="tr">
        <SessionProvider>
          <AlertRulesPage />
        </SessionProvider>
      </LocaleProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Alarm kuralları");
    expect(await screen.findByText("Şu an tetiklenen")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Kural oluştur/ })).toBeInTheDocument();
    expect(screen.getByText(/son 24 saatte 2 tetiklenme/)).toBeInTheDocument();
  });
});
