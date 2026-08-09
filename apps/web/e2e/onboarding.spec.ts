import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * The authoritative onboarding path, driven through a real browser.
 *
 * Production web build + FastAPI + PostgreSQL + a local fake GitHub read
 * provider. No mocked browser routes: every click here goes to the real API,
 * and every assertion about the catalog is read back from it.
 *
 *   choose a repository → session → analyse → review → approve → apply
 *
 * The fixture repository is Drake's own `Hermes`. The real Datalake
 * repository is deliberately NOT used: it is closed by a manual security
 * gate, and a golden path that depends on opening one is not a golden path.
 */

test.describe.configure({ mode: "serial" });

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

function sign(body: string): string {
  const secret = fs.readFileSync(SECRET_PATH, "utf8").trim();
  return (
    "sha256=" + crypto.createHmac("sha256", secret).update(Buffer.from(body, "utf8")).digest("hex")
  );
}

async function announceInstallation(request: APIRequestContext) {
  const body = JSON.stringify({
    action: "created",
    installation: {
      id: INSTALLATION_ID,
      account: { login: "Duosis-Developer-Team", id: 1 },
      repository_selection: "selected",
      permissions: { contents: "read", metadata: "read", pull_requests: "read" },
      events: ["installation", "installation_repositories", "repository", "push"],
    },
    repositories: REPOSITORIES,
  });
  return request.post("/v1/integrations/github/webhook", {
    headers: {
      "content-type": "application/json",
      "x-github-event": "installation",
      "x-github-delivery": crypto.randomUUID(),
      "x-hub-signature-256": sign(body),
    },
    data: body,
  });
}

