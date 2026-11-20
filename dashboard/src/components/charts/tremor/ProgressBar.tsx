// Tremor ProgressBar [v0.0.3] — vendored from tremorlabs/tremor (Apache-2.0), see NOTICE.
// Ported to React 19 (ref as prop); tailwind-variants replaced by a token map so the
// variants use the shadcn chart / reserved status tokens.

import * as React from "react";
import { cn } from "cn";

export type ProgressVariant = "default" | "neutral" | "warning" | "error" | "success";

/** Variant → CSS colour token (bar) ; the track is the same colour at low alpha. */
export const PROGRESS_COLORS: Record<ProgressVariant, string> = {
  default: "var(--chart-1)",
  neutral: "var(--muted-foreground)",
  warning: "var(--status-warn)",
  error: "var(--status-crit)",
  success: "var(--status-good)",
};

interface ProgressBarProps extends React.HTMLAttributes<HTMLDivElement> {
  value?: number;
  max?: number;
  showAnimation?: boolean;
  label?: string;
  variant?: ProgressVariant;
  ref?: React.Ref<HTMLDivElement>;
}

function ProgressBar({
  value = 0,
  max = 100,
  label,
  showAnimation = false,
  variant = "default",
  className,
  ref,
  ...props
}: ProgressBarProps) {
  const safeValue = Math.min(max, Math.max(value, 0));
  const color = PROGRESS_COLORS[variant];
  return (
    <div
      ref={ref}
      className={cn("flex w-full items-center", className)}
      role="progressbar"
      aria-label="Progress bar"
      aria-valuenow={value}
      aria-valuemax={max}
      {...props}
    >
      <div
        className="relative flex h-2 w-full items-center rounded-full"
        style={{ background: `color-mix(in oklab, ${color} 25%, transparent)` }}
      >
        <div
          className={cn(
            "h-full flex-col rounded-full",
            showAnimation && "transform-gpu transition-all duration-300 ease-in-out",
          )}
          style={{ width: max ? `${(safeValue / max) * 100}%` : `${safeValue}%`, background: color }}
        />
      </div>
      {label ? (
        <span className="ml-2 whitespace-nowrap text-sm font-medium leading-none text-foreground tabular-nums">
          {label}
        </span>
      ) : null}
    </div>
  );
}

export { ProgressBar, type ProgressBarProps };
