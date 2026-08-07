import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page, type APIRequestContext } from "@playwright/test";

/**
 * Real-flow E2E for the GitHub App boundary: fake OIDC + FastAPI +
 * PostgreSQL + a local fake GitHub REST API. No mocked browser routes.
 *
 * The webhook is delivered exactly as GitHub would deliver it — raw bytes
 * plus an HMAC header — so the signature path is exercised end to end
 * rather than stubbed.
 */

test.describe.configure({ mode: "serial" });

const GITHUB_ORIGIN = "http://127.0.0.1:59097";
const SECRET_PATH = path.resolve(__dirname, "../../../.e2e-github/webhook-secret");
const INSTALLATION_ID = 55501;

const REPOSITORIES = [
  { id: 900001, name: "Hermes", private: true },
  { id: 900002, name: "logislot", private: true },
  { id: 900003, name: "Datalake-Platform-GUI", private: true },
  { id: 900004, name: "Fikir-Sepeti", private: true },
].map((repo) => ({
  ...repo,
  node_id: `R_${repo.name}`,
  full_name: `Duosis-Developer-Team/${repo.name}`,
}));

function webhookSecret(): string {
  return fs.readFileSync(SECRET_PATH, "utf8").trim();
}

/** Sign the exact bytes we are about to send, like GitHub does. */
function sign(body: string): string {
  return (
    "sha256=" +
    crypto.createHmac("sha256", webhookSecret()).update(Buffer.from(body, "utf8")).digest("hex")
  );
}

async function deliver(
  request: APIRequestContext,
  options: {
    event: string;
    body: unknown;
    deliveryId?: string;
    signature?: string;
    raw?: string;
  },
) {
  const raw = options.raw ?? JSON.stringify(options.body);
  return request.post("/v1/integrations/github/webhook", {
    headers: {
      "content-type": "application/json",
      "x-github-event": options.event,
      "x-github-delivery": options.deliveryId ?? crypto.randomUUID(),
      "x-hub-signature-256": options.signature ?? sign(raw),
    },
    // Buffer, not string: Playwright must send these exact bytes, because
    // the signature was computed over them.
    data: Buffer.from(raw, "utf8"),
    failOnStatusCode: false,
  });
}

function installationPayload(action: string) {
  return {
    action,
    installation: {
      id: INSTALLATION_ID,
      account: { login: "Duosis-Developer-Team", type: "Organization" },
      app_slug: "drake",
      repository_selection: "selected",
      permissions: { metadata: "read", administration: "read", actions: "read" },
      events: ["installation", "installation_repositories", "repository"],
    },
    repositories: REPOSITORIES,
  };
}

async function signInAs(page: Page, subject: string) {
  await page.goto(`/v1/auth/login?redirect=/&login_hint=${subject}`);
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

/** POSTs to Drake carry the session CSRF token, exactly like the browser. */
async function csrfToken(page: Page): Promise<string> {
  const me = (await (await page.request.get("/v1/me")).json()) as { csrf_token: string };
  return me.csrf_token;
}

async function reconcile(page: Page, repositoryId: string) {
  return page.request.post(`/v1/integrations/github/repositories/${repositoryId}/reconcile`, {
    headers: { "x-csrf-token": await csrfToken(page) },
    failOnStatusCode: false,
  });
}

async function fakeGitHubCalls(request: APIRequestContext): Promise<string[]> {
  const response = await request.get(`${GITHUB_ORIGIN}/__calls`);
  return ((await response.json()) as { calls: string[] }).calls;
}

test.beforeAll(async ({ request }) => {
  await request.post(`${GITHUB_ORIGIN}/__reset`);
});

test("a signed installation delivery onboards the catalog", async ({ page }) => {
  await signInAs(page, "user-owner");

  const accepted = await deliver(page.request, { event: "installation", body: installationPayload("created") });
  expect(accepted.status()).toBe(202);

  await page.goto("/integrations/github");
  await expect(page.getByTestId("github-status-card")).toBeVisible();
  await expect(page.getByTestId("github-status-card").getByText("configured")).toBeVisible();

  await expect(page.getByTestId("installation-list")).toBeVisible();
  await expect(
    page.getByTestId("installation-list").getByText("Duosis-Developer-Team", { exact: true }),
  ).toBeVisible();

  const repositories = page.getByTestId("repository-list");
  for (const repo of REPOSITORIES) {
    await expect(repositories.getByText(repo.full_name)).toBeVisible();
  }
});

test("the Datalake manual security gate blocks onboarding and every upstream call", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await page.goto("/integrations/github");
  await expect(page.getByTestId("repository-list")).toBeVisible();

  const datalake = page
    .getByTestId("repository-card")
    .filter({ hasText: "Datalake-Platform-GUI" });
  await expect(datalake.getByText("blocked", { exact: true })).toBeVisible();
  await expect(datalake.getByTestId("security-gate-warning")).toBeVisible();
  await expect(datalake.getByTestId("reconcile-button")).toBeDisabled();

  const request = page.request;
  // The gate is enforced by the API too, not only by a disabled button —
  // and it refuses BEFORE any GitHub call is made.
  const before = await fakeGitHubCalls(request);
  const listed = await request.get("/v1/integrations/github/repositories?limit=50");
  const body = (await listed.json()) as {
    repositories: { id: string; full_name: string; onboarding_state: string }[];
  };
  const row = body.repositories.find((entry) => entry.full_name.endsWith("Datalake-Platform-GUI"))!;
  expect(row.onboarding_state).toBe("blocked");

  const refused = await reconcile(page, row.id);
  expect(refused.status()).toBe(409);
  expect(await fakeGitHubCalls(request)).toEqual(before);
});

