# Claude Code usage on the Development map

The Development map (Agents → Development) shows, per person and repository,
what Claude Code cost and did. It gets there two ways: live telemetry from
the moment a developer connects, and — once — their history from before.

## Connect (each developer, once)

From a checkout of the Tracesis repository:

```sh
scripts/connect-claude-code.sh <your-github-login>
```

It asks for the ingest token without echoing it (an administrator hands it
out), backs up `~/.claude/settings.json` and adds the telemetry variables to
its `env` block. Claude Code reads its settings when a session starts, so
**only sessions started afterwards report**: restart open sessions (in VS
Code, `Developer: Reload Window`). Use Claude Code 2.1.269 or later
(`claude update`) so sessions name their repository.

A clone made before a repository was renamed still reports the old name
(for example `drake` for Tracesis). The collector asks GitHub once which
repository that name became, so these sessions count without re-cloning.

## Bring your last 30 days (each developer, once)

Telemetry never carries history. Claude Code keeps it on your machine, and
this turns it into counts Tracesis can import:

```sh
python3 scripts/export-claude-code-usage.py --login <your-github-login> > claude-code-usage.json
```

- It reads `~/.claude/projects/**/*.jsonl` and keeps only sessions started
  in a clone of the Duosis-Developer-Team organisation.
- What it writes: per day and repository, cost, tokens, requests, prompts,
  sessions, models and tool names; your login and your Claude account's
  uuid. **No prompt, answer, tool input or output, file content, path or
  e-mail ever leaves your machine** — open the file and check.
- It stops where your telemetry starts (when `connect-claude-code.sh`
  switched it on), so nothing is counted twice. `--until <ISO time>` and
  `--days <n>` (up to 90) override.
- Cost is Claude Code's API-price estimate, not what a subscription bills.

Send `claude-code-usage.json` to a Tracesis administrator.

## Import (administrator)

Copy the file into an API pod and import it; a dry run first shows where
every repository lands:

```sh
kubectl -n drake-prod cp claude-code-usage.json <api-pod>:/tmp/usage.json -c api
kubectl -n drake-prod exec <api-pod> -c api -- \
  python -m drake_api.dev_activity.usage_import /tmp/usage.json --dry-run
kubectl -n drake-prod exec <api-pod> -c api -- \
  python -m drake_api.dev_activity.usage_import /tmp/usage.json
kubectl -n drake-prod exec <api-pod> -c api -- rm /tmp/usage.json
```

- Rows are kept under `backfill:<Claude account uuid>` with the login as
  the person: the same person as their live telemetry. The telemetry
  harvest never replaces them.
- Re-importing a file replaces that person's imported rows for the days it
  covers; it never double counts.
- Repositories outside the catalog and the watched organisations are
  reported and skipped. A file with an `@` anywhere is refused.
- `--identity <uuid>` sets the account when the file has none.
