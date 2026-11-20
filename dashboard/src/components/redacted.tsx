import { EyeOffIcon } from "lucide-react";
import { cn } from "cn";
import type { Sensitive } from "@/api/types";
import { isRedacted } from "@/lib/format";

/** Renders a `sensitive` field: verbatim text for engineer+, a muted chip for the
 * leadership marker `{redacted: true, length: n}` (design D12), never an empty string. */
export function SensitiveText({
  value,
  className,
  empty = "—",
  clamp,
}: {
  value: Sensitive | undefined;
  className?: string;
  empty?: string;
  /** Max characters before the verbatim text is truncated with an ellipsis. */
  clamp?: number;
}) {
  if (value == null) return <span className="text-muted-foreground">{empty}</span>;
  if (isRedacted(value)) return <RedactedChip length={value.length} className={className} />;
  const text = clamp && value.length > clamp ? `${value.slice(0, clamp)}…` : value;
  return (
    <span className={cn("whitespace-pre-wrap break-words", className)} title={clamp ? value : undefined}>
      {text}
    </span>
  );
}

export function RedactedChip({ length, className }: { length: number; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-dashed bg-muted px-1.5 py-0.5 text-xs text-muted-foreground",
        className,
      )}
      title="Redacted server-side for your role"
    >
      <EyeOffIcon className="size-3" aria-hidden />
      redacted ({length.toLocaleString()} chars)
    </span>
  );
}