test("replay is idempotent and a forged replay is refused", async ({ page }) => {
  await signInAs(page, "user-owner");
  const request = page.request;
  const deliveryId = crypto.randomUUID();
  const payload = installationPayload("created");

  const first = await deliver(request, { event: "installation", body: payload, deliveryId });
  expect(first.status()).toBe(202);

  // Identical bytes, same delivery id: acknowledged, no second effect.
  const replay = await deliver(request, { event: "installation", body: payload, deliveryId });
  expect(replay.status()).toBe(202);
  expect((await replay.json()).status).toBe("duplicate");

  // Same delivery id, DIFFERENT bytes, validly signed: fail closed.
  const forged = await deliver(request, {
    event: "installation",
    body: { ...payload, action: "deleted" },
    deliveryId,
  });
  expect(forged.status()).toBe(409);

  // Wrong signature never reaches the parser.
  const unsigned = await deliver(request, {
    event: "installation",
    body: payload,
    signature: "sha256=" + "0".repeat(64),
  });
  expect(unsigned.status()).toBe(401);

  // A body that is not even JSON, with a valid signature, is a 400 — not a crash.
  const notJson = await deliver(request, { event: "installation", body: null, raw: "not-json" });
  expect(notJson.status()).toBe(400);

  // An event outside the allowlist is refused, not silently absorbed.
  const outside = await deliver(request, { event: "push", body: payload });
  expect(outside.status()).toBe(401);

  // `ping` is the one event we acknowledge without doing domain work.
  const ping = await deliver(request, { event: "ping", body: { zen: "Design for failure." } });
  expect(ping.status()).toBe(202);
  expect((await ping.json()).status).toBe("acknowledged");
});

test("a dry-run reconciliation reports governance honestly", async ({ page }) => {
  await signInAs(page, "user-owner");
  await page.goto("/integrations/github");
  await expect(page.getByTestId("repository-list")).toBeVisible();

  // Hermes is governed in the fake: protection, reviews, checks, scanning.
  const hermes = page.getByTestId("repository-card").filter({ hasText: "/Hermes" });
  await hermes.getByTestId("reconcile-button").click();
  await expect(hermes.getByTestId("policy-result")).toBeVisible();
  await expect(hermes.getByTestId("policy-result").getByText("dry run")).toBeVisible();

  // Fikir-Sepeti is governed by a RULESET rather than classic protection.
  // The ruleset list endpoint carries no rule detail, so a pass here can
  // only come from the effective-rules endpoint actually being consulted.
  const fikir = page.getByTestId("repository-card").filter({ hasText: "/Fikir-Sepeti" });
  await fikir.getByTestId("reconcile-button").click();
  await expect(fikir.getByTestId("policy-result")).toBeVisible();
  await expect(fikir.getByTestId("blocking-violations")).toHaveCount(0);

  // logislot has no protection at all, so it must show a blocking violation
  // rather than a quiet pass.
  const logislot = page.getByTestId("repository-card").filter({ hasText: "/logislot" });
  await logislot.getByTestId("reconcile-button").click();
  await expect(logislot.getByTestId("blocking-violations")).toBeVisible();
  await expect(logislot.getByText("Default branch is protected")).toBeVisible();

  // Nothing Drake did may have been a write, and the effective-rules
  // endpoint is the one that answered the ruleset question.
  const calls = await fakeGitHubCalls(page.request);
  expect(calls.some((call) => call.includes("/rules/branches/"))).toBe(true);
  expect(calls.filter((call) => call.startsWith("POST") && !call.endsWith("/access_tokens"))).toEqual(
    [],
  );
  expect(calls.some((call) => call.startsWith("PUT") || call.startsWith("PATCH"))).toBe(false);
});

