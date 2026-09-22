/**
 * Strings for the `integrations` area. English above, Turkish below, same shape —
 * a key missing from `tr` fails typecheck. See ../README.md.
 *
 * `badge.*`, `missingInput.*` and `provider.<type>` are keyed by backend
 * tokens and read through `t.dyn`; the `label`-only records they replace
 * lived in the GitHub page and `components/github/primitives`.
 */
import { defineMessages } from "../define";

export const integrations = defineMessages({
  en: {
    overview: {
      title: "Integration Health",
      description: "Connector configuration and observed state per scope.",
      githubLink: "GitHub App integration",
      providersHeading: "Providers",
      empty: {
        title: "No integrations in your scope",
        description: "Integrations registered on scopes you can read will appear here.",
      },
    },
    stats: {
      providers: "Providers",
      connectorsAcross:
        "{connectors, plural, one {# connector} other {# connectors}} across {scopes, plural, one {# scope} other {# scopes}}",
      connected: "Connected",
      of: "of {total}",
      configured: "configured",
      answeringOk: "Answering OK",
      reportingNormally: "reporting normally",
      needsAttention: "Needs attention",
      needsAttentionHint: "Degraded, stale or erroring connectors",
    },
    provider: {
      github: { name: "GitHub", blurb: "Repository governance" },
      "cluster-agent": { name: "Cluster agent", blurb: "Kubernetes inventory" },
      "backup-reporter": { name: "Backup reporter", blurb: "Backup evidence" },
      genericBlurb: "Connector",
      scopes: "{connected}/{total} scopes",
      connected: "Connected",
      notConnected: "Not connected",
      lastSync: "{type} · last sync {time}",
      never: "never",
      latestSuccess: "Latest success {time}",
      noSuccess: "No successful sync yet",
      manage: "Manage",
      operatorManaged: "Operator-managed",
      operatorManagedHint: "Connected by an operator; there is nothing to set here.",
      coverageConnected: "{name}: connected",
      coverageNotConnected: "{name}: not connected",
    },
    health: {
      title: "Observed health",
      description: "Every connector, by what Drake last saw",
      donutLabel: "Observed connector state",
      ok: "OK",
      notReporting: "Not reporting",
      coverageTitle: "Coverage by scope",
      coverageDescription: "Connected providers per scope",
    },
    github: {
      title: "GitHub App integration",
      crumb: "Integrations",
      descriptionTail:
        "/ GitHub — read-only repository governance; Drake never changes a repository setting.",
      repositories: {
        heading: "Repositories",
        visible: "{count} visible to you",
        empty: {
          title: "No repositories in your scope",
          description:
            "Repositories the installation can see, and you are authorized to view, appear here.",
        },
      },
      installations: {
        title: "Installations",
        description: "Organizations the App is installed on",
        empty: {
          title: "No installation yet",
          description:
            "Once the GitHub App is installed for the organization, its installation appears here.",
        },
        unknownAccount: "unknown account",
        summary: "{selection} repositories · {count, plural, one {# event} other {# events}}",
      },
      readiness: {
        title: "Connection readiness",
        subtitle: "GitHub App · read-only",
        configurationLabel: "Configuration",
        configured: "configured",
        subscribedEvents: "Subscribed events",
        installations: "Installations",
        repositories: "Repositories",
        blocked: "Blocked by a security gate",
        operatorAction: "operator action required",
        noGate: "No gate is open",
        notConnected: {
          title: "GitHub App is not connected yet",
          description:
            "Drake shows nothing here until an operator supplies the app identity and its secret references.",
        },
        secretsNote: "Secrets are supplied out of band and referenced by name; never shown here.",
      },
      repository: {
        defaultBranch: "default branch: {branch}",
        unknownBranch: "unknown",
        private: "private",
        public: "public",
        stale: "stale",
        reconciliation: {
          title: "Reconciliation required",
          description:
            "A recent change could not be recorded in full, so this installation is being re-read. What is shown may be incomplete until that finishes.",
        },
        gate: {
          title: "Blocked by a manual security gate",
          description:
            "An operator must review and close this security gate before Drake may onboard this repository.",
        },
        lastReconciliation: "Last reconciliation",
        lastPolicyEvaluation: "Last policy evaluation",
        lastError: "Last error",
        requestFailed: "request failed",
        showPolicy: "Show last policy result",
        reconcileDisabled: "Reconciliation stays disabled while the gate is open.",
        evaluating: "Evaluating…",
        reconcile: "Reconcile (dry run)",
      },
      onboardingLink: {
        checking: "Checking whether this repository can be onboarded…",
        denied: "Onboarding needs the onboarding manage permission on this repository's scope.",
        openExisting: "Open the open onboarding session",
        cannot: "This repository cannot be onboarded right now.",
        onboard: "Onboard this repository",
      },
      policy: {
        neverEvaluated: {
          title: "No policy evaluation yet",
          description: "Run a dry-run reconciliation to produce the first snapshot.",
        },
        heading: "Policy result",
        dryRun: "dry run",
        evaluated: "evaluated {time}",
        blocking: "Blocking",
        notDeterminable: "Not determinable",
        passed: "Rules passed",
        blockingViolations: "Blocking violations",
      },
    },
    missingInput: {
      feature_disabled: "The GitHub App integration is switched off",
      app_identity: "App client id (or app id)",
      private_key_reference: "Private key secret reference",
      webhook_secret_reference: "Webhook secret reference",
    },
    badge: {
      onboarding: {
        discovered: "discovered",
        validating: "validating",
        ready: "ready",
        blocked: "blocked",
        degraded: "degraded",
        disabled: "disabled",
        unknown: "unknown",
      },
      verdict: {
        pass: "pass",
        warn: "warn",
        fail: "fail",
        unknown: "unknown",
      },
      installation: {
        active: "active",
        suspended: "suspended",
        deleted: "deleted",
        unknown: "unknown",
      },
    },
  },
  tr: {
    overview: {
      title: "Entegrasyon sağlığı",
      description: "Kapsam başına bağlayıcı yapılandırması ve gözlemlenen durum.",
      githubLink: "GitHub App entegrasyonu",
      providersHeading: "Sağlayıcılar",
      empty: {
        title: "Kapsamınızda entegrasyon yok",
        description: "Okuyabildiğiniz kapsamlara kayıtlı entegrasyonlar burada görünür.",
      },
    },
    stats: {
      providers: "Sağlayıcılar",
      connectorsAcross: "{scopes} kapsamda {connectors} bağlayıcı",
      connected: "Bağlı",
      of: "{total} içinden",
      configured: "yapılandırılmış",
      answeringOk: "Yanıt veriyor",
      reportingNormally: "normal raporluyor",
      needsAttention: "Dikkat gerektiren",
      needsAttentionHint: "Bozulmuş, güncel olmayan veya hata veren bağlayıcılar",
    },
    provider: {
      github: { name: "GitHub", blurb: "Depo yönetişimi" },
      "cluster-agent": { name: "Küme ajanı", blurb: "Kubernetes envanteri" },
      "backup-reporter": { name: "Yedek raporlayıcı", blurb: "Yedek kanıtı" },
      genericBlurb: "Bağlayıcı",
      scopes: "{connected}/{total} kapsam",
      connected: "Bağlı",
      notConnected: "Bağlı değil",
      lastSync: "{type} · son senkron: {time}",
      never: "hiç",
      latestSuccess: "Son başarılı senkron: {time}",
      noSuccess: "Henüz başarılı senkron yok",
      manage: "Yönet",
      operatorManaged: "Operatör yönetiyor",
      operatorManagedHint: "Bir operatör tarafından bağlandı; burada ayarlanacak bir şey yok.",
      coverageConnected: "{name}: bağlı",
      coverageNotConnected: "{name}: bağlı değil",
    },
    health: {
      title: "Gözlemlenen sağlık",
      description: "Drake'in son gördüğü haliyle her bağlayıcı",
      donutLabel: "Gözlemlenen bağlayıcı durumu",
      ok: "OK",
      notReporting: "Raporlamıyor",
      coverageTitle: "Kapsam bazında kapsama",
      coverageDescription: "Kapsam başına bağlı sağlayıcılar",
    },
    github: {
      title: "GitHub App entegrasyonu",
      crumb: "Entegrasyonlar",
      descriptionTail:
        "/ GitHub — salt okunur depo yönetişimi; Drake bir depo ayarını asla değiştirmez.",
      repositories: {
        heading: "Depolar",
        visible: "size görünen: {count}",
        empty: {
          title: "Kapsamınızda depo yok",
          description:
            "Kurulumun görebildiği ve görüntüleme yetkinizin olduğu depolar burada görünür.",
        },
      },
      installations: {
        title: "Kurulumlar",
        description: "GitHub App'in kurulu olduğu kuruluşlar",
        empty: {
          title: "Henüz kurulum yok",
          description: "GitHub App kuruluş için kurulduğunda kurulumu burada görünür.",
        },
        unknownAccount: "bilinmeyen hesap",
        summary: "{selection} depo · {count} olay",
      },
      readiness: {
        title: "Bağlantı hazırlığı",
        subtitle: "GitHub App · salt okunur",
        configurationLabel: "Yapılandırma",
        configured: "yapılandırılmış",
        subscribedEvents: "Abone olunan olaylar",
        installations: "Kurulumlar",
        repositories: "Depolar",
        blocked: "Güvenlik kapısıyla engellenen",
        operatorAction: "operatör işlemi gerekiyor",
        noGate: "Açık kapı yok",
        notConnected: {
          title: "GitHub App henüz bağlı değil",
          description:
            "Bir operatör uygulama kimliğini ve gizli anahtar referanslarını sağlayana kadar Drake burada hiçbir şey göstermez.",
        },
        secretsNote:
          "Gizli anahtarlar Drake dışında sağlanır ve adla referanslanır; burada asla gösterilmez.",
      },
      repository: {
        defaultBranch: "varsayılan dal: {branch}",
        unknownBranch: "bilinmiyor",
        private: "özel",
        public: "herkese açık",
        stale: "güncel değil",
        reconciliation: {
          title: "Uzlaştırma gerekiyor",
          description:
            "Yakın tarihli bir değişiklik tam olarak kaydedilemedi; bu yüzden bu kurulum yeniden okunuyor. Bu tamamlanana kadar gösterilenler eksik olabilir.",
        },
        gate: {
          title: "Manuel güvenlik kapısıyla engellendi",
          description:
            "Drake'in bu depo için katılım başlatabilmesi için önce bir operatörün bu güvenlik kapısını inceleyip kaldırması gerekir.",
        },
        lastReconciliation: "Son uzlaştırma",
        lastPolicyEvaluation: "Son politika değerlendirmesi",
        lastError: "Son hata",
        requestFailed: "istek başarısız",
        showPolicy: "Son politika sonucunu göster",
        reconcileDisabled: "Kapı açıkken uzlaştırma devre dışı kalır.",
        evaluating: "Değerlendiriliyor…",
        reconcile: "Uzlaştır (deneme)",
      },
      onboardingLink: {
        checking: "Bu depo için katılım başlatılıp başlatılamayacağı denetleniyor…",
        denied: "Katılım için bu deponun kapsamında katılım yönetme yetkisi gerekir.",
        openExisting: "Açık katılım oturumunu aç",
        cannot: "Bu depo için şu anda katılım başlatılamıyor.",
        onboard: "Bu depo için katılım başlat",
      },
      policy: {
        neverEvaluated: {
          title: "Henüz politika değerlendirmesi yok",
          description: "İlk anlık görüntüyü üretmek için bir deneme uzlaştırması çalıştırın.",
        },
        heading: "Politika sonucu",
        dryRun: "deneme",
        evaluated: "değerlendirme: {time}",
        blocking: "Engelleyen",
        notDeterminable: "Belirlenemeyen",
        passed: "Geçen kurallar",
        blockingViolations: "Engelleyen ihlaller",
      },
    },
    missingInput: {
      feature_disabled: "GitHub App entegrasyonu kapalı",
      app_identity: "App istemci kimliği (veya app kimliği)",
      private_key_reference: "Özel anahtar referansı",
      webhook_secret_reference: "Webhook gizli anahtarı referansı",
    },
    badge: {
      onboarding: {
        discovered: "keşfedildi",
        validating: "doğrulanıyor",
        ready: "hazır",
        blocked: "engellendi",
        degraded: "bozulmuş",
        disabled: "devre dışı",
        unknown: "bilinmiyor",
      },
      verdict: {
        pass: "geçti",
        warn: "uyarı",
        fail: "başarısız",
        unknown: "bilinmiyor",
      },
      installation: {
        active: "etkin",
        suspended: "askıya alındı",
        deleted: "silindi",
        unknown: "bilinmiyor",
      },
    },
  },
});
