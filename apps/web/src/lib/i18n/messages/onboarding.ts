/**
 * Strings for the `onboarding` area. English above, Turkish below, same shape —
 * a key missing from `tr` fails typecheck. See ../README.md.
 *
 * `session.*`, `action.*`, `gitops.*`, `missingInput.*`, `candidateBlocker.*`
 * and `errorGuidance.*` mirror the label records in `lib/onboarding.ts`
 * (SESSION_LABELS, ACTION_LABELS, GITOPS_LABELS, MISSING_INPUT_LABELS,
 * CANDIDATE_BLOCKERS, ERROR_GUIDANCE), which stay as the English source for
 * non-React callers. Components read them through `t.dyn`. `step.*` mirrors
 * `WIZARD_STEPS`.
 */
import { defineMessages } from "../define";

export const onboarding = defineMessages({
  en: {
    page: {
      title: "Onboard a project",
      description:
        "Drake reads a repository statically, proposes a catalog change, and applies nothing until someone approves it.",
    },
    how: {
      title: "How this works",
      description:
        "Seven steps from a repository to a catalog project. Nothing changes before approval.",
      nothingExecuted:
        "Nothing in a repository is executed: no build, no install, no script, no hook, no workflow. Drake reads an allowlist of metadata files at one immutable commit.",
    },
    step: {
      integrationStatus: "Integration status",
      repository: "Repository",
      safeDiscovery: "Safe discovery",
      detectedStructure: "Detected structure",
      review: "Review",
      approval: "Approval",
      result: "Result",
    },
    stepHint: {
      integrationStatus: "GitHub App ready",
      repository: "Pick one to onboard",
      safeDiscovery: "Metadata files only",
      detectedStructure: "What Drake found",
      review: "Item by item",
      approval: "This exact plan",
      result: "Written to the catalog",
    },
    notConfigured: {
      title: "GitHub is not configured",
      description:
        "Drake cannot read repositories. Nothing has been contacted, no token has been issued, and no repository list is being shown.",
      operatorNote:
        "An operator configures the App identity and its credential references outside Drake. Drake never accepts a credential through this screen.",
    },
    missingInput: {
      feature_disabled: "The GitHub App integration is switched off.",
      app_identity: "No GitHub App identity is configured.",
      private_key_reference: "No private key reference is configured.",
      webhook_secret_reference: "No webhook secret reference is configured.",
    },
    health: {
      needReview: "Need review",
      imported: "Imported",
      ofSessions: "of {count, plural, one {# session} other {# sessions}}",
      partialAnalyses: "Partial analyses",
      ofAnalyses: "of {count, plural, one {# analysis} other {# analyses}} · last {when}",
      failedPullRequests: "Failed pull requests",
      ofPullRequests:
        "of {count, plural, one {# manifest pull request} other {# manifest pull requests}}",
      gitopsOff: "GitOps pull requests are switched off. Drake will not write to any repository.",
    },
    pipeline: {
      title: "Session pipeline",
      description: "Where the sessions in your scope stand.",
      sessions: "sessions",
      analyses: "Analyses",
      analysesFailed: "Analyses failed",
    },
    row: {
      plan: "Plan",
      planSummary: "v{version} · {count, plural, one {# item} other {# items}}",
      decisions: "Decisions",
      needReview: "{count} need review",
      notAnalysed: "not analysed",
      opened: "opened {when}",
    },
    sessions: {
      title: "Sessions",
      description: "Every onboarding in your scope, newest first.",
      total: "{count} total",
      empty: {
        title: "No onboarding sessions",
        description:
          "Nothing in your scope is being onboarded. This is not a statement about which repositories exist.",
      },
    },
    start: {
      title: "Start an onboarding",
      description: "Search a repository the GitHub App can see, then start a session on it.",
      denied: {
        title: "You cannot start an onboarding",
        description:
          "Starting one needs the onboarding manage permission on the scope the repository belongs to. You can still review sessions you have access to.",
      },
      repository: "Repository",
      searchPlaceholder: "Search repositories…",
      unavailableSuffix: " — unavailable",
      unavailable: "unavailable",
      openExisting: "Open existing session",
      starting: "Starting…",
      start: "Start onboarding",
      noMatches: {
        title: "No repository matches that search",
        description:
          "Nothing in a scope where you hold the onboarding manage permission matches. This is not a statement about which repositories exist.",
      },
      empty: {
        title: "No repositories you can onboard",
        description:
          "Drake projects no repository in a scope where you hold the onboarding manage permission. This is not a statement about which repositories exist.",
      },
      loadingMore: "Loading…",
      loadMore: "Load more repositories",
      complete: "That is every repository you can onboard.",
      preselectLoading: "Looking up the repository from the link…",
      preselectDenied:
        "That repository is not available to you here. Starting an onboarding needs the onboarding manage permission on the scope it belongs to.",
      selected: "Selected:",
      cannotStart: "This repository cannot be onboarded right now.",
      readsNothing: "Starting a session reads nothing yet. The analysis is a separate, explicit step.",
      listFailed: "The list could not be loaded.",
      nextPageFailed: "The next page could not be loaded.",
      startFailed: "The session could not be started. Nothing was changed.",
    },
    session: {
      draft: "Draft",
      discovery_pending: "Discovery pending",
      analyzing: "Analysing",
      needs_review: "Needs review",
      ready: "Ready to approve",
      approved: "Approved",
      applying: "Applying",
      imported: "Imported",
      failed: "Failed",
      cancelled: "Cancelled",
      not_configured: "Not configured",
      stale: "Stale",
      provider_unavailable: "GitHub unavailable",
    },
    action: {
      create: "Create",
      link: "Link to existing",
      update_metadata: "Update metadata",
      no_change: "No change",
      conflict: "Conflict",
      unmapped: "Unmapped",
      unsupported: "Unsupported",
    },
    gitops: {
      pending: "Pending at GitHub",
      active: "Pull request open",
      failed: "Failed",
      stale: "Base commit moved",
      cancelled: "Cancelled",
    },
    candidateBlocker: {
      security_gate_open: "Closed by a manual security review. Ask a platform owner to clear it.",
      repository_unavailable:
        "Drake cannot reach this repository — it is archived, disabled, or the installation lost access.",
      repository_not_ready:
        "Drake has not finished projecting this repository. Reconcile it first, then try again.",
      session_in_progress: "An onboarding is already open for this repository.",
    },
    errorGuidance: {
      security_gate_open: "This repository is closed by a manual security review.",
      provider_unavailable: "GitHub could not be reached. Nothing was changed.",
      permission_missing: "The GitHub App installation is missing a read permission it needs.",
      version_conflict: "This session changed while you were looking at it. Reloaded.",
      plan_stale: "The repository moved after this plan was reviewed. Analyse again.",
      plan_blocked: "This plan has conflicts that must be resolved before it can be approved.",
      plan_integrity_mismatch:
        "This plan no longer matches what was approved. Analyse and review it again.",
      idempotency_key_reused: "That request was already used for a different plan version.",
      invalid_session_state: "This action is not available from the session's current state.",
      analysis_required: "Analyse the repository first.",
      gitops_disabled: "Repository writes are disabled. No branch or pull request is created.",
      legacy_onboarding_retired: "That path is retired. Onboarding now runs through a reviewed plan.",
      already_imported: "This session has already been applied.",
      already_approved: "This plan version has already been approved.",
      plan_not_found: "That plan version no longer exists. Reloaded.",
      plan_superseded: "A newer analysis replaced this plan. Review the current one.",
    },
    detail: {
      gate: {
        title: "Security gate",
        cardTitle: "Closed by a manual security gate",
        description:
          "This repository cannot be onboarded until an operator closes the gate. Drake makes no provider call and issues no token for it.",
      },
      stale: {
        title: "Out of date",
        cardTitle: "The repository moved",
        description:
          "This plan describes a commit that is no longer the branch head. A review of a commit is not a review of its successor, so it cannot be applied. Analyse again.",
      },
      discovery: {
        title: "Safe discovery",
        notAnalysed: {
          title: "Not analysed yet",
          description: "No repository has been read for this session.",
        },
        commit: "Commit",
        filesRead: "Files read",
        manifest: "Manifest",
        found: "found",
        absent: "absent",
        analysed: "Analysed",
        partial: {
          title: "Partial analysis",
          description:
            "The analysis stopped at a budget, so this describes part of the repository. It is not a complete picture.",
        },
        pathsOnly:
          "Paths and digests only. Drake stores no file content, and never reads environment files, private keys, credentials or cluster configuration.",
      },
      session: {
        title: "Session",
        state: "State",
        planVersion: "Plan version",
        approved: "Approved",
        notApproved: "not approved",
        imported: "Imported",
        notImported: "not imported",
      },
      plan: {
        title: "Proposed changes",
        empty: {
          title: "No plan yet",
          description: "Analyse the repository to produce a proposal.",
        },
        version: "Plan {version}",
        commit: "Commit {sha}",
        digest: "Digest {digest}",
        items: "{count, plural, one {# item} other {# items}}",
        group: {
          create: "Would create",
          link: "Would link to an existing catalog row",
          update_metadata: "Would update metadata",
          no_change: "No change",
          conflict: "Needs a decision",
        },
        note: {
          update_metadata:
            "These rewrite fields on rows that already exist. Approving accepts these exact values.",
          conflict:
            "Apply is blocked until each of these is resolved. Drake refuses to choose rather than filing something under the wrong project.",
        },
        evidenceOnly: "Recorded as evidence only — no catalog row is written for it.",
        before: "before:",
        after: "after:",
        structuredValue: "(structured value)",
        listValue: "{count, plural, one {# item} other {# items}}",
        applyAvailable:
          "This plan can be applied. Applying writes to Drake's catalog and changes nothing in the repository.",
        applyBlocked: "Apply is blocked: {count} item(s) need a decision.",
        applyNoPermission:
          "You can review this plan but not apply it. Applying needs the onboarding apply permission.",
        applyNotApplicable: "This plan is not currently applicable.",
      },
      gitopsRequests: {
        title: "Manifest pull requests",
        openDraft: "Open draft pull request #{number} →",
        draftNote:
          "The pull request Drake opens is a {draft}, and deliberately incomplete: every {token} in it is a decision a person has to make. Fill them in and merge it in GitHub.",
        draft: "draft",
        mergeNote:
          "Merging it does not import anything into Drake — it puts the manifest in the repository, which is where Drake reads intent from. Analyse again afterwards and approve the plan; the import happens there.",
      },
    },
    actions: {
      title: "Actions",
      failedTitle: "That did not happen",
      reference: "{text} (reference {id})",
      networkError: "The request could not be sent. Nothing was changed.",
      analysing: "Analysing…",
      analyseAgain: "Analyse again",
      analyse: "Analyse repository",
      approve: "Approve plan",
      applying: "Applying…",
      apply: "Apply approved plan",
      cancel: "Cancel session",
      proposePullRequest: "Propose manifest pull request",
      downloadDraft: "Download manifest draft",
      writesDisabled: "Repository writes are disabled. No branch or pull request will be created.",
      approvalBlocked: "Approval is blocked: {count} item(s) need a decision.",
      noApplicablePlan: "There is no applicable plan to approve yet.",
      confirmApprove: {
        title: "Approve this plan?",
        confirm: "Approve",
        planVersion: "Plan version",
        commit: "Commit",
        digest: "Digest",
        items: "Items",
        note: "Approving records that you accept these exact values. It changes nothing on its own.",
      },
      confirmApply: {
        title: "Apply the approved plan?",
        confirm: "Apply",
        approvedVersion: "Approved version",
        note: "This writes the approved plan to Drake's catalog. It does not write to the repository.",
      },
      confirmCancel: {
        title: "Cancel this session?",
        confirm: "Cancel session",
        note: "The session closes and its plan is no longer applicable. Nothing is removed from the catalog — cancelling a session does not undo anything already applied.",
      },
      working: "Working…",
      back: "Back",
      result: {
        title: "Applied to the catalog",
        created_entities: "Created",
        linked_entities: "Linked",
        unchanged_entities: "Unchanged",
        metadata_updated: "Metadata updated",
        slo_definitions_created: "SLOs created",
        slo_definitions_updated: "SLOs updated",
        bindings_created: "Bindings created",
        notRecorded: "Not recorded",
        openProject: "Open the catalog project →",
      },
    },
  },
  tr: {
    page: {
      title: "Proje katılımı",
      description:
        "Drake depoyu statik olarak okur, bir katalog değişikliği önerir ve biri onaylayana kadar hiçbir şeyi uygulamaz.",
    },
    how: {
      title: "Nasıl çalışır",
      description: "Depodan katalog projesine yedi adım. Onaydan önce hiçbir şey değişmez.",
      nothingExecuted:
        "Depodaki hiçbir şey çalıştırılmaz: derleme, kurulum, betik, hook veya iş akışı yok. Drake, izin listesindeki meta veri dosyalarını tek bir değişmez commit'te okur.",
    },
    step: {
      integrationStatus: "Entegrasyon durumu",
      repository: "Depo",
      safeDiscovery: "Güvenli keşif",
      detectedStructure: "Tespit edilen yapı",
      review: "İnceleme",
      approval: "Onay",
      result: "Sonuç",
    },
    stepHint: {
      integrationStatus: "GitHub App hazır",
      repository: "Katılım için bir depo seçin",
      safeDiscovery: "Yalnızca meta veri dosyaları",
      detectedStructure: "Drake'in bulduğu yapı",
      review: "Öğe öğe",
      approval: "Tam olarak bu plan",
      result: "Kataloğa yazıldı",
    },
    notConfigured: {
      title: "GitHub yapılandırılmamış",
      description:
        "Drake depoları okuyamıyor. Hiçbir yere bağlanılmadı, hiçbir token verilmedi ve hiçbir depo listesi gösterilmiyor.",
      operatorNote:
        "GitHub App kimliğini ve kimlik bilgisi referanslarını bir operatör Drake dışında yapılandırır. Drake bu ekrandan asla bir kimlik bilgisi kabul etmez.",
    },
    missingInput: {
      feature_disabled: "GitHub App entegrasyonu kapalı.",
      app_identity: "Yapılandırılmış bir GitHub App kimliği yok.",
      private_key_reference: "Yapılandırılmış bir özel anahtar referansı yok.",
      webhook_secret_reference: "Yapılandırılmış bir webhook gizli anahtarı referansı yok.",
    },
    health: {
      needReview: "İnceleme bekleyen",
      imported: "İçe aktarılan",
      ofSessions: "{count} oturumdan",
      partialAnalyses: "Kısmi analizler",
      ofAnalyses: "{count} analizden · son: {when}",
      failedPullRequests: "Başarısız pull request'ler",
      ofPullRequests: "{count} manifest pull request'inden",
      gitopsOff: "GitOps pull request'leri kapalı. Drake hiçbir depoya yazmayacak.",
    },
    pipeline: {
      title: "Oturum hattı",
      description: "Kapsamınızdaki oturumların bulunduğu aşamalar.",
      sessions: "oturum",
      analyses: "Analizler",
      analysesFailed: "Başarısız analizler",
    },
    row: {
      plan: "Plan sürümü",
      planSummary: "v{version} · {count} öğe",
      decisions: "Kararlar",
      needReview: "{count} tanesi inceleme bekliyor",
      notAnalysed: "analiz edilmedi",
      opened: "{when} açıldı",
    },
    sessions: {
      title: "Oturumlar",
      description: "Kapsamınızdaki tüm katılımlar, en yeniden başlayarak.",
      total: "toplam {count}",
      empty: {
        title: "Katılım oturumu yok",
        description:
          "Kapsamınızda katılımı süren bir depo yok. Bu, hangi depoların var olduğuna dair bir ifade değildir.",
      },
    },
    start: {
      title: "Katılım başlat",
      description: "GitHub App'in görebildiği bir depoyu arayın, ardından üzerinde bir oturum başlatın.",
      denied: {
        title: "Katılım başlatamazsınız",
        description:
          "Başlatmak için deponun ait olduğu kapsamda katılım yönetme yetkisi gerekir. Erişiminiz olan oturumları yine de inceleyebilirsiniz.",
      },
      repository: "Depo",
      searchPlaceholder: "Depo ara…",
      unavailableSuffix: " — kullanılamıyor",
      unavailable: "kullanılamıyor",
      openExisting: "Mevcut oturumu aç",
      starting: "Başlatılıyor…",
      start: "Katılımı başlat",
      noMatches: {
        title: "Bu aramayla eşleşen depo yok",
        description:
          "Katılım yönetme yetkisine sahip olduğunuz bir kapsamda eşleşen bir şey yok. Bu, hangi depoların var olduğuna dair bir ifade değildir.",
      },
      empty: {
        title: "Katılım başlatabileceğiniz depo yok",
        description:
          "Drake, katılım yönetme yetkisine sahip olduğunuz bir kapsamda hiçbir depo yansıtmıyor. Bu, hangi depoların var olduğuna dair bir ifade değildir.",
      },
      loadingMore: "Yükleniyor…",
      loadMore: "Daha fazla depo yükle",
      complete: "Katılım başlatabileceğiniz depoların tamamı bu kadar.",
      preselectLoading: "Bağlantıdaki depo aranıyor…",
      preselectDenied:
        "Bu depo burada sizin için kullanılamıyor. Katılım başlatmak için deponun ait olduğu kapsamda katılım yönetme yetkisi gerekir.",
      selected: "Seçili:",
      cannotStart: "Bu depo için şu anda katılım başlatılamıyor.",
      readsNothing: "Oturum başlatmak henüz hiçbir şey okumaz. Analiz ayrı ve açık bir adımdır.",
      listFailed: "Liste yüklenemedi.",
      nextPageFailed: "Sonraki sayfa yüklenemedi.",
      startFailed: "Oturum başlatılamadı. Hiçbir şey değiştirilmedi.",
    },
    session: {
      draft: "Taslak",
      discovery_pending: "Keşif bekliyor",
      analyzing: "Analiz ediliyor",
      needs_review: "İnceleme gerekiyor",
      ready: "Onaya hazır",
      approved: "Onaylandı",
      applying: "Uygulanıyor",
      imported: "İçe aktarıldı",
      failed: "Başarısız",
      cancelled: "İptal edildi",
      not_configured: "Yapılandırılmamış",
      stale: "Güncel değil",
      provider_unavailable: "GitHub'a erişilemiyor",
    },
    action: {
      create: "Oluştur",
      link: "Mevcut kayda bağla",
      update_metadata: "Meta veriyi güncelle",
      no_change: "Değişiklik yok",
      conflict: "Çakışma",
      unmapped: "Eşlenmemiş",
      unsupported: "Desteklenmiyor",
    },
    gitops: {
      pending: "GitHub'da bekliyor",
      active: "Pull request açık",
      failed: "Başarısız",
      stale: "Temel commit değişti",
      cancelled: "İptal edildi",
    },
    candidateBlocker: {
      security_gate_open:
        "Manuel güvenlik incelemesi nedeniyle kapalı. Kaldırması için bir platform sahibine başvurun.",
      repository_unavailable:
        "Drake bu depoya erişemiyor: depo arşivlenmiş, devre dışı bırakılmış ya da kurulum erişimini yitirmiş.",
      repository_not_ready:
        "Drake bu depoyu yansıtmayı henüz tamamlamadı. Önce uzlaştırın, sonra yeniden deneyin.",
      session_in_progress: "Bu depo için zaten açık bir katılım var.",
    },
    errorGuidance: {
      security_gate_open: "Bu depo manuel güvenlik incelemesi nedeniyle kapalı.",
      provider_unavailable: "GitHub'a ulaşılamadı. Hiçbir şey değiştirilmedi.",
      permission_missing: "GitHub App kurulumunda ihtiyaç duyulan bir okuma yetkisi eksik.",
      version_conflict: "Siz bakarken bu oturum değişti. Yeniden yüklendi.",
      plan_stale: "Bu plan incelendikten sonra depo değişti. Yeniden analiz edin.",
      plan_blocked: "Bu planda onaylanmadan önce çözülmesi gereken çakışmalar var.",
      plan_integrity_mismatch:
        "Bu plan artık onaylananla eşleşmiyor. Yeniden analiz edip inceleyin.",
      idempotency_key_reused: "Bu istek zaten farklı bir plan sürümü için kullanılmış.",
      invalid_session_state: "Bu işlem oturumun mevcut durumunda kullanılamaz.",
      analysis_required: "Önce depoyu analiz edin.",
      gitops_disabled: "Depo yazma işlemleri kapalı. Dal veya pull request oluşturulmaz.",
      legacy_onboarding_retired:
        "Bu yol kaldırıldı. Katılım artık incelenen bir plan üzerinden yürür.",
      already_imported: "Bu oturum zaten uygulandı.",
      already_approved: "Bu plan sürümü zaten onaylandı.",
      plan_not_found: "Bu plan sürümü artık yok. Yeniden yüklendi.",
      plan_superseded: "Daha yeni bir analiz bu planın yerini aldı. Güncel olanı inceleyin.",
    },
    detail: {
      gate: {
        title: "Güvenlik kapısı",
        cardTitle: "Manuel güvenlik kapısı nedeniyle kapalı",
        description:
          "Bir operatör kapıyı kaldırana kadar bu depo için katılım yapılamaz. Drake bu depo için sağlayıcı çağrısı yapmaz ve token vermez.",
      },
      stale: {
        title: "Güncel değil",
        cardTitle: "Depo değişti",
        description:
          "Bu plan, artık dalın başında olmayan bir commit'i açıklıyor. Bir commit'in incelemesi ardılının incelemesi sayılmaz; bu yüzden uygulanamaz. Yeniden analiz edin.",
      },
      discovery: {
        title: "Güvenli keşif",
        notAnalysed: {
          title: "Henüz analiz edilmedi",
          description: "Bu oturum için henüz depo okunmadı.",
        },
        commit: "Commit SHA'sı",
        filesRead: "Okunan dosya",
        manifest: "Manifest dosyası",
        found: "bulundu",
        absent: "yok",
        analysed: "Analiz zamanı",
        partial: {
          title: "Kısmi analiz",
          description:
            "Analiz bir bütçe sınırında durdu; bu yüzden deponun yalnızca bir kısmını açıklıyor. Tam bir resim değildir.",
        },
        pathsOnly:
          "Yalnızca yollar ve özetler. Drake dosya içeriği saklamaz; ortam dosyalarını, özel anahtarları, kimlik bilgilerini veya küme yapılandırmasını asla okumaz.",
      },
      session: {
        title: "Oturum",
        state: "Durum",
        planVersion: "Plan sürümü",
        approved: "Onay",
        notApproved: "onaylanmadı",
        imported: "İçe aktarma",
        notImported: "içe aktarılmadı",
      },
      plan: {
        title: "Önerilen değişiklikler",
        empty: {
          title: "Henüz plan yok",
          description: "Öneri üretmek için depoyu analiz edin.",
        },
        version: "Plan sürümü {version}",
        commit: "Commit SHA'sı {sha}",
        digest: "Özet {digest}",
        items: "{count} öğe",
        group: {
          create: "Oluşturulacak",
          link: "Mevcut katalog kaydına bağlanacak",
          update_metadata: "Meta verisi güncellenecek",
          no_change: "Değişiklik yok",
          conflict: "Karar gerekiyor",
        },
        note: {
          update_metadata:
            "Bunlar zaten var olan kayıtlardaki alanları yeniden yazar. Onaylamak tam olarak bu değerleri kabul etmek demektir.",
          conflict:
            "Bunların her biri çözülene kadar uygulama engellenir. Drake, bir şeyi yanlış projenin altına koymaktansa seçim yapmayı reddeder.",
        },
        evidenceOnly: "Yalnızca kanıt olarak kaydedildi; bunun için katalog kaydı yazılmaz.",
        before: "önce:",
        after: "sonra:",
        structuredValue: "(yapılandırılmış değer)",
        listValue: "{count} öğe",
        applyAvailable:
          "Bu plan uygulanabilir. Uygulamak Drake'in kataloğuna yazar ve depoda hiçbir şeyi değiştirmez.",
        applyBlocked: "Uygulama engellendi: {count} öğe için karar gerekiyor.",
        applyNoPermission:
          "Bu planı inceleyebilir ancak uygulayamazsınız. Uygulamak için katılım uygulama yetkisi gerekir.",
        applyNotApplicable: "Bu plan şu anda uygulanabilir değil.",
      },
      gitopsRequests: {
        title: "Manifest pull request'leri",
        openDraft: "#{number} numaralı taslak pull request'i aç →",
        draftNote:
          "Drake'in açtığı pull request bir {draft} ve bilerek eksik bırakılmıştır: içindeki her {token}, bir kişinin vermesi gereken bir karardır. Bunları doldurup GitHub'da birleştirin.",
        draft: "taslaktır",
        mergeNote:
          "Birleştirmek Drake'e hiçbir şey aktarmaz; manifesti depoya koyar ve Drake niyeti oradan okur. Ardından yeniden analiz edip planı onaylayın; içe aktarma orada gerçekleşir.",
      },
    },
    actions: {
      title: "İşlemler",
      failedTitle: "Bu gerçekleşmedi",
      reference: "{text} (referans {id})",
      networkError: "İstek gönderilemedi. Hiçbir şey değiştirilmedi.",
      analysing: "Analiz ediliyor…",
      analyseAgain: "Yeniden analiz et",
      analyse: "Depoyu analiz et",
      approve: "Planı onayla",
      applying: "Uygulanıyor…",
      apply: "Onaylanan planı uygula",
      cancel: "Oturumu iptal et",
      proposePullRequest: "Manifest pull request'i öner",
      downloadDraft: "Manifest taslağını indir",
      writesDisabled: "Depo yazma işlemleri kapalı. Dal veya pull request oluşturulmayacak.",
      approvalBlocked: "Onay engellendi: {count} öğe için karar gerekiyor.",
      noApplicablePlan: "Henüz onaylanacak uygulanabilir bir plan yok.",
      confirmApprove: {
        title: "Bu plan onaylansın mı?",
        confirm: "Onayla",
        planVersion: "Plan sürümü",
        commit: "Commit SHA'sı",
        digest: "Özet",
        items: "Öğe sayısı",
        note: "Onaylamak, tam olarak bu değerleri kabul ettiğinizi kaydeder. Tek başına hiçbir şeyi değiştirmez.",
      },
      confirmApply: {
        title: "Onaylanan plan uygulansın mı?",
        confirm: "Uygula",
        approvedVersion: "Onaylanan sürüm",
        note: "Bu, onaylanan planı Drake'in kataloğuna yazar. Depoya yazmaz.",
      },
      confirmCancel: {
        title: "Bu oturum iptal edilsin mi?",
        confirm: "Oturumu iptal et",
        note: "Oturum kapanır ve planı artık uygulanamaz. Katalogdan hiçbir şey kaldırılmaz; oturumu iptal etmek zaten uygulanmış hiçbir şeyi geri almaz.",
      },
      working: "İşleniyor…",
      back: "Geri",
      result: {
        title: "Kataloğa uygulandı",
        created_entities: "Oluşturulan",
        linked_entities: "Bağlanan",
        unchanged_entities: "Değişmeyen",
        metadata_updated: "Güncellenen meta veri",
        slo_definitions_created: "Oluşturulan SLO'lar",
        slo_definitions_updated: "Güncellenen SLO'lar",
        bindings_created: "Oluşturulan bağlamalar",
        notRecorded: "Kaydedilmedi",
        openProject: "Katalog projesini aç →",
      },
    },
  },
});