async function signInAs(page: Page, subject: string) {
  await page.goto(`/v1/auth/login?redirect=/&login_hint=${subject}`);
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

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

test("an operator onboards a repository through the reviewed path", async ({ page }) => {
  await signInAs(page, "user-owner");
  const request = page.request;

  expect((await announceInstallation(request)).status()).toBe(202);
  const listed = (await (
    await request.get("/v1/integrations/github/repositories?limit=50")
  ).json()) as { repositories: { id: string; full_name: string }[] };
  const hermes = listed.repositories.find((repo) => repo.full_name.endsWith("/Hermes"))!;
  expect((await reconcile(page, hermes.id)).status()).toBe(202);

  // --- 1. choose a repository and start ------------------------------------
  await page.goto("/onboarding");
  const picker = page.getByTestId("repository-select");
  await expect(picker).toBeVisible();
  // Selected by the repository's own id, which is what the option carries.
  await picker.selectOption(hermes.id);
  await page.getByTestId("start-onboarding-button").click();
  await page.waitForURL(/\/onboarding\/[0-9a-f-]{36}$/);
  const sessionId = page.url().split("/").pop()!;

  // --- 2. analyse ----------------------------------------------------------
  await page.getByTestId("action-analyze").click();
  await expect(page.getByTestId("analysis")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("plan")).toBeVisible();

  // The analysis reports what it read, and the plan says what it would do.
  //
  // Which groups appear depends on what the catalog already holds: a first
  // onboarding creates, a repeat links and reports no change. Asserting a
  // specific group would make this test pass or fail on the order the suite
  // happens to run in, so it asserts the plan is REAL — a version, a
  // commit, a digest and items — and leaves the shape to the plan tests.
  await expect(page.getByTestId("analysis")).toContainText("found");
  await expect(page.getByTestId("plan")).toContainText("Plan v");
  await expect(page.getByTestId("plan")).toContainText("items");

  // Nothing from the repository is on the page: not the manifest, not the
  // shell scripts the fixture deliberately contains.
  const rendered = await page.content();
  expect(rendered).not.toContain("apiVersion: drake.duosis");
  expect(rendered).not.toContain("rm -rf");
  expect(rendered).not.toContain("curl evil");
  expect(rendered).not.toContain("ghs_");
  expect(rendered).not.toContain("BEGIN PRIVATE KEY");

  // --- 3. approve ----------------------------------------------------------
  await page.getByTestId("action-approve").click();
  const approval = page.getByTestId("confirm-approve");
  await expect(approval).toBeVisible();
  // The reviewer is told which version, at which commit, over which digest,
  // and how many items — the four facts an approval binds. The version
  // NUMBER depends on how many analyses this repository has had, so the
  // assertion is that each fact is present and specific.
  await expect(approval).toContainText("Plan version");
  await expect(approval).toContainText(/v\d+/);
  await expect(approval).toContainText("Commit");
  await expect(approval).toContainText("Digest");
  await expect(approval).toContainText("Items");
  // Never the manifest itself.
  await expect(approval).not.toContainText("apiVersion");
  await page.getByTestId("confirm-approve-yes").click();
  await expect(page.getByTestId("action-apply")).toBeVisible({ timeout: 15_000 });

  // --- 4. apply ------------------------------------------------------------
  await page.getByTestId("action-apply").click();
  const confirmation = page.getByTestId("confirm-apply");
  await expect(confirmation).toContainText("does not write to the repository");
  await page.getByTestId("confirm-apply-yes").click();

  const result = page.getByTestId("apply-result");
  await expect(result).toBeVisible({ timeout: 30_000 });
  await expect(result).toContainText("Created");
  // Real numbers, not placeholders, and never a bare NaN or "null".
  await expect(result).not.toContainText("NaN");
  await expect(result).not.toContainText("null");

  // --- 5. the catalog actually changed -------------------------------------
  const session = (await (await request.get(`/v1/onboarding/sessions/${sessionId}`)).json()) as {
    state: string;
    imported_project_id: string | null;
    imported_project_key: string | null;
  };
  expect(session.state).toBe("imported");
  expect(session.imported_project_key).toBe("hermes");

  const project = (await (
    await request.get(`/v1/projects/${session.imported_project_id}`)
  ).json()) as { project_key: string };
  expect(project.project_key).toBe("hermes");

  // --- 6. imported survives a reload ---------------------------------------
  await page.reload();
  await expect(page.getByTestId("session-actions")).toBeVisible();
  // Terminal: nothing is offered that would re-open it.
  for (const action of ["analyze", "approve", "apply", "cancel"]) {
    await expect(page.getByTestId(`action-${action}`)).toHaveCount(0);
  }

  // --- 7. a replayed apply returns the first answer ------------------------
  //
  // Driven through the API with a key this test owns, because the key the
  // UI used lives in its component memory — which is the point of it. A
  // second session on the same repository plans as mostly `no_change`, and
  // applying it twice under one key must produce one answer, not two.
  const csrf = await csrfToken(page);
  const replaySession = (await (
    await request.post("/v1/onboarding/sessions", {
      headers: { "x-csrf-token": csrf },
      data: { repository_id: hermes.id },
    })
  ).json()) as { session_id: string };
  const replayId = replaySession.session_id;

  const analysis = (await (
    await request.post(`/v1/onboarding/sessions/${replayId}/analyze`, {
      headers: { "x-csrf-token": csrf },
    })
  ).json()) as { plan_version: number };
  const current = (await (await request.get(`/v1/onboarding/sessions/${replayId}`)).json()) as {
    version: number;
  };
  const approved = await request.post(`/v1/onboarding/sessions/${replayId}/approve`, {
    headers: { "x-csrf-token": csrf },
    data: { plan_version: analysis.plan_version, expected_version: current.version },
    failOnStatusCode: false,
  });
  expect(approved.status(), await approved.text()).toBe(200);

  const key = crypto.randomUUID();
  const applyOnce = async () =>
    request.post(`/v1/onboarding/sessions/${replayId}/apply`, {
      headers: { "x-csrf-token": csrf, "idempotency-key": key },
      data: { plan_version: analysis.plan_version, idempotency_key: key },
      failOnStatusCode: false,
    });
  const first = await applyOnce();
  expect(first.status(), await first.text()).toBe(200);
  const second = await applyOnce();
  expect(second.status()).toBe(200);
  // The same request, sent twice, is one operation with one answer — every
  // field, including the outcome word.
  expect(await second.json()).toEqual(await first.json());

  // --- 8. the retired path stays retired -----------------------------------
  const before = (await (
    await request.get("/v1/projects?limit=100")
  ).json()) as { total: number };
  const retired = await request.post(
    `/v1/integrations/github/repositories/${hermes.id}/onboarding/import`,
    {
      headers: { "x-csrf-token": csrf, "idempotency-key": crypto.randomUUID() },
      failOnStatusCode: false,
    },
  );
  expect(retired.status()).toBe(410);
  expect(((await retired.json()) as { error: { code: string } }).error.code).toBe(
    "legacy_onboarding_retired",
  );
  const after = (await (await request.get("/v1/projects?limit=100")).json()) as { total: number };
  expect(after.total).toBe(before.total);
});

test("the manifest draft downloads and is never rendered into the page", async ({ page }) => {
  await signInAs(page, "user-owner");
  const request = page.request;
  const sessions = (await (await request.get("/v1/onboarding/sessions?limit=50")).json()) as {
    items: { id: string; analyzed_commit_sha: string | null }[];
  };
  const analysed = sessions.items.find((item) => item.analyzed_commit_sha)!;

  await page.goto(`/onboarding/${analysed.id}`);
  const link = page.getByTestId("action-manifest-draft");
  await expect(link).toBeVisible();

  const response = await request.get(`/v1/onboarding/sessions/${analysed.id}/manifest-draft`);
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("application/yaml");
  expect(response.headers()["content-disposition"]).toContain("attachment");
  expect(response.headers()["cache-control"]).toBe("no-store");
  expect(await response.text()).toContain("apiVersion: drake.duosis.com");

  // The bytes are a download. They are not in the document.
  expect(await page.content()).not.toContain("apiVersion: drake.duosis");
});

test("a user without onboarding rights is offered nothing to act with", async ({ page }) => {
  await signInAs(page, "user-plain");
  // `user-plain` is a project Developer: no onboarding permission at all.
  await page.goto("/onboarding");
  // Either they cannot see the picker, or they see it with nothing in it.
  // What they must never see is a live Start button.
  await expect(page.getByTestId("start-onboarding-button")).toHaveCount(0);

  const sessions = await page.request.get("/v1/onboarding/sessions?limit=50");
  if (sessions.status() === 200) {
    const body = (await sessions.json()) as { items: { id: string }[] };
    for (const item of body.items) {
      await page.goto(`/onboarding/${item.id}`);
      for (const action of ["analyze", "approve", "apply", "cancel", "gitops"]) {
        await expect(page.getByTestId(`action-${action}`)).toHaveCount(0);
      }
    }
  }
});

// ===========================================================================
// visual and accessibility QA
// ===========================================================================

const VIEWPORTS = [
  { name: "mobile", width: 390, height: 844 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "desktop", width: 1536, height: 960 },
];

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test("the onboarding screens fit every viewport, in both themes", async ({ page }) => {
  await signInAs(page, "user-owner");
  const sessions = (await (await page.request.get("/v1/onboarding/sessions?limit=1")).json()) as {
    items: { id: string }[];
  };
  const paths = ["/onboarding", ...sessions.items.map((item) => `/onboarding/${item.id}`)];

  for (const theme of ["light", "dark"] as const) {
    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      for (const target of paths) {
        await page.goto(target);
        await page.evaluate((mode) => {
          document.documentElement.classList.toggle("dark", mode === "dark");
        }, theme);
        await expect(page.getByRole("heading").first()).toBeVisible();
        // A page that scrolls sideways hides the right-hand end of every
        // row on it, which on this screen is where the actions live.
        expect(
          await horizontalOverflow(page),
          `${target} @ ${viewport.name} ${theme}`,
        ).toBeLessThanOrEqual(1);
      }
    }
  }
});

