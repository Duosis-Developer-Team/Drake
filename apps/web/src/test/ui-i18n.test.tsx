/**
 * The shared primitives, in Turkish.
 *
 * Every other test of these components runs without a provider and asserts
 * English; this one wraps them in `<LocaleProvider locale="tr">` to prove the
 * wiring — that the defaults, the tone words, the range presets and the
 * formatters all follow the locale. It also pins the English path: the
 * `ui.token.*` / `ui.tone.*` / `ui.range.*` entries must equal what the
 * pure functions produced before the catalogue existed, so a caller that
 * never passes a locale sees no change.
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChartFrame } from "@/components/charts/ChartFrame";
import { Provenance } from "@/components/provenance/Provenance";
import { DISTINCT_STATE_KINDS, DataState } from "@/components/state/DataState";
import { DataTable, Pagination } from "@/components/ui/DataTable";
import { Stat } from "@/components/ui/Stat";
import { HealthWord, StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/states";
import { TimeRangeControl } from "@/components/telemetry/TimeRangeControl";
import { formatRelative, formatUnit, unitLabel, UNIT_LABELS } from "@/lib/design/format";
import {
  TONES,
  humanize,
  thresholdLabel,
  toneSpec,
  type StatusTone,
} from "@/lib/design/status";
import { LocaleProvider } from "@/lib/i18n";
import { ui } from "@/lib/i18n/messages/ui";
import { RANGE_PRESETS, formatValue, rangePresetLabel } from "@/lib/telemetry";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/projects/p1",
  useSearchParams: () => new URLSearchParams("range=24h"),
}));

function tr(node: React.ReactNode) {
  return render(<LocaleProvider locale="tr">{node}</LocaleProvider>);
}

describe("DataState in Turkish", () => {
  it("renders every kind with its testid, in Turkish", () => {
    for (const kind of DISTINCT_STATE_KINDS) {
      const { unmount } = tr(<DataState kind={kind} />);
      expect(screen.getByTestId(`state-${kind}`)).toBeInTheDocument();
      unmount();
    }
  });

  it("translates the default copy of each state", () => {
    tr(<DataState kind="no-data" />);
    expect(screen.getByTestId("state-no-data")).toHaveTextContent("Bu pencerede veri yok");
    expect(screen.getByTestId("state-no-data")).not.toHaveTextContent("0");

    tr(<DataState kind="error" onRetry={() => {}} />);
    expect(screen.getByTestId("state-error")).toHaveTextContent("Sorgu başarısız");
    expect(screen.getByRole("button", { name: "Yeniden dene" })).toBeInTheDocument();

    tr(<DataState kind="permission-denied" />);
    expect(screen.getByTestId("state-permission-denied")).toHaveTextContent("Yetki gerekli");

    tr(<DataState kind="zero" />);
    expect(screen.getByTestId("state-zero")).toHaveTextContent("gerçek bir 0 değeri");

    tr(<DataState kind="unknown" />);
    expect(screen.getByTestId("state-unknown")).toHaveTextContent("Bilinmiyor");

    tr(<DataState kind="not-configured" />);
    expect(screen.getByTestId("state-not-configured")).toHaveTextContent("Yapılandırılmamış");

    tr(<DataState kind="empty" />);
    expect(screen.getByTestId("state-empty")).toHaveTextContent("Henüz bir şey yok");

    tr(<DataState kind="loading" />);
    expect(screen.getByTestId("state-loading").textContent).toBe("Yükleniyor");

    tr(<DataState kind="partial" />);
    expect(screen.getByTestId("state-partial")).toHaveTextContent("Kısmi sonuç.");

    tr(<DataState kind="estimated" />);
    expect(screen.getByTestId("state-estimated")).toHaveTextContent("Tahmini.");
  });

  it("stale keeps the UTC instant and localizes the words around it", () => {
    tr(<DataState kind="stale" lastSuccessAt="2026-08-06T00:00:00Z" />);
    const banner = screen.getByTestId("state-stale");
    expect(banner).toHaveTextContent("Son bilinen değerler.");
    expect(banner).toHaveTextContent("Son başarılı güncelleme:");
    expect(banner).toHaveTextContent("2026-08-06 00:00:00 UTC");
    expect(banner).toHaveTextContent(/önce\)/);
    expect(banner.querySelector("time")).toHaveAttribute("datetime", "2026-08-06T00:00:00Z");
  });

  it("an explicit title or description still wins over the translated default", () => {
    tr(<DataState kind="no-data" title="Custom" description="Given by the caller" />);
    expect(screen.getByTestId("state-no-data")).toHaveTextContent("Custom");
    expect(screen.getByTestId("state-no-data")).toHaveTextContent("Given by the caller");
    expect(screen.getByTestId("state-no-data")).not.toHaveTextContent("veri yok");
  });
});

describe("StatusBadge in Turkish", () => {
  it("uses the glossary word for each tone", () => {
    const expected: Record<string, string> = {
      success: "Sağlıklı",
      healthy: "Sağlıklı",
      warning: "Uyarı",
      critical: "Kritik",
      unknown: "Bilinmiyor",
      stale: "Güncel değil",
      "not-applicable": "Uygulanamaz",
      denied: "Yetki gerekli",
      pending: "Bekliyor",
    };
    for (const [status, word] of Object.entries(expected)) {
      const { unmount } = tr(<StatusBadge status={status as StatusTone} />);
      expect(screen.getByTestId(`status-${status}`)).toHaveTextContent(word);
      unmount();
    }
  });

  it("badges a raw backend word through the token catalogue", () => {
    tr(<HealthWord value="not_applicable" />);
    expect(screen.getByTestId("status-not-applicable")).toHaveTextContent("Uygulanamaz");
  });

  it("a caller's own label is never translated", () => {
    tr(<StatusBadge status="success" label="Reporting" />);
    expect(screen.getByTestId("status-success")).toHaveTextContent("Reporting");
  });
});

describe("TimeRangeControl in Turkish", () => {
  it("labels the group and every preset from the catalogue", () => {
    tr(<TimeRangeControl />);
    const group = screen.getByRole("group", { name: "Zaman aralığı" });
    expect(within(group).getByRole("button", { name: "Son 1 saat" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Son 24 saat" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(group).getByRole("button", { name: "Son 7 gün" })).toBeInTheDocument();
  });
});

describe("Stat and DataTable in Turkish", () => {
  it("formats the value, the threshold verdict and the comparison in Turkish", () => {
    tr(
      <Stat
        label="p95"
        value={4.2}
        unit="seconds"
        thresholds={{ warn: 5, critical: 10, direction: "above" }}
        comparison={{ previous: 4, periodLabel: "önceki 24 saat", goodDirection: "down" }}
      />,
    );
    const stat = screen.getByTestId("stat");
    expect(stat).toHaveTextContent("4,20 sn");
    expect(stat).toHaveTextContent("eşik içinde");
    expect(stat).toHaveTextContent("önceki 24 saat ile kıyasla");
  });

  it("an empty table shows the translated empty state and pagination words", () => {
    tr(
      <>
        <DataTable<{ id: string }>
          rows={[]}
          columns={[{ key: "id", header: "ID", cell: (row) => row.id }]}
          rowKey={(row) => row.id}
          caption="Rows"
          emptyState={<EmptyState compact />}
        />
        <Pagination offset={0} limit={20} count={0} total={0} onOffsetChange={() => {}} />
      </>,
    );
    expect(screen.getByTestId("state-empty")).toHaveTextContent("Henüz bir şey yok");
    expect(screen.getByText("0 kayıttan 0–0 arası gösteriliyor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Önceki" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "İleri" })).toBeInTheDocument();
  });
});

describe("ChartFrame and Provenance in Turkish", () => {
  it("translates the frame's states, the step notice and the table disclosure", () => {
    tr(
      <ChartFrame title="İstek hızı" unit="req/s" status="no-data">
        <p>plot</p>
      </ChartFrame>,
    );
    expect(screen.getByTestId("state-no-data")).toHaveTextContent("Bu pencerede veri yok");

    tr(
      <ChartFrame
        title="İstek hızı"
        unit="req/s"
        status="ready"
        window={{
          from: "2026-08-10T00:00:00Z",
          to: "2026-08-11T00:00:00Z",
          stepSeconds: 300,
          stepAdjusted: true,
        }}
        table={<table />}
      >
        <p>plot</p>
      </ChartFrame>,
    );
    expect(screen.getByText("adım 300 sn'ye genişletildi")).toBeInTheDocument();
    expect(screen.getByText("Tablo olarak görüntüle")).toBeInTheDocument();
  });

  it("labels provenance fields and the not-configured fallback in Turkish", () => {
    tr(<Provenance />);
    const node = screen.getByTestId("provenance");
    expect(node).toHaveTextContent("Kaynak:");
    expect(node).toHaveTextContent("Yöntem:");
    expect(node.textContent?.match(/yapılandırılmamış/g)?.length).toBe(6);
  });
});

describe("the pure functions, with and without a locale", () => {
  it("humanize reads the catalogue for Turkish and stays English by default", () => {
    expect(humanize("not_applicable", "tr")).toBe("Uygulanamaz");
    expect(humanize("reconcile_required", "tr")).toBe("Uzlaştırma gerekli");
    expect(humanize("not_applicable")).toBe("Not applicable");
    expect(humanize("reconcile_required")).toBe("Reconcile required");
    expect(humanize(null, "tr")).toBe("Bilinmiyor");
    expect(humanize(null)).toBe("Unknown");
    // A word the catalogue does not know is still shown, spaced, in both.
    expect(humanize("brand_new_state", "tr")).toBe("Brand new state");
    expect(humanize("brand_new_state")).toBe("Brand new state");
  });

  it("every English token entry equals what humanize always produced", () => {
    for (const [token, english] of Object.entries(ui.en.token)) {
      expect(humanize(token), token).toBe(english);
    }
  });

  it("toneSpec keeps its English label and identity unless handed a locale", () => {
    for (const tone of Object.keys(TONES) as StatusTone[]) {
      expect(toneSpec(tone)).toBe(TONES[tone]);
      expect(ui.en.tone[tone]).toBe(TONES[tone].label);
      expect(toneSpec(tone, "tr").label).not.toBe(TONES[tone].label);
      expect(toneSpec(tone, "tr").icon).toBe(TONES[tone].icon);
    }
    expect(toneSpec("stale", "tr").label).toBe("Güncel değil");
    expect(toneSpec("success", "tr").label).toBe("Sağlıklı");
    expect(toneSpec("not-applicable", "tr").label).toBe("Uygulanamaz");
  });

  it("threshold verdicts follow the locale", () => {
    expect(thresholdLabel("success", true)).toBe("within threshold");
    expect(thresholdLabel("success", true, "tr")).toBe("eşik içinde");
    expect(thresholdLabel("critical", true, "tr")).toBe("kritik eşik aşıldı");
    expect(thresholdLabel("unknown", false, "tr")).toBe("eşik tanımlı değil");
    expect(thresholdLabel("unknown", true, "tr")).toBe("ölçülmedi");
  });

  it("formatRelative and formatUnit localize their words and decimal marks", () => {
    const now = new Date("2026-09-22T12:00:00Z");
    expect(formatRelative("2026-09-22T11:56:00Z", now)).toBe("4m ago");
    expect(formatRelative("2026-09-22T11:56:00Z", now, "tr")).toBe("4 dk önce");
    expect(formatRelative("2026-09-22T14:00:00Z", now, "tr")).toBe("2 sa sonra");

    expect(formatUnit(4.2, "seconds")).toBe("4.20 s");
    expect(formatUnit(4.2, "seconds", {}, "tr")).toBe("4,20 sn");
    expect(formatUnit(90, "seconds", {}, "tr")).toBe("1 dk 30 sn");
    expect(formatUnit(5400, "seconds", { compact: true }, "tr")).toBe("1 sa");
    expect(formatUnit(0.42, "seconds", {}, "tr")).toBe("420 ms");
    expect(formatUnit(1, "cores", {}, "tr")).toBe("1,00 çekirdek");
    expect(formatUnit(12_345, "count", {}, "tr")).toBe("12.345");
    expect(formatUnit(3.5, "requests_per_second", {}, "tr")).toBe("3,5 istek/sn");
    expect(formatUnit(1024, "bytes", {}, "tr")).toBe("1,00 KiB");
    expect(formatUnit(null, "bytes", {}, "tr")).toBe("—");
  });

  it("unit labels come from common in Turkish and match UNIT_LABELS in English", () => {
    for (const unit of Object.keys(UNIT_LABELS)) {
      expect(unitLabel(unit)).toBe(UNIT_LABELS[unit]);
    }
    expect(unitLabel("requests_per_second", "tr")).toBe("istek/sn");
    expect(unitLabel("bytes", "tr")).toBe("bayt");
    expect(unitLabel("something_unknown", "tr")).toBe("something_unknown");
  });

  it("range presets and telemetry values follow the locale", () => {
    for (const preset of RANGE_PRESETS) {
      expect(rangePresetLabel(preset.key)).toBe(preset.label);
      expect(ui.en.range[preset.key]).toBe(preset.label);
    }
    expect(rangePresetLabel("24h", "tr")).toBe("Son 24 saat");
    expect(formatValue(2, "cores")).toBe("2.00 cores");
    expect(formatValue(2, "cores", "tr")).toBe("2.00 çekirdek");
    expect(formatValue(0.5, "seconds", "tr")).toBe("500 ms");
    expect(formatValue(4.2, "seconds", "tr")).toBe("4.20 sn");
    expect(formatValue(3, "requests_per_second", "tr")).toBe("3.00 istek/sn");
  });
});
