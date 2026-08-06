export function Card({
  title,
  children,
  footer,
  "data-testid": testId,
}: {
  title?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <section
      data-testid={testId}
      className="flex flex-col rounded-xl border border-border bg-surface shadow-sm"
    >
      {title ? (
        <header className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
        </header>
      ) : null}
      <div className="flex-1 px-4 py-4">{children}</div>
      {footer ? <footer className="border-t border-border px-4 py-2.5">{footer}</footer> : null}
    </section>
  );
}
