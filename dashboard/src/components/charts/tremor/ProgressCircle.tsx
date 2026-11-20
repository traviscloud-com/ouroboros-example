// Tremor ProgressCircle [v0.0.3] — vendored from tremorlabs/tremor (Apache-2.0), see NOTICE.
// Ported to React 19 (ref as prop); tailwind-variants replaced by the ProgressBar token map.

import * as React from "react";
import { cn } from "cn";
import { PROGRESS_COLORS, type ProgressVariant } from "./ProgressBar";

interface ProgressCircleProps extends Omit<React.SVGProps<SVGSVGElement>, "value" | "ref"> {
  value?: number;
  max?: number;
  showAnimation?: boolean;
  radius?: number;
  strokeWidth?: number;
  variant?: ProgressVariant;
  children?: React.ReactNode;
  ref?: React.Ref<SVGSVGElement>;
}

function ProgressCircle({
  value = 0,
  max = 100,
  radius = 32,
  strokeWidth = 6,
  showAnimation = true,
  variant = "default",
  className,
  children,
  ref,
  ...props
}: ProgressCircleProps) {
  const safeValue = Math.min(max, Math.max(value, 0));
  const normalizedRadius = radius - strokeWidth / 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const offset = circumference - (safeValue / max) * circumference;
  const color = PROGRESS_COLORS[variant];

  return (
    <div
      className="relative"
      role="progressbar"
      aria-label="Progress circle"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      data-max={max}
      data-value={safeValue ?? null}
    >
      <svg
        ref={ref}
        width={radius * 2}
        height={radius * 2}
        viewBox={`0 0 ${radius * 2} ${radius * 2}`}
        className={cn("-rotate-90 transform", className)}
        {...props}
      >
        <circle
          r={normalizedRadius}
          cx={radius}
          cy={radius}
          strokeWidth={strokeWidth}
          fill="transparent"
          stroke={`color-mix(in oklab, ${color} 25%, transparent)`}
          strokeLinecap="round"
          className="transition-colors ease-linear"
        />
        {safeValue >= 0 ? (
          <circle
            r={normalizedRadius}
            cx={radius}
            cy={radius}
            strokeWidth={strokeWidth}
            strokeDasharray={`${circumference} ${circumference}`}
            strokeDashoffset={offset}
            fill="transparent"
            stroke={color}
            strokeLinecap="round"
            className={cn(
              "transition-colors ease-linear",
              showAnimation && "transform-gpu transition-all duration-300 ease-in-out",
            )}
          />
        ) : null}
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">{children}</div>
    </div>
  );
}

export { ProgressCircle, type ProgressCircleProps };
