"use client";

/**
 * One tooltip for the whole dashboard: every chart says what is under the
 * pointer (time, value, unit) instead of leaving the reader to guess from
 * the shape. Lines are plain [label, value] pairs; the first is the title.
 */

import { createContext, useCallback, useContext, useMemo, useState } from "react";

import s from "./dashboards.module.css";

export type TipLine = [string, string] | [string];
interface TipApi {
  show: (event: React.MouseEvent, lines: TipLine[]) => void;
  hide: () => void;
}

const TipContext = createContext<TipApi>({ show: () => undefined, hide: () => undefined });

export function TipProvider({ children }: { children: React.ReactNode }) {
  const [tip, setTip] = useState<{ x: number; y: number; lines: TipLine[] } | null>(null);
  const show = useCallback((event: React.MouseEvent, lines: TipLine[]) => {
    setTip({ x: event.clientX, y: event.clientY, lines });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  const api = useMemo(() => ({ show, hide }), [show, hide]);
  const flip = tip !== null && typeof window !== "undefined" && tip.x > window.innerWidth - 260;
  return (
    <TipContext.Provider value={api}>
      {children}
      {tip && (
        <div
          className={s.tip}
          role="tooltip"
          style={{ left: flip ? undefined : tip.x + 14, right: flip ? window.innerWidth - tip.x + 14 : undefined, top: tip.y + 14 }}
        >
          {tip.lines.map((line, i) =>
            line.length === 1 ? (
              <b key={i}>{line[0]}</b>
            ) : (
              <span key={i}>
                <em>{line[0]}</em>
                <strong>{line[1]}</strong>
              </span>
            ),
          )}
        </div>
      )}
    </TipContext.Provider>
  );
}

export const useTip = () => useContext(TipContext);
