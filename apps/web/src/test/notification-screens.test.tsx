import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationDeliveriesPage from "@/app/notification-deliveries/page";
import NotificationPoliciesPage from "@/app/notification-policies/page";
import NotificationsPage from "@/app/notifications/page";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { SessionProvider } from "@/lib/session";
import { errorBody, installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/notifications",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function inboxItem(overrides: Record<string, unknown> = {}) {
  return {
    id: "n1",
    event_type: "opened",
    title: "Incident opened: api (dev): No replicas ready",
    body: "pilot/dev/api — No replicas ready. Drake opened an incident.",
    target_path: "/incidents/inc-1",
    metadata: { severity: "critical", primary_reason: "no_ready_replicas" },
    created_at: "2026-08-08T12:00:00Z",
    read_at: null,
    incident_id: "inc-1",
    accessible: true,
    ...overrides,
  };
}

const POLICY = {
  id: "pol-1",
  display_name: "Critical incidents",
  project_id: "p1",
  project_key: "pilot",
  environment_id: "e1",
  environment_key: "dev",
  service_id: null,
  service_key: null,
  event_types: ["opened", "auto_resolved"],
  severities: ["critical"],
  enabled: true,
  version: 3,
  destination_count: 2,
};

const OPTIONS = {
  event_types: ["opened", "acknowledged", "auto_resolved"],
  severities: ["critical"],
  destination_types: ["in_app_user", "webhook"],
  webhook_keys: [
    { key: "ops-primary", display_name: "Ops primary", payload_schema_version: 1 },
  ],
};

const DESTINATIONS = [
  {
    id: "d1",
    destination_type: "webhook",
    display_name: "Ops primary",
    destination_key: "ops-primary",
    enabled: true,
    project_id: "p1",
    recipient: null,
    payload_schema_version: 1,
    version: 1,
  },
];

const DELIVERY = {
  id: "del-1",
  state: "retrying",
  attempt_count: 2,
  next_attempt_at: "2026-08-08T12:10:00Z",
  delivered_at: null,
  last_error_code: "http_503",
  last_http_status: 503,
  created_at: "2026-08-08T12:00:00Z",
  destination_display_name: "Ops primary",
  event_type: "opened",
  incident_id: "inc-1",
  incident_title: "api (dev): No replicas ready",
  project_key: "pilot",
};

afterEach(() => vi.unstubAllGlobals());

// --- inbox ---------------------------------------------------------------

function renderInbox(
  items: unknown[],
  extra: Record<string, { status: number; body: unknown }> = {},
) {
  const calls = installFetchMock({
    "/v1/me": { status: 200, body: makeMe() },
    "/v1/notifications": { status: 200, body: { items, next_cursor: null, limit: 25 } },
    ...extra,
  });
  render(
    <SessionProvider>
      <NotificationsPage />
    </SessionProvider>,
  );
  return calls;
}

