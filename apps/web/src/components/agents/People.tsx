"use client";

/**
 * People in Agent Visibility: who used an MCP server, or who a run was
 * for, as the telemetry names them (`user.id` / `user.name`). Initials on a
 * tinted disc, the same colour for the same person everywhere on the page.
 *
 * A name is shown, never an email (the API drops those). A person without
 * a name shows their id. Nobody named is said plainly — unknown, not "no one".
 */

import { initials, type McpPerson, type RunPerson } from "@/lib/agents";
import { useFormat, useT } from "@/lib/i18n";

type Person = Pick<RunPerson, "key" | "id" | "name">;

/** A stable colour per person: a small hash of their key into the series. */
export function personColor(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) | 0;
  return `var(--series-${(Math.abs(hash) % 6) + 1})`;
}

export function displayName(person: Person, unnamed: string): string {
  return person.name ?? person.id ?? unnamed;
}

export function Avatar({
  person,
  size = "sm",
  title,
}: {
  person: Person;
  size?: "xs" | "sm" | "md";
  title?: string;
}) {
  const color = personColor(person.key);
  const box =
    size === "xs"
      ? "h-[22px] w-[22px] text-[9px]"
      : size === "md"
        ? "h-8 w-8 text-[11px]"
        : "h-6 w-6 text-[10px]";
  return (
    <span
      title={title}
      aria-hidden={title ? undefined : true}
      className={`relative inline-flex shrink-0 items-center justify-center rounded-full font-semibold tracking-tight text-ink ${box}`}
      style={{
        background: `color-mix(in srgb, ${color} 28%, var(--surface-1))`,
        // The card's own colour around each disc keeps a stack readable.
        boxShadow: `0 0 0 2px var(--surface-1), inset 0 0 0 1px color-mix(in srgb, ${color} 60%, transparent)`,
      }}
    >
      {initials(person)}
    </span>
  );
}

/** Overlapping initials, the busiest first; the rest as "+n more". */
export function AvatarStack({
  people,
  max = 3,
  testId,
}: {
  people: McpPerson[];
  max?: number;
  testId?: string;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  if (people.length === 0) return null;
  const shown = people.slice(0, max);
  const label = people
    .map((p) => displayName(p, t("mcp.unnamedPerson")))
    .join(", ");
  return (
    <span
      className="inline-flex items-center"
      data-testid={testId}
      aria-label={`${t("mcp.usedBy")}: ${label}`}
      role="img"
    >
      <span className="flex -space-x-1">
        {shown.map((p) => (
          <Avatar
            key={p.key}
            person={p}
            size="xs"
            title={t("mcp.person", {
              name: displayName(p, t("mcp.unnamedPerson")),
              calls: fmt.number(p.calls),
              when: fmt.relative(p.last_seen_at),
            })}
          />
        ))}
      </span>
      {people.length > shown.length ? (
        <span className="ml-1 text-micro text-ink-muted tabular-nums">
          {t("mcp.more", { count: people.length - shown.length })}
        </span>
      ) : null}
    </span>
  );
}

/** A name with its initials: for a run, "For  (GC) Gencay Coskun". */
export function PersonChip({ person }: { person: Person }) {
  const t = useT("agents");
  const name = displayName(person, t("mcp.unnamedPerson"));
  return (
    <span
      className="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full bg-surface-2 py-0.5 pr-2.5 pl-0.5 text-micro font-medium text-ink"
      title={person.id && person.name ? `${person.name} · ${person.id}` : name}
      data-testid="person-chip"
    >
      <Avatar person={person} size="xs" />
      <span className="truncate">{name}</span>
    </span>
  );
}
