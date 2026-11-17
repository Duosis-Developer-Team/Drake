import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ScheduledJobs, type ScheduledJobsPage } from "@/components/catalog/ScheduledJobs";
import { LocaleProvider } from "@/lib/i18n";

function respond(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: "", json: async () => body };
}

const PAGE: ScheduledJobsPage = {
  environment_id: "e1",
  as_of: "2026-11-16T20:00:00+00:00",
  state: "ok",
  jobs: [
    {
      name: "hermes-api-cleanup",
      schedule: "0 3 * * *",
      suspended: false,
      health: "degraded",
      reasons: ["recent_runs_failed"],
      last_schedule_time: "2026-11-16T03:00:00Z",
      last_successful_time: "2026-08-16T03:01:19Z",
      recent_runs: [
        { name: "hermes-api-cleanup-29913300", scheduled_at: "2026-11-16T03:00:00+00:00", outcome: "failed" },
        { name: "hermes-api-cleanup-29911860", scheduled_at: "2026-11-15T03:00:00+00:00", outcome: "failed" },
      ],
    },
    {
      name: "hermes-ticket-dispatcher",
      schedule: "* * * * *",
      suspended: false,
      health: "healthy",
      reasons: [],
      last_schedule_time: "2026-11-16T20:10:00Z",
      last_successful_time: "2026-11-16T20:09:15Z",
      recent_runs: [
        { name: "hermes-ticket-dispatcher-29914330", scheduled_at: null, outcome: "running" },
      ],
    },
  ],
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ScheduledJobs", () => {
  it("shows each CronJob's health, why, and how its recent runs ended", async () => {
    fetchMock.mockResolvedValue(respond(PAGE));
    render(<ScheduledJobs projectId="p1" environmentId="e1" />);

    const [cleanup, dispatcher] = await screen.findAllByTestId("scheduled-job");
    expect(within(cleanup).getByText("hermes-api-cleanup")).toBeInTheDocument();
    expect(within(cleanup).getByTestId("scheduled-job-reasons")).toHaveTextContent(
      "Its recent runs failed",
    );
    expect(within(cleanup).getAllByText("Failed")).toHaveLength(2);
    // A run in progress is running, never a failure.
    expect(within(dispatcher).getByText("Running")).toBeInTheDocument();
    expect(within(dispatcher).queryByTestId("scheduled-job-reasons")).not.toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/projects/p1/environments/e1/scheduled-jobs",
    );
  });

  it("opens a CronJob's logs on demand, and only then reads them", async () => {
    fetchMock.mockImplementation(async (input: string) =>
      String(input).includes("/logs")
        ? respond({
            state: "ok",
            entries: [
              {
                timestamp: "2026-11-16T03:00:05.000+00:00",
                severity: null,
                severity_number: 17,
                body: "ERROR: cleanup failed: relation does not exist",
                trace_id: null,
                span_id: null,
                pod: "hermes-api-cleanup-29913300-abcde",
                container: "cleanup",
              },
            ],
          })
        : respond(PAGE),
    );
    render(<ScheduledJobs projectId="p1" environmentId="e1" />);
    const [cleanup] = await screen.findAllByTestId("scheduled-job");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.click(within(cleanup).getByText("Show logs"));
    expect(await within(cleanup).findByTestId("scheduled-job-logs")).toHaveTextContent(
      "cleanup failed: relation does not exist",
    );
    expect(String(fetchMock.mock.calls[1][0])).toContain(
      "/scheduled-jobs/hermes-api-cleanup/logs?range=24h&limit=100",
    );
  });

  it("says so when there are none, and when the runtime has none", async () => {
    fetchMock.mockResolvedValueOnce(respond({ ...PAGE, jobs: [] }));
    const a = render(<ScheduledJobs projectId="p1" environmentId="e1" />);
    expect(await screen.findByText("No scheduled jobs run in this environment.")).toBeInTheDocument();
    a.unmount();

    fetchMock.mockResolvedValueOnce(respond({ ...PAGE, state: "not_applicable", jobs: [] }));
    render(<ScheduledJobs projectId="p1" environmentId="e2" />);
    expect(await screen.findByText("This environment's runtime has no scheduled jobs.")).toBeInTheDocument();
  });

  it("renders in Turkish", async () => {
    fetchMock.mockResolvedValue(respond(PAGE));
    render(
      <LocaleProvider locale="tr">
        <ScheduledJobs projectId="p1" environmentId="e1" />
      </LocaleProvider>,
    );
    expect(await screen.findByText("Zamanlanmış işler")).toBeInTheDocument();
    expect(screen.getByText("Son çalıştırmaları başarısız oldu")).toBeInTheDocument();
    expect(screen.getByText("Çalışıyor")).toBeInTheDocument();
  });
});
