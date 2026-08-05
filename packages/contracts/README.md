# @drake/contracts

Machine-validated contracts shared across Drake:

- **Project manifest** (`.drake/project.yaml`, kind `ProjectObservability`) —
  a repository's observability intent.
- **Tenant snapshot** — the payload application adapters deliver to Drake.
- **Backup events** — CloudEvents-style backup/restore evidence.

Schemas live in [`schemas/`](schemas/), example manifests in
[`fixtures/valid/`](fixtures/valid/), and rejection cases in
[`fixtures/invalid/`](fixtures/invalid/). All fixtures use fictional projects;
any credential-looking strings inside `fixtures/invalid/` are deliberately fake
test values.

## Validation model

Validation is two-layered:

1. **JSON Schema** (draft 2019-09, Ajv): structure, required fields, enums,
   unknown-field rejection, conditional requirements.
2. **Content policy**: rejects credential values, private keys, bearer tokens,
   inline SQL, and plaintext `http://` endpoints — while allowing legitimate
   secret *reference names* such as `connectionSecretRef`. Findings report the
   JSON path and rule id, never the matched value.

## CLI

```bash
pnpm --filter @drake/contracts build
node packages/contracts/dist/cli.js path/to/.drake/project.yaml
node packages/contracts/dist/cli.js --schema tenant-snapshot snapshot.json
```

Exit codes: `0` valid, `1` invalid, `2` usage/IO error.

## Development

```bash
pnpm --filter @drake/contracts test        # build + vitest
pnpm --filter @drake/contracts typecheck
pnpm --filter @drake/contracts lint
```

See [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for versioning rules.
