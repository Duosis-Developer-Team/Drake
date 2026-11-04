/**
 * Strings for the `signals` area: the cross-service "where is the problem?"
 * board. English above, Turkish below, same shape — a key missing from `tr`
 * fails typecheck.
 */
import { defineMessages } from "../define";

export const signals = defineMessages({
  en: {
    title: "Where is the problem?",
    description: "Failed requests and error log lines per service, last hour.",
    column: {
      service: "Service",
      requests: "Requests",
      failed: "Failed",
      p95: "p95",
      errorLines: "Error lines",
      warnLines: "Warn lines",
    },
    cause: {
      requests: "Failing requests",
      logs: "Error logs",
      none: "Quiet",
    },
    clear: "No service is failing requests or logging errors above the threshold.",
    clearNote: "Judged from {count, plural, one {# service} other {# services}} with telemetry.",
    noTelemetry: "No bound service has logs or traces you can read yet.",
    notConfigured: "Logs and traces are not enabled on this Drake.",
    partial: {
      logs: "Log counts are unavailable right now; only requests are judged.",
      traces: "Request counts are unavailable right now; only logs are judged.",
      both: "The telemetry store is not answering right now.",
    },
    more: "{count, plural, one {# more service} other {# more services}} with signals",
    noRequests: "no requests",
    attention: {
      requests: "{ratio} of requests failing",
      logs: "{count, plural, one {# error log line} other {# error log lines}} in the last hour",
    },
  },
  tr: {
    title: "Nerede sorun var?",
    description: "Servis başına başarısız istekler ve hata log satırları, son bir saat.",
    column: {
      service: "Servis",
      requests: "İstek",
      failed: "Başarısız",
      p95: "%95",
      errorLines: "Hata satırı",
      warnLines: "Uyarı satırı",
    },
    cause: {
      requests: "Başarısız istekler",
      logs: "Hata logları",
      none: "Sakin",
    },
    clear: "Eşiğin üstünde istek hatası ya da hata logu üreten servis yok.",
    clearNote: "Telemetrisi olan {count} servise bakılarak değerlendirildi.",
    noTelemetry: "Bağlı servislerin henüz okuyabileceğin log ya da trace verisi yok.",
    notConfigured: "Bu Drake'te loglar ve trace'ler açık değil.",
    partial: {
      logs: "Log sayıları şu an alınamıyor; yalnızca isteklere bakılıyor.",
      traces: "İstek sayıları şu an alınamıyor; yalnızca loglara bakılıyor.",
      both: "Telemetri deposu şu an yanıt vermiyor.",
    },
    more: "Sinyali olan {count} servis daha",
    noRequests: "istek yok",
    attention: {
      requests: "isteklerin {ratio} kadarı başarısız",
      logs: "son bir saatte {count} hata log satırı",
    },
  },
});