describe("notification inbox", () => {
  it("renders the server-composed title, body and incident link", async () => {
    renderInbox([inboxItem()]);
    const row = await screen.findByTestId("notification-n1");
    // The title itself, not the event-type label that repeats the phrase.
    expect(
      within(row).getByText("Incident opened: api (dev): No replicas ready"),
    ).toBeInTheDocument();
    expect(
      within(row).getByText("pilot/dev/api — No replicas ready. Drake opened an incident."),
    ).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: /open incident/i })).toHaveAttribute(
      "href",
      "/incidents/inc-1",
    );
  });

  it("withholds an incident the reader can no longer access", async () => {
    renderInbox([
      inboxItem({
        accessible: false,
        title: "Notification unavailable",
        body: "This notification refers to a service you no longer have access to.",
        target_path: null,
        incident_id: null,
        metadata: {},
        event_type: null,
      }),
    ]);
    const row = await screen.findByTestId("notification-n1");
    expect(within(row).getByTestId("notification-withheld")).toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
    // Nothing about the service survives the revocation.
    expect(row.textContent).not.toContain("pilot/dev/api");
  });

  it("marks a notification read and reloads", async () => {
    const calls = renderInbox([inboxItem()], {
      "/v1/notifications/read": { status: 200, body: { marked_read: 1 } },
    });
    fireEvent.click(await screen.findByRole("button", { name: /mark read/i }));

    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith("/v1/notifications/read"))).toBe(true),
    );
    const call = calls.find((entry) => entry.path.endsWith("/v1/notifications/read"));
    expect(JSON.parse(String(call?.init?.body))).toEqual({ notification_ids: ["n1"] });
    // CSRF is carried on the mutation.
    expect((call?.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBeTruthy();
  });

  it("separates loading, empty and error", async () => {
    installFetchMock({ "/v1/me": { status: 200, body: makeMe() } });
    const { unmount } = render(
      <SessionProvider>
        <NotificationsPage />
      </SessionProvider>,
    );
    expect(screen.getByTestId("state-loading")).toBeInTheDocument();
    unmount();

    renderInbox([]);
    expect(await screen.findByText("No notifications")).toBeInTheDocument();

    installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/notifications": {
        status: 503,
        body: errorBody("unavailable", "database is down"),
      },
    });
    render(
      <SessionProvider>
        <NotificationsPage />
      </SessionProvider>,
    );
    expect(await screen.findByTestId("state-error")).toBeInTheDocument();
  });

  it("shows an unread badge and hides it when nothing is unread", async () => {
    installFetchMock({
      "/v1/notifications/unread-count": { status: 200, body: { unread: 3 } },
    });
    const { unmount } = render(<NotificationBell />);
    expect(await screen.findByTestId("unread-badge")).toHaveTextContent("3");
    expect(screen.getByTestId("notification-bell")).toHaveAttribute(
      "aria-label",
      "Notifications, 3 unread",
    );
    unmount();

    installFetchMock({
      "/v1/notifications/unread-count": { status: 200, body: { unread: 0 } },
    });
    render(<NotificationBell />);
    await waitFor(() =>
      expect(screen.queryByTestId("unread-badge")).not.toBeInTheDocument(),
    );
  });

  it("shows no badge at all when the count cannot be read", async () => {
    // A wrong number is worse than none: people learn to trust the badge.
    installFetchMock({
      "/v1/notifications/unread-count": { status: 503, body: errorBody("x", "down") },
    });
    render(<NotificationBell />);
    await waitFor(() =>
      expect(screen.queryByTestId("unread-badge")).not.toBeInTheDocument(),
    );
  });
});

// --- policies -------------------------------------------------------------

function renderPolicies(
  permissions: string[] = ["notification.view", "notification.manage"],
  extra: Record<string, { status: number; body: unknown }> = {},
) {
  const calls = installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions }) },
    "/v1/notification-policies": { status: 200, body: { policies: [POLICY] } },
    "/v1/notification-policies/options": { status: 200, body: OPTIONS },
    "/v1/notification-destinations": { status: 200, body: { destinations: DESTINATIONS } },
    "/v1/projects": {
      status: 200,
      body: { projects: [{ id: "p1", project_key: "pilot", display_name: "Pilot" }] },
    },
    "/v1/projects/p1/environments": {
      status: 200,
      body: { environments: [{ id: "e1", environment_key: "dev" }] },
    },
    ...extra,
  });
  render(
    <SessionProvider>
      <NotificationPoliciesPage />
    </SessionProvider>,
  );
  return calls;
}

