import { describe, expect, it } from "vitest";

import {
  displayBand,
  isHealthCheck,
  logsPath,
  severityBand,
  structuredBody,
  utcClock,
  utcDay,
  type LogEntry,
} from "@/lib/logs";

function entry(overrides: Partial<LogEntry> = {}): LogEntry {
  return {
    timestamp: "2026-11-04T09:15:02.123456+00:00",
    severity: null,
    severity_number: 0,
    body: "hello",
    trace_id: null,
    span_id: null,
    pod: "api-1",
    container: "api",
    ...overrides,
  };
}

describe("logsPath", () => {
  it("sends only the caller's bounded choices", () => {
    const path = logsPath("b1", { range: "6h", severity: "warn", term: "  timeout ", before: null });
    const url = new URL(path, "file:///");
    expect(url.pathname).toBe("/v1/service-health/bindings/b1/logs");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      range: "6h",
      severity: "warn",
      limit: "100",
      q: "timeout",
    });
  });

  it("omits an empty search and carries the page cursor", () => {
    const url = new URL(
      logsPath("b1", { range: "1h", severity: "any", term: "   ", before: "2026-11-04T09:00:00+00:00" }),
      "file:///",
    );
    expect(url.searchParams.has("q")).toBe(false);
    expect(url.searchParams.get("before")).toBe("2026-11-04T09:00:00+00:00");
  });
});

describe("isHealthCheck", () => {
  it.each([
    'INFO:     10.233.65.1:34232 - "GET /health HTTP/1.1" 200 OK',
    '10.233.65.1 - - [28/Oct/2026:14:32:16 +0000] "GET / HTTP/1.1" 200 1127 "-" "kube-probe/1.34"',
    'GET /healthz 200',
    '"GET /readyz?verbose HTTP/1.1" 200',
    'GET /-/healthy 200',
  ])("recognises %s", (body) => {
    expect(isHealthCheck({ body })).toBe(true);
  });

  it.each([
    'GET /healthcare/patients 200',
    "ERROR health check dependency failed: database unreachable",
    'POST /health 500',
    "connection reset by peer",
  ])("leaves %s visible", (body) => {
    expect(isHealthCheck({ body })).toBe(false);
  });
});

describe("severity bands", () => {
  it.each([
    [0, "none"],
    [1, "debug"],
    [9, "info"],
    [13, "warn"],
    [17, "error"],
    [21, "fatal"],
  ] as const)("number %i is %s", (n, band) => {
    expect(severityBand({ severity_number: n })).toBe(band);
  });

  it("uses a JSON line's own level only when nothing was stored", () => {
    const structured = structuredBody('{"level":"ERROR","message":"boom"}');
    expect(displayBand(entry(), structured)).toBe("error");
    // A stored severity wins over what the line claims about itself.
    expect(displayBand(entry({ severity_number: 9 }), structured)).toBe("info");
    expect(displayBand(entry(), structuredBody('{"level":"mystery","msg":"x"}'))).toBe("none");
  });
});

describe("structuredBody", () => {
  it("leads with the message and level and keeps the rest as fields", () => {
    const parsed = structuredBody(
      '{"timestamp":"2026-11-04T09:15:02Z","level":"INFO","logger":"drake_api","message":"request served","status":200}',
    );
    expect(parsed).toEqual({
      message: "request served",
      level: "INFO",
      fields: [
        ["timestamp", "2026-11-04T09:15:02Z"],
        ["logger", "drake_api"],
        ["status", "200"],
      ],
    });
  });

  it.each(["plain text line", "{not json}", "[1,2,3]", '{"a":1'])("returns null for %s", (body) => {
    expect(structuredBody(body)).toBeNull();
  });
});

describe("UTC formatting", () => {
  it("shows clock and day in UTC whatever the input offset", () => {
    expect(utcClock("2026-11-04T12:15:02.123+03:00")).toBe("09:15:02.123");
    expect(utcDay("2026-11-04T01:00:00+03:00")).toBe("2026-11-03");
  });

  it("leaves an unparseable timestamp as written", () => {
    expect(utcClock("not a time")).toBe("not a time");
  });
});
