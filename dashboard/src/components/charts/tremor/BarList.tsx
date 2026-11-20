// Tremor BarList [v1.0.0] — vendored from tremorlabs/tremor (Apache-2.0), see NOTICE.
// Ported to React 19 (ref as prop) and shadcn/ui theme tokens.

import * as React from "react";
import { cn } from "cn";

type Bar<T> = T & {
  key?: string;
  href?: string;
  value: number;
  name: string;
};

interface BarListProps<T = unknown> extends React.HTMLAttributes<HTMLDivElement> {
  data: Bar<T>[];
  valueFormatter?: (value: number) => string;
  showAnimation?: boolean;
  onValueChange?: (payload: Bar<T>) => void;
  sortOrder?: "ascending" | "descending" | "none";
  ref?: React.Ref<HTMLDivElement>;
}

const focusRing = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";

function BarList<T>({
  data = [],
  valueFormatter = (value) => value.toString(),
  showAnimation = false,
  onValueChange,
  sortOrder = "descending",
  className,
  ref,
  ...props
}: BarListProps<T>) {
  const Component = onValueChange ? "button" : "div";
  const sortedData = React.useMemo(() => {
    if (sortOrder === "none") return data;
    return [...data].sort((a, b) => (sortOrder === "ascending" ? a.value - b.value : b.value - a.value));
  }, [data, sortOrder]);

  const widths = React.useMemo(() => {
    const maxValue = Math.max(...sortedData.map((item) => item.value), 0);
    return sortedData.map((item) => (item.value === 0 ? 0 : Math.max((item.value / maxValue) * 100, 2)));
  }, [sortedData]);

  const rowHeight = "h-8";

  return (
    <div
      ref={ref}
      className={cn("flex justify-between space-x-6", className)}
      aria-sort={sortOrder}
      {...props}
    >
      <div className="relative w-full space-y-1.5">
        {sortedData.map((item, index) => (
          <Component
            key={item.key ?? item.name}
            onClick={() => onValueChange?.(item)}
            className={cn(
              "group w-full rounded-sm",
              focusRing,
              onValueChange ? "-m-0! cursor-pointer hover:bg-muted" : "",
            )}
          >
            <div
              className={cn(
                "flex items-center rounded-sm transition-all",
                rowHeight,
                "bg-chart-1/25 dark:bg-chart-1/35",
                onValueChange ? "group-hover:bg-chart-1/40" : "",
                {
                  "mb-0": index === sortedData.length - 1,
                  "duration-800": showAnimation,
                },
              )}
              style={{ width: `${widths[index]}%` }}
            >
              <div className="absolute left-2 flex max-w-full pr-2">
                {item.href ? (
                  <a
                    href={item.href}
                    className={cn(
                      "truncate rounded-sm text-sm whitespace-nowrap text-foreground",
                      "hover:underline hover:underline-offset-2",
                      focusRing,
                    )}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {item.name}
                  </a>
                ) : (
                  <p className="truncate text-sm whitespace-nowrap text-foreground">{item.name}</p>
                )}
              </div>
            </div>
          </Component>
        ))}
      </div>
      <div>
        {sortedData.map((item, index) => (
          <div
            key={item.key ?? item.name}
            className={cn(
              "flex items-center justify-end",
              rowHeight,
              index === sortedData.length - 1 ? "mb-0" : "mb-1.5",
            )}
          >
            <p className="truncate text-sm leading-none whitespace-nowrap text-foreground tabular-nums">
              {valueFormatter(item.value)}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

BarList.displayName = "BarList";

export { BarList, type BarListProps };