describe("notification policies", () => {
  it("lists policies with their scope and events", async () => {
    renderPolicies();
    const row = await screen.findByTestId("policy-pol-1");
    expect(within(row).getByText("Critical incidents")).toBeInTheDocument();
    expect(within(row).getByText("pilot/dev")).toBeInTheDocument();
    expect(within(row).getByText(/Incident opened, Incident resolved/)).toBeInTheDocument();
    expect(within(row).getByText("Enabled")).toBeInTheDocument();
  });

  it("offers no field for a URL, header, token or message body", async () => {
    renderPolicies();
    const form = await screen.findByRole("form", { name: /notification policy/i });
    const inputs = within(form).getAllByRole("textbox");
    // Exactly one free-text input, and it is the policy's own name.
    expect(inputs).toHaveLength(1);
    expect(form.textContent).not.toMatch(/https?:\/\//);
    expect(within(form).queryByLabelText(/url|header|token|payload|body/i)).toBeNull();
  });

  it("clears the environment when the project changes", async () => {
    renderPolicies();
    await screen.findByRole("form", { name: /notification policy/i });
    const project = screen.getByLabelText("Project");
    const environment = screen.getByLabelText(/environment/i);
    expect(environment).toBeDisabled();

    fireEvent.change(project, { target: { value: "p1" } });
    await waitFor(() => expect(environment).not.toBeDisabled());
    fireEvent.change(environment, { target: { value: "e1" } });
    expect((environment as HTMLSelectElement).value).toBe("e1");

    fireEvent.change(project, { target: { value: "" } });
    expect((environment as HTMLSelectElement).value).toBe("");
    expect(environment).toBeDisabled();
  });

  it("creates a policy and says it does not replay history", async () => {
    renderPolicies(["notification.manage"], {
      "/v1/notification-policies": { status: 200, body: { policies: [] } },
    });
    await screen.findByRole("form", { name: /notification policy/i });

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Critical incidents" },
    });
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "p1" } });
    fireEvent.click(screen.getByRole("button", { name: /create policy/i }));

    const saved = await screen.findByTestId("policy-saved");
    expect(saved).toHaveTextContent(/past incidents are not replayed/i);
  });

  it("explains a version conflict on edit", async () => {
    renderPolicies(["notification.manage"], {
      "/v1/notification-policies/pol-1": {
        status: 409,
        body: errorBody("conflict", "the policy changed since it was read"),
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: /edit/i }));
    fireEvent.click(await screen.findByRole("button", { name: /save changes/i }));

    const conflict = await screen.findByTestId("policy-conflict");
    expect(conflict).toHaveTextContent(/someone else saved first/i);
  });

  it("disables the form without notification.manage", async () => {
    renderPolicies(["notification.view"]);
    await screen.findByRole("form", { name: /notification policy/i });
    expect(screen.getByTestId("state-permission-denied")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create policy/i })).toBeDisabled();
  });
});

// --- delivery audit --------------------------------------------------------

describe("delivery audit", () => {
  it("shows state, attempts and a safe error label", async () => {
    installFetchMock({
      "/v1/notification-deliveries": { status: 200, body: { items: [DELIVERY], limit: 25 } },
    });
    render(<NotificationDeliveriesPage />);

    const row = await screen.findByTestId("delivery-del-1");
    expect(within(row).getByText("Retrying")).toBeInTheDocument();
    expect(within(row).getByText("→ Ops primary")).toBeInTheDocument();
    expect(within(row).getByText("2 attempts")).toBeInTheDocument();
    expect(within(row).getByText(/HTTP 503/)).toBeInTheDocument();
  });

  it("expands an attempt timeline without exposing a target or body", async () => {
    installFetchMock({
      "/v1/notification-deliveries": { status: 200, body: { items: [DELIVERY], limit: 25 } },
      "/v1/notification-deliveries/del-1/attempts": {
        status: 200,
        body: {
          attempts: [
            {
              attempt_number: 1,
              started_at: "2026-08-08T12:00:00Z",
              completed_at: "2026-08-08T12:00:01Z",
              outcome: "retryable",
              http_status: 503,
              error_code: "http_503",
              duration_ms: 42,
              retry_at: "2026-08-08T12:01:00Z",
            },
          ],
        },
      },
    });
    render(<NotificationDeliveriesPage />);
    fireEvent.click(await screen.findByRole("button", { name: /attempts/i }));

    const timeline = await screen.findByTestId("attempt-timeline");
    expect(timeline).toHaveTextContent("Attempt 1");
    expect(timeline).toHaveTextContent("42 ms");

    const text = document.body.textContent ?? "";
    for (const forbidden of ["http://", "https://", "secret", "traceback", "receiver."]) {
      expect(text.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("separates empty from error", async () => {
    installFetchMock({
      "/v1/notification-deliveries": { status: 200, body: { items: [], limit: 25 } },
    });
    const { unmount } = render(<NotificationDeliveriesPage />);
    expect(await screen.findByText("No deliveries")).toBeInTheDocument();
    unmount();

    installFetchMock({
      "/v1/notification-deliveries": { status: 500, body: errorBody("x", "boom") },
    });
    render(<NotificationDeliveriesPage />);
    expect(await screen.findByTestId("state-error")).toBeInTheDocument();
  });
});
