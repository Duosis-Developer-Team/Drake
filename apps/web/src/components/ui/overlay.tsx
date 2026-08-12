"use client";

/**
 * Overlays: the drawer, the modal and the popover.
 *
 * All three share one behaviour module because the accessibility of an
 * overlay is the whole of it, and three separate implementations means three
 * chances to forget: Escape closes, a click outside closes, focus moves in on
 * open and returns to the trigger on close, focus cannot tab out while open,
 * and the page behind does not scroll.
 */

import { X } from "lucide-react";
import { useCallback, useEffect, useId, useRef } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Escape, outside click, focus trap and focus restore for one open surface.
 *
 * `trap` is false for popovers: a dropdown that traps focus stops Tab from
 * moving to the next control, which is the behaviour people expect from a
 * menu but not from a filter.
 */
export function useDismissable<T extends HTMLElement>({
  open,
  onClose,
  trap = true,
}: {
  open: boolean;
  onClose: () => void;
  trap?: boolean;
}) {
  const ref = useRef<T | null>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreTo.current = document.activeElement as HTMLElement | null;

    const node = ref.current;
    if (node) {
      const target =
        node.querySelector<HTMLElement>("[data-autofocus]") ??
        node.querySelector<HTMLElement>(FOCUSABLE) ??
        node;
      // A container without a tabindex cannot hold focus; give it one so the
      // reader starts inside the overlay rather than back at the document.
      if (target === node && !node.hasAttribute("tabindex")) node.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !trap || !ref.current) return;
      const focusable = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (element) => element.offsetParent !== null || element === document.activeElement,
      );
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const onPointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };

    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("mousedown", onPointerDown);
      restoreTo.current?.focus?.({ preventScroll: true });
    };
  }, [open, onClose, trap]);

  return ref;
}

/** Stops the page behind an overlay from scrolling while it is open. */
export function useScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [active]);
}

/**
 * A side sheet for detail without losing the list behind it.
 *
 * Used for row detail everywhere, because navigating away from a filtered
 * table and coming back is the single most common way to lose your place.
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: "md" | "lg";
}) {
  const titleId = useId();
  const close = useCallback(() => onClose(), [onClose]);
  const ref = useDismissable<HTMLDivElement>({ open, onClose: close });
  useScrollLock(open);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="drawer">
      <div aria-hidden className="absolute inset-0 bg-[var(--scrim)] motion-safe:animate-[fade-in_140ms_ease-out]" />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`relative flex h-full w-full flex-col border-l border-border bg-surface shadow-overlay motion-safe:animate-[slide-in-right_240ms_var(--ease-entrance)] ${
          width === "lg" ? "max-w-2xl" : "max-w-lg"
        }`}
      >
        <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-title font-semibold text-ink">
              {title}
            </h2>
            {description ? (
              <p className="mt-0.5 text-caption text-ink-secondary">{description}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control border border-border text-ink-secondary transition-colors hover:bg-surface-hover"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
        {footer ? <div className="border-t border-border px-4 py-3">{footer}</div> : null}
      </div>
    </div>
  );
}

/** A centred dialog, for a decision that must be made before continuing. */
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const titleId = useId();
  const ref = useDismissable<HTMLDivElement>({ open, onClose });
  useScrollLock(open);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" data-testid="modal">
      <div aria-hidden className="absolute inset-0 bg-[var(--scrim)]" />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative w-full max-w-md rounded-overlay border border-border bg-surface shadow-overlay motion-safe:animate-[scale-in_180ms_var(--ease-entrance)]"
      >
        <div className="border-b border-border px-4 py-3">
          <h2 id={titleId} className="text-title font-semibold text-ink">
            {title}
          </h2>
        </div>
        <div className="px-4 py-4">{children}</div>
        {footer ? (
          <div className="flex justify-end gap-2 border-t border-border px-4 py-3">{footer}</div>
        ) : null}
      </div>
    </div>
  );
}