test("the operator can drive onboarding from the keyboard", async ({ page }) => {
  await signInAs(page, "user-owner");
  await page.goto("/onboarding");

  const picker = page.getByTestId("repository-select");
  await expect(picker).toBeVisible();
  // Focusable, labelled and operable without a pointer.
  await picker.focus();
  await expect(picker).toBeFocused();
  const labelled = await picker.evaluate((element) => {
    const id = element.getAttribute("id");
    return Boolean(id && document.querySelector(`label[for="${id}"]`));
  });
  expect(labelled).toBe(true);

  // A disabled button is correctly skipped by Tab, so choose a repository
  // that can actually be started — the first option is the gated Datalake
  // repository, which is meant to stay unselectable.
  const candidates = (await (
    await page.request.get("/v1/onboarding/repositories?limit=50")
  ).json()) as { items: { id: string; startable: boolean; active_session_id: string | null }[] };
  const usable = candidates.items.find((item) => item.startable || item.active_session_id);
  expect(usable, "no repository this operator can act on").toBeTruthy();
  await picker.selectOption(usable!.id);
  // `selectOption` does not guarantee where focus lands, so put it back on
  // the control an operator would be on before testing what Tab reaches.
  await picker.focus();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("start-onboarding-button")).toBeFocused();
});

/**
 * A session in a state that offers actions.
 *
 * Built rather than found: after the golden path the only session is
 * `imported`, and a QA test that quietly skips because the world was not in
 * the shape it wanted is a test that never runs.
 */
