/**
 * Error tracking (ADR-0036).
 */
import { defineMessages } from "../define";

export const errors = defineMessages({
  en: {
    title: "Errors",
    environment: "Environment",
    rangeLabel: "Time range",
    range: { "6h": "6 hours", "24h": "24 hours" },
    loadError: "Errors could not be read.",
    noEnvironments: "No environment has a bound service yet.",
    service: "Service",
    allServices: "All services",
    asOf: "read {ago}",
    notConfigured: "not switched on",
    unavailable: "store did not answer",
    partial: "Part of the store did not answer; what it did answer is shown.",
    kpi: {
      all: "Issues",
      allNote: "in {environment}",
      issues: "{count, plural, one {# issue} other {# issues}}",
    },
    kind: {
      exception: "Exceptions",
      failed_request: "Failed requests",
      error_log: "Error logs",
    },
    lastSeen: "last {ago}",
    trendLabel: "{count, plural, one {# event} other {# events}} over the window",
    clean: {
      title: "No errors in the last {range}",
      kind: "No {kind} in this window",
      body: "Nothing raised, failed or was logged as an error by these services.",
    },
    hintExceptions:
      "No exceptions were recorded. Services show them here once their OpenTelemetry SDK records exceptions on spans.",
    detail: {
      service: "Service",
      firstSeen: "First seen in this window",
      lastSeen: "Last seen",
      events: "Events",
      code: "Code",
      routes: "Endpoints",
      sample: "Sample",
      stack: "Stack trace",
      trace: "Trace",
      openHealth: "Open its service health page",
      windowNote: "Drake groups what the store holds for this window; an older occurrence is not counted.",
    },
  },
  tr: {
    title: "Hatalar",
    environment: "Ortam",
    rangeLabel: "Zaman aralığı",
    range: { "6h": "6 saat", "24h": "24 saat" },
    loadError: "Hatalar okunamadı.",
    noEnvironments: "Henüz bağlı servisi olan bir ortam yok.",
    service: "Servis",
    allServices: "Tüm servisler",
    asOf: "{ago} okundu",
    notConfigured: "açık değil",
    unavailable: "depo yanıt vermedi",
    partial: "Deponun bir kısmı yanıt vermedi; yanıt verenler gösteriliyor.",
    kpi: {
      all: "Sorunlar",
      allNote: "{environment} içinde",
      issues: "{count} sorun",
    },
    kind: {
      exception: "İstisnalar",
      failed_request: "Başarısız istekler",
      error_log: "Hata logları",
    },
    lastSeen: "son {ago}",
    trendLabel: "aralık boyunca {count} olay",
    clean: {
      title: "Son {range} içinde hata yok",
      kind: "Bu aralıkta {kind} yok",
      body: "Bu servislerde fırlatılan, başarısız olan ya da hata olarak loglanan bir şey yok.",
    },
    hintExceptions:
      "Hiç istisna kaydedilmedi. Servislerin OpenTelemetry SDK'sı istisnaları span'lere kaydetmeye başlayınca burada görünür.",
    detail: {
      service: "Servis",
      firstSeen: "Bu aralıkta ilk görülme",
      lastSeen: "Son görülme",
      events: "Olay",
      code: "Kod",
      routes: "Uçlar",
      sample: "Örnek",
      stack: "Yığın izi",
      trace: "İz kimliği",
      openHealth: "Servis sağlığı sayfasını aç",
      windowNote: "Drake deponun bu aralıkta tuttuklarını gruplar; daha eski olaylar sayılmaz.",
    },
  },
});
