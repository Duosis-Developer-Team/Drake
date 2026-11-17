/**
 * Strings for the `scheduledJobs` area: an environment's CronJobs, their
 * recent runs and their logs. English above, Turkish below, same shape.
 *
 * CronJob, Job and pod names are data and are shown as recorded.
 */
import { defineMessages } from "../define";

export const scheduledJobs = defineMessages({
  en: {
    title: "Scheduled jobs",
    description:
      "The CronJobs running in this environment, how their recent runs ended, and their logs.",
    empty: "No scheduled jobs run in this environment.",
    notApplicable: "This environment's runtime has no scheduled jobs.",
    loadError: "Scheduled jobs could not be read.",
    schedule: "Schedule",
    suspended: "Suspended",
    lastRun: "Last run",
    lastSuccess: "Last success",
    never: "never",
    recentRuns: "Recent runs",
    noRuns: "No runs kept",
    showLogs: "Show logs",
    hideLogs: "Hide logs",
    logs: {
      title: "Logs from the last 24 hours",
      empty: "No log lines in the last 24 hours.",
      notConfigured: "Logs are not configured for this environment.",
      unavailable: "Logs could not be read ({reason}).",
      denied: "Reading these logs needs log access for this environment.",
    },
    outcome: {
      succeeded: "Succeeded",
      failed: "Failed",
      running: "Running",
      unknown: "Unknown",
    },
    reason: {
      recent_runs_failed: "Its recent runs failed",
      last_run_not_successful: "Its latest run did not succeed",
      schedule_missed: "It missed its schedule",
      no_successful_run_recorded: "No successful run recorded",
      cronjob_suspended: "Suspended",
      never_scheduled: "Never scheduled",
    },
  },
  tr: {
    title: "Zamanlanmış işler",
    description:
      "Bu ortamda çalışan CronJob'lar, son çalıştırmalarının nasıl bittiği ve logları.",
    empty: "Bu ortamda zamanlanmış iş çalışmıyor.",
    notApplicable: "Bu ortamın çalışma ortamında zamanlanmış iş yok.",
    loadError: "Zamanlanmış işler okunamadı.",
    schedule: "Zamanlama",
    suspended: "Durdurulmuş",
    lastRun: "Son çalışma",
    lastSuccess: "Son başarı",
    never: "hiç",
    recentRuns: "Son çalıştırmalar",
    noRuns: "Saklanan çalıştırma yok",
    showLogs: "Logları göster",
    hideLogs: "Logları gizle",
    logs: {
      title: "Son 24 saatin logları",
      empty: "Son 24 saatte log satırı yok.",
      notConfigured: "Bu ortam için loglar yapılandırılmamış.",
      unavailable: "Loglar okunamadı ({reason}).",
      denied: "Bu logları okumak için bu ortamda log yetkisi gerekir.",
    },
    outcome: {
      succeeded: "Başarılı",
      failed: "Başarısız",
      running: "Çalışıyor",
      unknown: "Bilinmiyor",
    },
    reason: {
      recent_runs_failed: "Son çalıştırmaları başarısız oldu",
      last_run_not_successful: "Son çalıştırması başarılı olmadı",
      schedule_missed: "Zamanında çalışmadı",
      no_successful_run_recorded: "Hiç başarılı çalıştırma kaydı yok",
      cronjob_suspended: "Durdurulmuş",
      never_scheduled: "Hiç çalışmadı",
    },
  },
});
