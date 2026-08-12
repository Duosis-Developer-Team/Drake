"use client";

/**
 * Human names for identifier path segments.
 *
 * The breadcrumb is derived from the URL and has no data of its own, so
 * `/projects/8e130614-.../environments/62af...` would otherwise read as two
 * UUIDs. Fetching a name per crumb on every navigation would mean four extra
 * requests to render a header, and they would arrive after the page did.
 *
 * Instead the page — which already loaded the record — publishes the label it
 * has. Until it does, the crumb shows the identifier, which is honest: it is
 * what the URL says.
 *
 * Deliberately a module-level store rather than context: the breadcrumb lives
 * in the shell, above the route, so a provider around the page could not
 * reach it without wrapping the entire tree in something that re-renders on
 * every navigation.
 */

import { useEffect, useSyncExternalStore } from "react";

const labels = new Map<string, string>();
const subscribers = new Set<() => void>();
/** Bumped on every mutation so `getSnapshot` returns a stable value. */
let version = 0;

function emit() {
  version += 1;
  for (const subscriber of subscribers) subscriber();
}

/**
 * Publish the display name for one identifier segment.
 *
 * Registered for as long as the page is mounted and dropped on unmount, so a
 * stale name cannot outlive the record it came from.
 */
export function useCrumbLabel(id: string | undefined, label: string | undefined | null): void {
  useEffect(() => {
    if (!id || !label) return;
    if (labels.get(id) === label) return;
    labels.set(id, label);
    emit();
    return () => {
      labels.delete(id);
      emit();
    };
  }, [id, label]);
}

export function useCrumbLabels(): (id: string) => string | undefined {
  useSyncExternalStore(
    (onChange) => {
      subscribers.add(onChange);
      return () => subscribers.delete(onChange);
    },
    () => version,
    () => version,
  );
  return (id: string) => labels.get(id);
}
