/**
 * Strings for the `traces` area: requests and traces on a service. English
 * above, Turkish below, same shape — a key missing from `tr` fails typecheck.
 *
 * Route names, paths and span names are data and are shown as recorded.
 */
import { defineMessages } from "../define";

export const traces = defineMessages({
  en: {
    title: "Requests and traces",
    description: "What this workload served, from the kernel up. Times are UTC.",
    range: { label: "Time range" },
    refresh: "Refresh",
    totals: {
      requests: "Requests",
      errorRate: "Error rate",
      p95: "p95 latency",
      noRequests: "No requests in this window",
    },
    routes: {
      title: "Endpoints",
      route: "Endpoint",
      requests: "Requests",
      errors: "Errors",
      p50: "p50",
      p95: "p95",
    },
    requests: {
      title: "Requests",
      sort: { label: "Order", recent: "Recent", slowest: "Slowest" },
      errorsOnly: "Errors only",
      open: "Open trace",
      close: "Close trace",
      none: "No requests matched",
    },
    trace: {
      title: "Trace",
      spans: "{count, plural, one {# span} other {# spans}}",
      total: "Total {duration}",
      kind: {
        Server: "Server",
        Client: "Client",
        Internal: "Internal",
        Producer: "Producer",
        Consumer: "Consumer",
      },
      attributes: "Details",
      status: "Status",
      loading: "Loading trace…",
      gone: "This trace is no longer stored. Traces are kept for 3 days.",
    },
    state: {
      denied: {
        title: "Trace access required",
        description:
          "Reading this service's requests needs the traces.view permission at its scope. Seeing a service does not include its traces.",
      },
      notConfigured: {
        title: "Tracing not enabled",
        description:
          "No traces are collected for this Drake yet, so nothing was read. That is not the same as no requests.",
      },
      unavailable: {
        title: "Traces could not be read",
        description:
          "The trace store did not answer just now. This says nothing about the service itself. Code: {reason}",
      },
      empty: {
        title: "No requests in this window",
        description:
          "Nothing was recorded for this service in the selected window. If the service is busy, it may not be instrumented yet.",
      },
    },
    note: "Recorded at the kernel without changing the service: entry and exit of each request, not the code in between.",
  },
  tr: {
    title: "İstekler ve trace'ler",
    description: "Bu iş yükünün karşıladığı istekler, çekirdekten itibaren. Saatler UTC.",
    range: { label: "Zaman aralığı" },
    refresh: "Yenile",
    totals: {
      requests: "İstek",
      errorRate: "Hata oranı",
      p95: "p95 gecikme",
      noRequests: "Bu aralıkta istek yok",
    },
    routes: {
      title: "Uç noktalar",
      route: "Uç nokta",
      requests: "İstek",
      errors: "Hata",
      p50: "%50",
      p95: "%95",
    },
    requests: {
      title: "İstekler",
      sort: { label: "Sıralama", recent: "Son", slowest: "En yavaş" },
      errorsOnly: "Yalnızca hatalar",
      open: "Trace'i aç",
      close: "Trace'i kapat",
      none: "Eşleşen istek yok",
    },
    trace: {
      title: "Trace ayrıntısı",
      spans: "{count} adım",
      total: "Toplam {duration}",
      kind: {
        Server: "Sunucu",
        Client: "İstemci",
        Internal: "İç",
        Producer: "Üretici",
        Consumer: "Tüketici",
      },
      attributes: "Ayrıntılar",
      status: "Durum",
      loading: "Trace yükleniyor…",
      gone: "Bu trace artık saklanmıyor. Trace'ler 3 gün tutulur.",
    },
    state: {
      denied: {
        title: "Trace erişimi gerekli",
        description:
          "Bu servisin isteklerini okumak için kapsamında traces.view yetkisi gerekir. Servisi görebilmek trace'lerini görmeyi kapsamaz.",
      },
      notConfigured: {
        title: "Trace toplama açık değil",
        description:
          "Bu Drake için henüz trace toplanmıyor, hiçbir şey okunmadı. Bu, istek olmadığı anlamına gelmez.",
      },
      unavailable: {
        title: "Trace'ler okunamadı",
        description:
          "Trace deposu şu an yanıt vermedi. Bu, servisin kendisi hakkında bir şey söylemez. Kod: {reason}",
      },
      empty: {
        title: "Bu aralıkta istek yok",
        description:
          "Seçilen aralıkta bu servis için kayıt yok. Servis yoğunsa henüz izlenmiyor olabilir.",
      },
    },
    note: "Servis değiştirilmeden çekirdekte kaydedilir: her isteğin girişi ve çıkışı, aradaki kod değil.",
  },
});
