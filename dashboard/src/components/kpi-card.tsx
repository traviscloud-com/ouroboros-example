import { MinusIcon, TrendingDownIcon, TrendingUpIcon } from "lucide-react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Card, CardAction, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtInt } from "@/lib/format";
import { pctChange } from "@/lib/period";

interface KpiCardProps {
  label: string;
  value: number | null | undefined;
  /** Previous-period value; omit (undefined) when there is no comparison. */
  previous?: number | null;
  format?: (n: number | null | undefined) => string;
  /** "relative" = % change of the value; "points" = difference of two rates in percentage points. */
  deltaMode?: "relative" | "points";
  /** When true, a decrease is the good direction (e.g. failure rate). */
  invert?: boolean;
  hint?: string;
  /** Dimmed while refetching (the previous value stays visible). */
  loading?: boolean;
}

/** KPI card (stat tile) with period-over-period delta, after shadcn's dashboard-01
 * `section-cards`. Delta text wears text tokens; the badge carries the status colour
 * together with a direction icon, never colour alone. */
export function KpiCard({
  label,
  value,
  previous,
  format = fmtInt,
  deltaMode = "relative",
  invert = false,
  hint,
  loading,
}: KpiCardProps) {
  const hasComparison = previous !== undefined && value != null;
  const delta = !hasComparison
    ? null
    : previous == null
      ? null
      : deltaMode === "relative"
        ? pctChange(value, previous)
        : value - previous;
  const direction = delta === null || Math.abs(delta) < 0.0005 ? 0 : delta > 0 ? 1 : -1;
  const good = direction === 0 ? null : invert ? direction < 0 : direction > 0;
  const Icon = direction > 0 ? TrendingUpIcon : direction < 0 ? TrendingDownIcon : MinusIcon;
  const deltaText =
    delta === null
      ? "new"
      : deltaMode === "relative"
        ? `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(Math.abs(delta) < 0.1 ? 1 : 0)}%`
        : `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(1)} pt`;

  return (
    <Card className={cn("@container/card transition-opacity", loading && "opacity-60")}>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl font-semibold @[250px]/card:text-3xl">{format(value)}</CardTitle>
        {hasComparison && (
          <CardAction>
            <Badge
              variant="outline"
              className={cn("gap-1 tabular-nums", good === true && "text-status-good", good === false && "text-status-crit")}
            >
              <Icon className="size-3.5" aria-hidden />
              <span className="sr-only">{good === true ? "improved" : good === false ? "worsened" : "unchanged"}</span>
              {deltaText}
            </Badge>
          </CardAction>
        )}
      </CardHeader>
      <CardFooter className="text-sm text-muted-foreground">
        {hint ?? (hasComparison ? `${format(previous)} in the previous period` : " ")}
      </CardFooter>
    </Card>
  );
}

export function KpiCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-4 w-24" />
        <Skeleton className="mt-2 h-8 w-20" />
      </CardHeader>
      <CardFooter>
        <Skeleton className="h-4 w-36" />
      </CardFooter>
    </Card>
  );
}

export function KpiRow({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("grid gap-4 sm:grid-cols-2 xl:grid-cols-4", className)}>{children}</div>;
}
