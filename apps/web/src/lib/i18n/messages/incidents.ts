/**
 * Strings for the `incidents` area. English above, Turkish below, same shape —
 * a key missing from `tr` fails typecheck. See ../README.md.
 *
 * `state.*`, `event.*`, `eventDescription.*` and `window.*` mirror the enum
 * records in lib/incidents (which other areas still import); components use
 * `t.dyn("state", value, STATE_LABELS[value])` so an unknown token still
 * renders as English rather than as nothing.
 */
import { defineMessages } from "../define";

export const incidents = defineMessages({
  en: {
    state: {
      open: "Open",
      acknowledged: "Acknowledged",
      resolved: "Resolved",
    },
    /** The severity chip mirrors the API token, lowercase on purpose. */
    severity: {
      critical: "critical",
    },
    window: {
      "24h": "24h",
      "7d": "7d",
      "30d": "30d",
    },
    event: {
      opened: "Incident opened",
      acknowledged: "Acknowledged",
      recovery_started: "Recovery started",
      recovery_interrupted: "Recovery interrupted",
      auto_resolved: "Automatically resolved",
    },
    eventDescription: {
      opened: "Two consecutive trustworthy critical evaluations.",
      acknowledged: "A responder confirmed they have seen this. Monitoring continues.",
      recovery_started: "One healthy evaluation. A second one resolves the incident.",
      recovery_interrupted: "The service stopped reporting healthy before recovery completed.",
      auto_resolved: "Two consecutive trustworthy healthy evaluations.",
    },
    step: {
      opened: "Opened",
      acknowledged: "Acknowledged",
      resolved: "Resolved",
    },
    list: {
      title: "Incidents",
      description:
        "Opened and closed by Drake from trustworthy health evaluations — never by a datasource outage.",
      shown: "{shown} of {total} shown",
      filter: {
        state: "State",
        severity: "Severity",
        openedWithin: "Opened within",
        any: "Any",
        anyTime: "Any time",
      },
      kpi: {
        open: "Open",
        openCaption: "nobody has acknowledged yet",
        acknowledged: "Acknowledged",
        acknowledgedCaption: "someone is on it",
        resolved: "Resolved",
        resolvedCaption: "closed on a real recovery",
        shown: "Shown",
        shownCaption: "of {total} in your authorized scope",
      },
      queue: {
        title: "Incident queue",
        description: "Worst first, as the processor recorded them",
      },
      forbidden: "Your current scope does not include incidents.",
      empty: {
        title: "No incidents",
        description: "Nothing matches these filters in your authorized scope.",
        state: "State · {value}",
        severity: "Severity · {value}",
        opened: "Opened · {value}",
        any: "any",
        anyTime: "any time",
      },
      footer:
        "Showing {shown} of {total, plural, one {# incident} other {# incidents}} in your authorized scope.",
      byState: {
        title: "By state",
        description: "Incidents on this page",
        ringLabel: "Incidents on this page by state",
        center: "on this page",
      },
      lifecycle: {
        title: "How incidents move",
        opens: "Opens",
        opensDetail: "after two consecutive trustworthy critical evaluations",
        acknowledged: "Acknowledged",
        acknowledgedDetail: "someone is on it — monitoring continues",
        closes: "Closes",
        closesDetail: "on its own when the service reports healthy twice",
        note: "A datasource outage never opens an incident.",
      },
    },
    row: {
      healthUnknown: "health unknown",
      acknowledged: "acknowledged",
      opened: "opened",
    },
    detail: {
      back: "Incidents",
      acknowledge: "Acknowledge",
      acknowledged: "Acknowledged",
      notice: {
        acknowledged:
          "Acknowledged. Monitoring continues — this incident closes only on a real recovery.",
        alreadyAcknowledged: "Already acknowledged.",
        conflictTitle: "Version conflict",
        conflict:
          "This incident changed while you were looking at it — someone else acted on it. Refresh to see the current state before trying again.",
        requestFailed: "request failed",
      },
      ackForbidden: "Acknowledging an incident needs incident.ack in this scope.",
      lifecycle: {
        title: "Lifecycle",
        description:
          "Opened, acknowledged, resolved — as Drake recorded it, one honest timestamp at a time.",
        activeNote:
          "Acknowledging does not close the incident. It resolves on its own after two consecutive healthy evaluations.",
        resolvedNote: "Resolved — see below for how.",
      },
      why: {
        title: "Why it opened",
        description:
          "Opened after two consecutive trustworthy critical evaluations. A partial, stale, or last-known reading is never one of them.",
      },
      health: {
        title: "Current health",
        lastObserved: "Last observed",
        open: "Open service health",
        none: "No health state has been recorded for this binding yet.",
      },
      events: {
        title: "Lifecycle events",
        description: "Append-only. Nothing on this screen edits or removes an entry.",
      },
      timing: {
        title: "Workload & timing",
        workload: "Workload",
        opened: "Opened",
        duration: "Duration",
        lastCritical: "Last critical",
        acknowledged: "Acknowledged",
        resolved: "Resolved",
        notYet: "not yet",
        stillActive: "still active",
        healthRecovered: "health recovered",
      },
    },
    reasons: {
      none: "No reason codes recorded.",
    },
    timeline: {
      empty: "No lifecycle events recorded yet.",
    },
    kpi: {
      shareLabel: "{label} of shown incidents",
    },
    toolbar: {
      filters: "Filters",
    },
    ring: {
      total: "total",
    },
    service: {
      title: "Incidents",
      openIncident: "Open incident: {title}",
      runningFor: "Running for {duration}",
      empty: {
        title: "No incidents",
        description: "This service has not had an incident opened for it.",
      },
      transitions: {
        title: "Recent health changes",
        emptyTitle: "No recorded changes",
        emptyDescription:
          "Drake records a row when the status or its reasons change, not on every evaluation.",
        first: "first observation",
      },
    },
  },
  tr: {
    state: {
      open: "Açık",
      acknowledged: "Onaylandı",
      resolved: "Çözüldü",
    },
    severity: {
      critical: "kritik",
    },
    window: {
      "24h": "24 sa",
      "7d": "7 g",
      "30d": "30 g",
    },
    event: {
      opened: "Olay açıldı",
      acknowledged: "Onaylandı",
      recovery_started: "Toparlanma başladı",
      recovery_interrupted: "Toparlanma kesildi",
      auto_resolved: "Otomatik olarak çözüldü",
    },
    eventDescription: {
      opened: "Art arda iki güvenilir kritik değerlendirme.",
      acknowledged: "Bir müdahale eden bunu gördüğünü doğruladı. İzleme sürüyor.",
      recovery_started: "Bir sağlıklı değerlendirme. İkincisi olayı çözer.",
      recovery_interrupted: "Servis, toparlanma tamamlanmadan sağlıklı raporlamayı bıraktı.",
      auto_resolved: "Art arda iki güvenilir sağlıklı değerlendirme.",
    },
    step: {
      opened: "Açıldı",
      acknowledged: "Onaylandı",
      resolved: "Çözüldü",
    },
    list: {
      title: "Olaylar",
      description:
        "Drake tarafından güvenilir sağlık değerlendirmelerine göre açılır ve kapatılır; bir veri kaynağı kesintisi asla olay açmaz.",
      shown: "{total} kayıttan {shown} tanesi gösteriliyor",
      filter: {
        state: "Durum",
        severity: "Önem derecesi",
        openedWithin: "Açılış aralığı",
        any: "Tümü",
        anyTime: "Tüm zamanlar",
      },
      kpi: {
        open: "Açık",
        openCaption: "henüz kimse onaylamadı",
        acknowledged: "Onaylandı",
        acknowledgedCaption: "biri ilgileniyor",
        resolved: "Çözüldü",
        resolvedCaption: "gerçek bir toparlanmayla kapandı",
        shown: "Gösterilen",
        shownCaption: "yetkili kapsamınızdaki {total} olaydan",
      },
      queue: {
        title: "Olay kuyruğu",
        description: "En kötüsü önce, işlemcinin kaydettiği sırayla",
      },
      forbidden: "Mevcut kapsamınız olayları içermiyor.",
      empty: {
        title: "Olay yok",
        description: "Yetkili kapsamınızda bu filtrelerle eşleşen bir şey yok.",
        state: "Durum · {value}",
        severity: "Önem derecesi · {value}",
        opened: "Açılış · {value}",
        any: "tümü",
        anyTime: "tüm zamanlar",
      },
      footer: "Yetkili kapsamınızdaki {total} olaydan {shown} tanesi gösteriliyor.",
      byState: {
        title: "Duruma göre",
        description: "Bu sayfadaki olaylar",
        ringLabel: "Bu sayfadaki olaylar, duruma göre",
        center: "bu sayfada",
      },
      lifecycle: {
        title: "Olaylar nasıl ilerler",
        opens: "Açılır",
        opensDetail: "art arda iki güvenilir kritik değerlendirmeden sonra",
        acknowledged: "Onaylanır",
        acknowledgedDetail: "biri ilgileniyor; izleme sürüyor",
        closes: "Kapanır",
        closesDetail: "servis iki kez sağlıklı raporladığında kendiliğinden",
        note: "Bir veri kaynağı kesintisi asla olay açmaz.",
      },
    },
    row: {
      healthUnknown: "sağlık bilinmiyor",
      acknowledged: "onay:",
      opened: "açılış:",
    },
    detail: {
      back: "Olaylar",
      acknowledge: "Onayla",
      acknowledged: "Onaylandı",
      notice: {
        acknowledged:
          "Onaylandı. İzleme sürüyor; bu olay yalnızca gerçek bir toparlanmayla kapanır.",
        alreadyAcknowledged: "Zaten onaylanmış.",
        conflictTitle: "Sürüm çakışması",
        conflict:
          "Siz bakarken bu olay değişti; başka biri işlem yaptı. Yeniden denemeden önce güncel durumu görmek için yenileyin.",
        requestFailed: "istek başarısız",
      },
      ackForbidden: "Bir olayı onaylamak için bu kapsamda incident.ack yetkisi gerekir.",
      lifecycle: {
        title: "Yaşam döngüsü",
        description:
          "Açıldı, onaylandı, çözüldü; Drake'in kaydettiği gibi, her seferinde dürüst bir zaman damgasıyla.",
        activeNote:
          "Onaylamak olayı kapatmaz. Art arda iki sağlıklı değerlendirmeden sonra kendiliğinden çözülür.",
        resolvedNote: "Çözüldü; nasıl olduğu aşağıda.",
      },
      why: {
        title: "Neden açıldı",
        description:
          "Art arda iki güvenilir kritik değerlendirmeden sonra açıldı. Kısmi, güncel olmayan veya son bilinen bir okuma hiçbir zaman bunlardan biri sayılmaz.",
      },
      health: {
        title: "Mevcut sağlık",
        lastObserved: "Son gözlem:",
        open: "Servis sağlığını aç",
        none: "Bu bağlama için henüz bir sağlık durumu kaydedilmedi.",
      },
      events: {
        title: "Yaşam döngüsü olayları",
        description:
          "Yalnızca ekleme yapılır. Bu ekrandaki hiçbir şey bir kaydı düzenlemez veya silmez.",
      },
      timing: {
        title: "İş yükü ve zamanlama",
        workload: "İş yükü",
        opened: "Açılış",
        duration: "Süre",
        lastCritical: "Son kritik",
        acknowledged: "Onay",
        resolved: "Çözülme",
        notYet: "henüz değil",
        stillActive: "hâlâ etkin",
        healthRecovered: "sağlık toparlandı",
      },
    },
    reasons: {
      none: "Kayıtlı neden kodu yok.",
    },
    timeline: {
      empty: "Henüz kayıtlı yaşam döngüsü olayı yok.",
    },
    kpi: {
      shareLabel: "Gösterilen olaylar içinde {label}",
    },
    toolbar: {
      filters: "Filtreler",
    },
    ring: {
      total: "toplam",
    },
    service: {
      title: "Olaylar",
      openIncident: "Açık olay: {title}",
      runningFor: "{duration} süredir sürüyor",
      empty: {
        title: "Olay yok",
        description: "Bu servis için hiç olay açılmadı.",
      },
      transitions: {
        title: "Son sağlık değişiklikleri",
        emptyTitle: "Kayıtlı değişiklik yok",
        emptyDescription:
          "Drake her değerlendirmede değil, durum veya nedenleri değiştiğinde bir satır kaydeder.",
        first: "ilk gözlem",
      },
    },
  },
});
