import { cn } from "cn";

export function EmptyState({
  title,
  children,
  className,
  compact,
}: {
  title: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center", compact ? "py-8" : "py-16", className)}>
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

export function ErrorNotice({ error, what }: { error: Error; what: string }) {
  return (
    <p role="alert" className="rounded-md border border-status-crit/40 bg-status-crit/5 px-3 py-2 text-sm text-status-crit">
      Failed to load {what}: {error.message}
    </p>
  );
}
