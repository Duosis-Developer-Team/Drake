# Web i18n — how strings work in Drake

Two locales: `en` (source) and `tr`. No library; `src/lib/i18n` is ~300 lines.

## Rules

1. **Every user-visible string goes through `useT`.** JSX text, `aria-label`,
   `title`, `placeholder`, `alt`, empty-state copy, button labels, tooltips,
   chart axis titles, toast text. If a person can read it, it is in the
   catalogue. Identifiers the API returns (service keys, namespaces, image
   digests, cluster ids) are data, not copy, and stay as they are.
2. **One namespace per area, one file each** under `messages/`. A component
   under `components/incidents` or `app/incidents` uses `useT("incidents")`.
   Shared vocabulary (Retry, Loading, Healthy, Critical, field names) lives in
   `common` — use it, do not redefine it. Do not edit `common.ts` or another
   area's file; if you truly need a shared string that is missing, put it in
   your own namespace and say so in the PR.
3. **English above, Turkish below, in the same file.** `tr` is typed as the
   shape of `en`: a missing Turkish key is a typecheck error. Never leave a
   Turkish value in English "for now".
4. **Keys are stable identifiers, not English sentences.** `row.openedAt`, not
   `Opened at`. Nest by screen/section: `list.title`, `list.empty`,
   `detail.timeline.heading`, `form.field.name`.
5. **Enums** (`STATE_LABELS`-style records in `lib/`): keep the record for
   non-React code if it has other consumers, but components render
   `t("state.open")` / `t.dyn("state", value)`. Where the record exists only
   to produce a label, delete it and move the strings into the catalogue.
6. **Plurals and variables** use the ICU subset in `format.ts`:
   `"{count, plural, one {# incident} other {# incidents}}"` in `en`;
   Turkish has no cardinal plural, so `"{count} olay"`. `{name}` interpolates.
   Never build sentences by string concatenation in JSX: word order differs.
7. **Dates, durations, numbers** come from `useFormat()`:
   `fmt.relative(iso)`, `fmt.duration(seconds)`, `fmt.utc(iso)`, `fmt.number(n)`.
   Timestamps stay UTC in both languages — that is product policy, not a
   locale choice.
8. **Tests.** Existing tests assert English and run without a provider, so
   they keep passing. Add ONE test per area that wraps the main screen in
   `<LocaleProvider locale="tr">` and asserts a couple of Turkish strings —
   that proves the wiring, not the translation. Do not rewrite English tests
   to Turkish.
9. **Do not translate**: product name Drake, Kubernetes nouns used as proper
   nouns (Pod, Deployment as a K8s kind, Namespace, StatefulSet, Ingress),
   GitHub, Helm, SLO, SLI, UTC, HTTP status codes, log/error text quoted from
   the backend, code and identifiers.

## Turkish glossary (use these consistently)

| English | Türkçe | note |
|---|---|---|
| Command Center | Komuta Merkezi | |
| Overview | Genel bakış | |
| Estate | Varlıklar | nav group: the platform's own inventory |
| Operations | Operasyon | nav group |
| Configuration | Yapılandırma | nav group |
| Project / Environment / Service | Proje / Ortam / Servis | |
| Cluster / Namespace / Workload | Küme / Namespace / İş yükü | |
| Inventory / Resource | Envanter / Kaynak | |
| Service health | Servis sağlığı | |
| Objectives (SLO) / Error budget / Burn rate | Hedefler (SLO) / Hata bütçesi / Tüketim hızı | |
| Incident / Alert | Olay / Uyarı | |
| Open / Acknowledged / Resolved | Açık / Onaylandı / Çözüldü | incident states |
| Severity / Priority | Önem derecesi / Öncelik | |
| Critical / Warning / Healthy / Degraded / Unknown / Stale | Kritik / Uyarı / Sağlıklı / Bozulmuş / Bilinmiyor / Güncel değil | |
| Deployment (Drake's) / Rollout | Dağıtım / Rollout | a K8s `Deployment` kind stays "Deployment" |
| Verdict / Evidence / Coverage / Signal | Karar / Kanıt / Kapsam / Sinyal | |
| Protection / Backup / Recoverability / Policy | Koruma / Yedek / Kurtarılabilirlik / Politika | |
| Notification / Delivery / Notification policy | Bildirim / Teslimat / Bildirim politikası | |
| Integration / GitHub App / Webhook | Entegrasyon / GitHub App / Webhook | |
| Onboarding / Session / Plan / Apply | Katılım / Oturum / Plan / Uygula | |
| Admin / Identity / Permission / Scope / Role | Yönetim / Kimlik / Yetki / Kapsam / Rol | |
| Silence / Silenced | Sessize alma / Sessize alındı | |
| Time range / Window / Last 24 hours | Zaman aralığı / Pencere / Son 24 saat | |
| Sign in / Sign out / Session expired | Giriş yap / Çıkış yap / Oturum süresi doldu | |
| Permission denied | Yetkiniz yok | |
| Loading / Retry / Refresh / Search | Yükleniyor / Yeniden dene / Yenile / Ara | |
| Attention queue / Root cause | Dikkat kuyruğu / Kök neden | command center |
| Capacity / Headroom / Risk | Kapasite / Boşluk / Risk | |
| Freshness / Fresh / Stale | Tazelik / Güncel / Güncel değil | |

Register: formal, second-person plural ("yetkiniz yok", "yeniden deneyin"),
no slang, no Anglicisms where a common Turkish word exists ("servis" and
"küme" are established; "deploy etmek" is not). Sentence case, not Title Case:
"Bildirim politikaları", not "Bildirim Politikaları". Proper nouns keep their
own capitalisation. Suffixes on proper nouns take an apostrophe
("Komuta Merkezi'ne", "GitHub'a").

## Wiring

- `LocaleProvider` wraps the app in `components/shell/AppShell.tsx`.
- `LOCALE_INIT_SCRIPT` in `app/layout.tsx` sets `<html lang>` before paint.
- `components/shell/LanguageControl.tsx` is the EN/TR switch (top bar and
  drawer footer, mirroring `ThemeControl`).
- `getTranslator(ns, locale)` and `formattersFor(locale)` are for code that
  is not a component but is handed a locale explicitly.
