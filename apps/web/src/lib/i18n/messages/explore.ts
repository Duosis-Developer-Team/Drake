/**
 * The global Traces and Logs screens: pick a service, then the same
 * explorers the service page uses.
 */
import { defineMessages } from "../define";

export const explore = defineMessages({
  en: {
    traces: {
      title: "Traces",
      description: "Every request a bound service served, failing and slowest first.",
      quick: "Busiest and failing services, last hour",
    },
    logs: {
      title: "Logs",
      description: "What bound services wrote, errors first, with a link to each line's trace.",
      quick: "Services writing errors, last hour",
    },
    pick: "Service",
    choose: "Choose a service",
    none: "No service is bound to a workload yet, so there is nothing to read.",
    openService: "Service page",
    stats: {
      requests: "{count} requests",
      failed: "{ratio} failed",
      p95: "p95 {value}",
      errorLines: "{count} error lines",
      quiet: "quiet",
    },
  },
  tr: {
    traces: {
      title: "Trace'ler",
      description: "Bağlı servislerin karşıladığı her istek; önce hatalı ve en yavaş olanlar.",
      quick: "En yoğun ve hata veren servisler, son bir saat",
    },
    logs: {
      title: "Loglar",
      description: "Bağlı servislerin yazdıkları; önce hatalar, her satırdan trace'ine bağlantı.",
      quick: "Hata yazan servisler, son bir saat",
    },
    pick: "Servis",
    choose: "Bir servis seçin",
    none: "Henüz bir iş yüküne bağlı servis yok, o yüzden okunacak bir şey yok.",
    openService: "Servis sayfası",
    stats: {
      requests: "{count} istek",
      failed: "{ratio} başarısız",
      p95: "p95 {value}",
      errorLines: "{count} hata satırı",
      quiet: "sakin",
    },
  },
});