test("an unreadable upstream never manufactures a fresh pass", async ({ page }) => {
  await signInAs(page, "user-owner");
  const request = page.request;
  const listed = await request.get("/v1/integrations/github/repositories?limit=50");
  const body = (await listed.json()) as { repositories: { id: string; full_name: string }[] };
  const hermes = body.repositories.find((entry) => entry.full_name.endsWith("/Hermes"))!;

  const policyUrl = `/v1/integrations/github/repositories/${hermes.id}/policy`;
  const before = (await (await request.get(policyUrl)).json()) as {
    overall?: string;
    evaluated_at?: string;
    evidence_digest?: string;
  };

  await request.post(`${GITHUB_ORIGIN}/__mode/unavailable`);
  let response;
  try {
    response = await reconcile(page, hermes.id);
  } finally {
    await request.post(`${GITHUB_ORIGIN}/__mode/ok`);
  }

  // Either an honest upstream failure, or an evaluation that is not a pass.
  if (response.status() === 202) {
    expect((await response.json()).overall).not.toBe("pass");
  } else {
    expect([502, 503]).toContain(response.status());
  }

  // The stored snapshot is either untouched (the last good result survives
  // an outage) or replaced by something that is honestly not a pass. What
  // it must never be is a NEW pass produced while GitHub was unreadable.
  const after = (await (await request.get(policyUrl)).json()) as {
    overall?: string;
    evaluated_at?: string;
    evidence_digest?: string;
  };
  if (after.evaluated_at !== before.evaluated_at) {
    expect(after.overall).not.toBe("pass");
  } else {
    expect(after.evidence_digest).toBe(before.evidence_digest);
  }
});

test("removing one repository does not delete the installation", async ({ page }) => {
  await signInAs(page, "user-owner");
  const request = page.request;

  // A membership event names repositories; it says nothing about the App's
  // own installation. Collapsing the two is how one removal used to take
  // the whole installation with it.
  const removed = await deliver(request, {
    event: "installation_repositories",
    body: {
      action: "removed",
      installation: {
        id: INSTALLATION_ID,
        account: { login: "Duosis-Developer-Team", type: "Organization" },
      },
      repositories_removed: [REPOSITORIES.find((r) => r.name === "logislot")],
    },
  });
  expect(removed.status()).toBe(202);

  const installations = await (
    await request.get("/v1/integrations/github/installations")
  ).json();
  expect(installations.installations[0].state).toBe("active");

  const body = (await (
    await request.get("/v1/integrations/github/repositories?limit=50")
  ).json()) as { repositories: { full_name: string; access_state: string }[] };
  const logislot = body.repositories.find((r) => r.full_name.endsWith("/logislot"))!;
  const hermes = body.repositories.find((r) => r.full_name.endsWith("/Hermes"))!;
  expect(logislot.access_state).toBe("removed");
  expect(hermes.access_state).toBe("accessible");

  // Put it back so later scenarios see the full catalogue again.
  const restored = await deliver(request, {
    event: "installation_repositories",
    body: {
      action: "added",
      installation: {
        id: INSTALLATION_ID,
        account: { login: "Duosis-Developer-Team", type: "Organization" },
      },
      repositories_added: [REPOSITORIES.find((r) => r.name === "logislot")],
    },
  });
  expect(restored.status()).toBe(202);
});

test("a read-only user can see the integration but cannot drive it", async ({ page }) => {
  await signInAs(page, "user-plain");
  const request = page.request;
  await page.goto("/integrations/github");

  // No manage action anywhere on the screen.
  await expect(page.getByTestId("reconcile-button")).toHaveCount(0);

  const listed = await request.get("/v1/integrations/github/repositories?limit=50");
  expect([200, 403]).toContain(listed.status());
  if (listed.status() === 200) {
    const body = (await listed.json()) as { repositories: { id: string }[] };
    for (const repo of body.repositories) {
      const refused = await reconcile(page, repo.id);
      expect([403, 404]).toContain(refused.status());
    }
  }
});

test("no response on the integration surface leaks credential material", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  const request = page.request;
  const paths = [
    "/v1/integrations/github/status",
    "/v1/integrations/github/installations",
    "/v1/integrations/github/repositories?limit=50",
    "/v1/integrations/github/webhook-deliveries?limit=50",
  ];
  for (const target of paths) {
    const response = await request.get(target);
    expect(response.status()).toBe(200);
    const text = await response.text();
    expect(text).not.toContain("BEGIN RSA PRIVATE KEY");
    expect(text).not.toContain("BEGIN PRIVATE KEY");
    expect(text).not.toContain("gh" + "s_");
    expect(text).not.toContain(webhookSecret());
    // A minted JWT would appear as three base64url segments.
    expect(text).not.toMatch(/eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\./);
  }

  await page.goto("/integrations/github");
  await expect(page.getByTestId("github-status-card")).toBeVisible();
  const rendered = await page.content();
  expect(rendered).not.toContain(webhookSecret());
  expect(rendered).not.toContain("BEGIN PRIVATE KEY");
});
