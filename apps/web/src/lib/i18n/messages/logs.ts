/**
 * Strings for the `logs` area: the log explorer on a service. English above,
 * Turkish below, same shape — a key missing from `tr` fails typecheck.
 *
 * Log lines themselves are data and are shown exactly as the service wrote
 * them; only the words around them live here.
 */
import { defineMessages } from "../define";

export const logs = defineMessages({
  en: {
    title: "Logs",
    description: "What this workload wrote, newest first. Times are UTC.",
    range: { label: "Time range", last: "Last {range}" },
    severity: {
      label: "Severity",
      any: "All levels",
      debug: "Debug and above",
      info: "Info and above",
      warn: "Warnings and above",
      error: "Errors and above",
      fatal: "Fatal only",
    },
    band: {
      fatal: "Fatal",
      error: "Error",
      warn: "Warn",
      info: "Info",
      debug: "Debug",
      none: "—",
    },
    search: {
      label: "Search log lines",
      placeholder: "Search text in log lines",
    },
    pod: { label: "Pod", all: "All pods" },
    healthChecks: {
      hide: "Hide health checks",
      hidden: "{count, plural, one {# health-check line hidden} other {# health-check lines hidden}}",
      show: "Show them",
    },
    live: {
      label: "Live",
      on: "Live: refreshing every 10 seconds",
      off: "Live updates off",
    },
    action: {
      refresh: "Refresh",
      loadOlder: "Load older lines",
      loadingOlder: "Loading older lines…",
      copyLine: "Copy line",
      copied: "Copied",
      expand: "Show details",
      collapse: "Hide details",
      clearFilters: "Clear filters",
    },
    summary: {
      lines: "{count, plural, one {# line} other {# lines}}",
      errors: "{count, plural, one {# error} other {# errors}}",
      warnings: "{count, plural, one {# warning} other {# warnings}}",
      onlyErrors: "Show only errors",
      onlyWarnings: "Show only warnings",
      window: "{start} – {end} UTC",
      more: "More lines exist in this window",
      end: "Start of the window reached",
    },
    detail: {
      time: "Time (UTC)",
      severity: "Severity",
      pod: "Pod",
      container: "Container",
      trace: "Trace ID",
      span: "Span ID",
      fields: "Fields",
      raw: "Raw line",
    },
    state: {
      denied: {
        title: "Log access required",
        description:
          "Reading this service's logs needs the logs.view permission at its scope. Seeing a service does not include its logs.",
      },
      notConfigured: {
        title: "Log store not configured",
        description:
          "This Drake has no log store connected, so nothing was read. Logs are not missing; they are not being collected here.",
      },
      unavailable: {
        title: "Logs could not be read",
        description:
          "The log store did not answer just now. This says nothing about the service itself. Code: {reason}",
      },
      empty: {
        title: "No lines matched",
        description:
          "Nothing in this window matched the filters. That is not the same as no errors: widen the range or clear the filters.",
      },
      allHidden: {
        title: "Only health checks in this window",
        description: "Every line on this page is a Kubernetes health check.",
      },
      unavailableLink: "Try again",
    },
    redaction:
      "Values that look like passwords, tokens or keys are masked before storage. Masking is best effort, not a guarantee.",
  },
  tr: {
    title: "Loglar",
    description: "Bu iş yükünün yazdıkları, en yeni en üstte. Saatler UTC.",
    range: { label: "Zaman aralığı", last: "Son {range}" },
    severity: {
      label: "Seviye",
      any: "Tüm seviyeler",
      debug: "Ayıklama ve üstü",
      info: "Bilgi ve üstü",
      warn: "Uyarı ve üstü",
      error: "Hata ve üstü",
      fatal: "Yalnızca ölümcül",
    },
    band: {
      fatal: "Ölümcül",
      error: "Hata",
      warn: "Uyarı",
      info: "Bilgi",
      debug: "Ayıklama",
      none: "—",
    },
    search: {
      label: "Log satırlarında ara",
      placeholder: "Log satırlarında metin ara",
    },
    pod: { label: "Pod", all: "Tüm pod'lar" },
    healthChecks: {
      hide: "Sağlık kontrollerini gizle",
      hidden: "{count} sağlık kontrolü satırı gizlendi",
      show: "Göster",
    },
    live: {
      label: "Canlı",
      on: "Canlı: 10 saniyede bir yenileniyor",
      off: "Canlı güncelleme kapalı",
    },
    action: {
      refresh: "Yenile",
      loadOlder: "Daha eski satırları yükle",
      loadingOlder: "Daha eski satırlar yükleniyor…",
      copyLine: "Satırı kopyala",
      copied: "Kopyalandı",
      expand: "Ayrıntıları göster",
      collapse: "Ayrıntıları gizle",
      clearFilters: "Filtreleri temizle",
    },
    summary: {
      lines: "{count} satır",
      errors: "{count} hata",
      warnings: "{count} uyarı",
      onlyErrors: "Yalnızca hataları göster",
      onlyWarnings: "Yalnızca uyarıları göster",
      window: "UTC {start} – {end}",
      more: "Bu aralıkta daha fazla satır var",
      end: "Aralığın başına ulaşıldı",
    },
    detail: {
      time: "Zaman (UTC)",
      severity: "Seviye",
      pod: "Pod",
      container: "Konteyner",
      trace: "Trace kimliği",
      span: "Span kimliği",
      fields: "Alanlar",
      raw: "Ham satır",
    },
    state: {
      denied: {
        title: "Log erişimi gerekli",
        description:
          "Bu servisin loglarını okumak için kapsamında logs.view yetkisi gerekir. Servisi görebilmek loglarını görmeyi kapsamaz.",
      },
      notConfigured: {
        title: "Log deposu yapılandırılmamış",
        description:
          "Bu Drake'e bağlı bir log deposu yok, hiçbir şey okunmadı. Loglar kayıp değil; burada toplanmıyor.",
      },
      unavailable: {
        title: "Loglar okunamadı",
        description:
          "Log deposu şu an yanıt vermedi. Bu, servisin kendisi hakkında bir şey söylemez. Kod: {reason}",
      },
      empty: {
        title: "Eşleşen satır yok",
        description:
          "Bu aralıkta filtrelerle eşleşen bir şey yok. Bu, hata olmadığı anlamına gelmez: aralığı genişletin ya da filtreleri temizleyin.",
      },
      allHidden: {
        title: "Bu aralıkta yalnızca sağlık kontrolleri var",
        description: "Bu sayfadaki her satır bir Kubernetes sağlık kontrolü.",
      },
      unavailableLink: "Tekrar dene",
    },
    redaction:
      "Parola, token ya da anahtara benzeyen değerler depolanmadan önce maskelenir. Maskeleme en iyi çabadır, garanti değildir.",
  },
});