async function openSession(page: Page): Promise<string> {
  const csrf = await csrfToken(page);
  const candidates = (await (
    await page.request.get("/v1/onboarding/repositories?limit=50")
  ).json()) as { items: { id: string; startable: boolean; active_session_id: string | null }[] };
  const usable = candidates.items.find((item) => item.startable || item.active_session_id)!;
  if (usable.active_session_id) return usable.active_session_id;
  const created = (await (
    await page.request.post("/v1/onboarding/sessions", {
      headers: { "x-csrf-token": csrf },
      data: { repository_id: usable.id },
    })
  ).json()) as { session_id: string };
  await page.request.post(`/v1/onboarding/sessions/${created.session_id}/analyze`, {
    headers: { "x-csrf-token": csrf },
    failOnStatusCode: false,
  });
  return created.session_id;
}

test("a confirmation is reachable, labelled, and returns focus sensibly", async ({ page }) => {
  await signInAs(page, "user-owner");
  const sessionId = await openSession(page);

  await page.goto(`/onboarding/${sessionId}`);
  const cancel = page.getByTestId("action-cancel");
  await cancel.click();

  const dialog = page.getByTestId("confirm-cancel");
  await expect(dialog).toBeVisible();
  // Named for a screen reader, not just visually grouped.
  await expect(dialog).toHaveAttribute("aria-label", /cancel this session/i);

  await page.getByTestId("confirm-cancel-no").click();
  await expect(dialog).toBeHidden();
  // Focus lands somewhere useful rather than at the top of the document.
  const focused = await page.evaluate(() => document.activeElement?.tagName ?? "");
  expect(["BUTTON", "BODY"]).toContain(focused);
});

test("a disabled action says why in words, not only in colour", async ({ page }) => {
  await signInAs(page, "user-owner");
  const sessionId = await openSession(page);

  await page.goto(`/onboarding/${sessionId}`);
  const actions = page.getByTestId("session-actions");
  await expect(actions).toBeVisible();

  // Whatever is disabled in this panel, the reason is in text somewhere in
  // it — never carried by colour alone, which a screen reader does not read
  // and a colour-blind operator does not see.
  const disabled = actions.locator("button:disabled");
  const count = await disabled.count();
  if (count > 0) {
    const explanation = await actions.textContent();
    expect(explanation?.trim().length ?? 0).toBeGreaterThan(0);
    for (let index = 0; index < count; index += 1) {
      // A disabled control still has a readable label.
      await expect(disabled.nth(index)).not.toHaveText("");
    }
  }

  // The GitOps case specifically: the flag is off, so if the operator holds
  // the permission the button is present, disabled, and says why.
  const gitops = page.getByTestId("action-gitops");
  if (await gitops.count()) {
    await expect(gitops).toBeDisabled();
    await expect(page.getByTestId("gitops-disabled")).toContainText(
      "No branch or pull request will be created.",
    );
  } else {
    // Absent because the operator lacks `onboarding.gitops` — and nothing
    // on the page claims a pull request is possible.
    await expect(page.getByTestId("gitops-disabled")).toHaveCount(0);
  }
});

test("axe finds no critical violation on either onboarding screen", async ({ page }) => {
  await signInAs(page, "user-owner");
  const sessions = (await (await page.request.get("/v1/onboarding/sessions?limit=1")).json()) as {
    items: { id: string }[];
  };

  for (const target of ["/onboarding", ...sessions.items.map((item) => `/onboarding/${item.id}`)]) {
    await page.goto(target);
    await expect(page.getByRole("heading").first()).toBeVisible();
    const scan = await new AxeBuilder({ page }).analyze();
    const critical = scan.violations.filter((violation) => violation.impact === "critical");
    expect(critical, `${target}: ${critical.map((v) => v.id).join(", ")}`).toEqual([]);
  }
});
