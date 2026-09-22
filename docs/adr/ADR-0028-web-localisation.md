# ADR-0028 — Web localisation: English and Turkish from one typed catalogue

Drake's operators read Turkish. The product's copy was English only, and
every previous "we'll translate later" in this codebase's history became a
screen where half the sentences were in one language and the raw backend
token stood in for the other half. This records how localisation is built
so that cannot happen here.

## Decision

1. **Two locales, one source.** `en` is the source language every key is
   authored in; `tr` is the second. Both live in `apps/web/src/lib/i18n`.
2. **No library.** The runtime is a few hundred lines: a React context, a
   `useT(namespace)` hook, an ICU subset (`{name}`, `plural`, `select`)
   and `Intl` for numbers and plural rules. A dependency here would be one
   more thing the dependency scanner gates a release on, for a problem that
   is small and fully understood.
3. **Completeness is a type, not a test.** Each area ships one file with
   `en` and `tr` side by side, and `tr` is typed as the shape of `en`. A key
   present in English and absent in Turkish does not compile. There is a
   runtime fallback to English, but nothing in the repository can reach it
   without also failing typecheck, so it exists for defence, not for use.
4. **Copy goes through the catalogue; data does not.** Anything a person
   reads — JSX text, `aria-label`, `title`, `placeholder`, chart titles,
   empty and error states — is a key. Identifiers the API returns (service
   keys, namespaces, digests, cluster ids) and backend error text are data
   and are shown as they are. Kubernetes kinds used as proper nouns stay
   in English in both locales, as do Drake, GitHub, Helm, SLO and UTC.
5. **Timestamps stay UTC in both locales.** The product's one timestamp
   form is the one operators compare against pod logs and audit records
   (ADR-0011's honesty applies to time too). Localisation changes the words
   around a time, never the time.
6. **Preference is a client preference, applied before paint.** It is
   stored beside the theme, applied to `<html lang>` by an inline script
   before hydration, and read through `useSyncExternalStore` so the server
   snapshot (English) hydrates without a mismatch and the client switches
   in the same frame the shell first renders. No cookie, no per-locale
   route prefix, no server rendering per language: the app is fully
   client-rendered behind a session gate, so a server-side locale would
   buy a dynamic layout and nothing else.
7. **Every existing English test keeps passing untouched.** A component
   rendered without a provider sees English. Each area adds one test that
   renders in Turkish to prove the wiring; the translation itself is
   reviewed by reading the file, where the English source sits directly
   above every Turkish string.

## Consequences

- Adding a locale is one more typed tree per namespace and one entry in
  `LOCALES`; nothing else changes. Adding a string is one key in `en` and
  the compiler pointing at `tr`.
- `scripts/i18n-scan.mjs` is the checklist of what is still hardcoded. It
  over-reports by design and is not a CI gate; a hit is something to read.
- Turkish has no cardinal plural, so its strings say `{count} olay` where
  English needs `one`/`other`. That asymmetry is in the message, not in the
  component, which is the point of never concatenating fragments in JSX.
- Text the backend produces in English (validation messages, GitHub App
  errors) still arrives in English. Localising the API's error vocabulary
  is a separate decision, and until it is taken those strings are shown
  verbatim rather than machine-guessed.
