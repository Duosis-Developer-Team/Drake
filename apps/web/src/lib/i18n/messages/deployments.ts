/**
 * Strings for the `deployments` area. English above, Turkish below, same shape —
 * a key missing from `tr` fails typecheck. See ../README.md.
 *
 * `rollout.*`, `evidence.*`, `evidenceDescription.*`, `verdict.*`,
 * `direction.*`, `signal.*` and `chain.*` are keyed by the backend enum
 * values, so components render them with `t.dyn("rollout", state)`. The
 * English records in lib/deployments.ts stay for non-React consumers.
 */
import { defineMessages } from "../define";

export const deployments = defineMessages({
  en: {
    rollout: {
      pending: "Pending",
      progressing: "Progressing",
      healthy: "Healthy",
      degraded: "Degraded",
      failed: "Failed",
      stalled: "Stalled",
      unknown: "Unknown",
    },
    evidence: {
      verified: "Verified",
      partial: "Partial evidence",
      unverified: "Unverified",
      conflict: "Conflicting evidence",
    },
    evidenceDescription: {
      verified: "Commit, workflow run and image digest all line up with the running workload.",
      partial: "Some of the chain was observed, but it does not close end to end.",
      unverified:
        "Only a mutable image tag was seen. It may well be correct; Drake has no evidence for it.",
      conflict:
        "The workload declares one image digest and the node pulled another. Drake does not pick a side.",
    },
    verdict: {
      improved: "Improved",
      stable: "Stable",
      regressed: "Regressed",
      insufficient_data: "Not enough data",
    },
    direction: {
      improved: "improved",
      regressed: "regressed",
      stable: "stable",
      unknown: "not measured",
    },
    signal: {
      request_rate: "Request rate",
      error_ratio: "Error ratio",
      latency_p95: "Latency (p95)",
      restarts: "Restarts",
      availability: "Scrape availability",
    },
    chain: {
      commit: "Commit SHA",
      workflow: "Workflow run",
      declared_digest: "Digest in the workload spec",
      running_digest: "Digest the node pulled",
    },
    ref: {
      imageDigest: "image digest",
      digest: "digest",
      commit: "commit",
      missing: "no {label}",
    },
    kpi: {
      share: "{label} share",
    },
    list: {
      title: "Deployments",
      description:
        "Every observed workload revision, and how much of its commit-to-workload chain Drake actually saw.",
      row: {
        unbound: "not bound to a service",
        ready: "ready",
        notCompared: "not compared yet",
      },
      kpi: {
        healthy: "Healthy",
        healthyCaption: "of {count, plural, one {# rollout} other {# rollouts}} on this page",
        failedOrDegraded: "Failed or degraded",
        failedOrDegradedCaption: "{failed} failed · {degraded} degraded",
        stalled: "Stalled",
        stalledCaption: "{count} still progressing",
        verified: "Verified evidence",
        verifiedCaption: "whole chain observed",
      },
      filters: {
        summary: "{shown} of {total} shown",
        rollout: "Rollout",
        evidence: "Evidence",
        startedWithin: "Started within",
        any: "Any",
        anyTime: "Any time",
      },
      table: {
        title: "Rollouts",
        description: "One row per observed workload revision",
        forbidden: "Your current scope does not include deployments.",
        footer:
          "Showing {shown} of {total, plural, one {# deployment} other {# deployments}} in your authorized scope.",
      },
      empty: {
        title: "No deployments",
        description:
          "Nothing matches these filters. Drake records a revision when a cluster agent reports a workload generation.",
        rollout: "Rollout · {value}",
        evidence: "Evidence · {value}",
        started: "Started · {value}",
      },
      outcomes: {
        title: "Rollout outcomes",
        description: "Rollouts on this page",
        chart: "Rollouts on this page by state",
        center: "rollouts",
      },
      evidencePanel: {
        title: "Evidence",
        description: "commit → workflow → digest → workload",
      },
    },
    detail: {
      back: "Deployments",
      unbound: "not bound to a service",
      replicasReady: "Replicas ready",
      rolloutDuration: "Rollout duration",
      completed: "completed",
      stillRollingOut: "still rolling out",
      evidenceChain: "Evidence chain",
      evidenceChainObserved: "Evidence chain observed",
      healthVerdict: "Health verdict",
      notCompared: "not compared",
      incidentsAfter: "{count, plural, one {# incident} other {# incidents}} after rollout",
      waitingForWindow: "waiting for the window after this rollout to close",
      provenance: {
        title: "Provenance",
        observed: "observed",
        notObserved: "not observed",
        digest: "Digest",
        commit: "Commit",
        workflowRun: "Workflow run",
      },
      rollout: {
        title: "Rollout",
        readyDesired: "Ready / desired",
        updated: "Updated",
        available: "Available",
        generation: "Generation",
        generationValue: "{revision} observed {observed}",
        started: "Started",
        duration: "Duration",
      },
      comparison: {
        title: "Health before and after",
        description:
          "A comparison of two time windows, not a causal claim. Drake does not assert that this deployment caused any change it shows here.",
        incidentsInWindow:
          "{count, plural, one {# incident} other {# incidents}} opened in the window after this rollout",
        signal: "Signal",
        before: "Before",
        after: "After",
        direction: "Direction",
        notYetTitle: "Not compared yet",
        notYetDescription:
          "A comparison is computed once the rollout has finished and the window after it has closed.",
      },
      revisions: {
        title: "Revision history",
        empty: "No prior revisions recorded",
      },
      incidents: {
        title: "Incidents in the window",
        emptyTitle: "No incidents",
        emptyDescription: "No incident opened for this service in the two hours after this rollout.",
      },
    },
  },
  tr: {
    rollout: {
      pending: "Bekliyor",
      progressing: "İlerliyor",
      healthy: "Sağlıklı",
      degraded: "Bozulmuş",
      failed: "Başarısız",
      stalled: "Takıldı",
      unknown: "Bilinmiyor",
    },
    evidence: {
      verified: "Doğrulandı",
      partial: "Kısmi kanıt",
      unverified: "Doğrulanmadı",
      conflict: "Çelişen kanıt",
    },
    evidenceDescription: {
      verified: "Commit, iş akışı çalıştırması ve imaj digest'i çalışan iş yüküyle birebir örtüşüyor.",
      partial: "Zincirin bir kısmı gözlemlendi, ancak uçtan uca kapanmıyor.",
      unverified:
        "Yalnızca değiştirilebilir bir imaj etiketi görüldü. Doğru olabilir; ancak Drake'in elinde buna dair kanıt yok.",
      conflict:
        "İş yükü bir imaj digest'i bildiriyor, düğüm ise başka birini çekti. Drake taraf tutmaz.",
    },
    verdict: {
      improved: "İyileşti",
      stable: "Kararlı",
      regressed: "Geriledi",
      insufficient_data: "Yeterli veri yok",
    },
    direction: {
      improved: "iyileşti",
      regressed: "geriledi",
      stable: "kararlı",
      unknown: "ölçülmedi",
    },
    signal: {
      request_rate: "İstek hızı",
      error_ratio: "Hata oranı",
      latency_p95: "Gecikme (p95)",
      restarts: "Yeniden başlatmalar",
      availability: "Scrape erişilebilirliği",
    },
    chain: {
      commit: "Commit SHA'sı",
      workflow: "İş akışı çalıştırması",
      declared_digest: "İş yükü spec'indeki digest",
      running_digest: "Düğümün çektiği digest",
    },
    ref: {
      imageDigest: "imaj digest'i",
      digest: "imaj digest'i",
      commit: "commit SHA'sı",
      missing: "{label} yok",
    },
    kpi: {
      share: "{label} payı",
    },
    list: {
      title: "Dağıtımlar",
      description:
        "Gözlemlenen her iş yükü revizyonu ve commit'ten iş yüküne uzanan zincirinin Drake'in gerçekten ne kadarını gördüğü.",
      row: {
        unbound: "bir servise bağlı değil",
        ready: "hazır",
        notCompared: "henüz karşılaştırılmadı",
      },
      kpi: {
        healthy: "Sağlıklı",
        healthyCaption: "bu sayfadaki {count} rollout içinde",
        failedOrDegraded: "Başarısız veya bozulmuş",
        failedOrDegradedCaption: "{failed} başarısız · {degraded} bozulmuş",
        stalled: "Takıldı",
        stalledCaption: "{count} hâlâ ilerliyor",
        verified: "Doğrulanmış kanıt",
        verifiedCaption: "zincirin tamamı gözlemlendi",
      },
      filters: {
        summary: "{total} kayıttan {shown} tanesi gösteriliyor",
        rollout: "Rollout durumu",
        evidence: "Kanıt",
        startedWithin: "Başlangıç aralığı",
        any: "Tümü",
        anyTime: "Herhangi bir zaman",
      },
      table: {
        title: "Rollout'lar",
        description: "Gözlemlenen her iş yükü revizyonu için bir satır",
        forbidden: "Mevcut kapsamınız dağıtımları içermiyor.",
        footer: "Yetkili olduğunuz kapsamdaki {total} dağıtımdan {shown} tanesi gösteriliyor.",
      },
      empty: {
        title: "Dağıtım yok",
        description:
          "Bu filtrelerle eşleşen bir kayıt yok. Drake, bir küme ajanı yeni bir iş yükü generation'ı bildirdiğinde revizyon kaydeder.",
        rollout: "Rollout durumu · {value}",
        evidence: "Kanıt · {value}",
        started: "Başlangıç · {value}",
      },
      outcomes: {
        title: "Rollout sonuçları",
        description: "Bu sayfadaki rollout'lar",
        chart: "Bu sayfadaki rollout'lar, duruma göre",
        center: "rollout",
      },
      evidencePanel: {
        title: "Kanıt",
        description: "commit → iş akışı → digest → iş yükü",
      },
    },
    detail: {
      back: "Dağıtımlar",
      unbound: "bir servise bağlı değil",
      replicasReady: "Hazır replika",
      rolloutDuration: "Rollout süresi",
      completed: "tamamlandı",
      stillRollingOut: "rollout sürüyor",
      evidenceChain: "Kanıt zinciri",
      evidenceChainObserved: "Gözlemlenen kanıt zinciri",
      healthVerdict: "Sağlık kararı",
      notCompared: "karşılaştırılmadı",
      incidentsAfter: "rollout sonrası {count} olay",
      waitingForWindow: "bu rollout'tan sonraki pencerenin kapanması bekleniyor",
      provenance: {
        title: "Köken",
        observed: "gözlemlendi",
        notObserved: "gözlemlenmedi",
        digest: "İmaj digest'i",
        commit: "Commit SHA'sı",
        workflowRun: "İş akışı çalıştırması",
      },
      rollout: {
        title: "Rollout bilgileri",
        readyDesired: "Hazır / istenen",
        updated: "Güncellenen",
        available: "Kullanılabilir",
        generation: "Generation değeri",
        generationValue: "{revision}, gözlemlenen {observed}",
        started: "Başlangıç",
        duration: "Süre",
      },
      comparison: {
        title: "Öncesi ve sonrası sağlık",
        description:
          "Bu, iki zaman penceresinin karşılaştırmasıdır; bir nedensellik iddiası değildir. Drake, burada gösterdiği hiçbir değişikliğe bu dağıtımın yol açtığını öne sürmez.",
        incidentsInWindow: "bu rollout'tan sonraki pencerede {count} olay açıldı",
        signal: "Sinyal",
        before: "Önce",
        after: "Sonra",
        direction: "Yön",
        notYetTitle: "Henüz karşılaştırılmadı",
        notYetDescription:
          "Karşılaştırma, rollout tamamlanıp sonrasındaki pencere kapandığında hesaplanır.",
      },
      revisions: {
        title: "Revizyon geçmişi",
        empty: "Önceki revizyon kaydı yok",
      },
      incidents: {
        title: "Penceredeki olaylar",
        emptyTitle: "Olay yok",
        emptyDescription: "Bu rollout'tan sonraki iki saat içinde bu servis için olay açılmadı.",
      },
    },
  },
});
