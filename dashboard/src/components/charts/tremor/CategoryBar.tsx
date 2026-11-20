// Tremor CategoryBar [v0.0.3] — vendored from tremorlabs/tremor (Apache-2.0), see NOTICE.
// Ported to React 19 (ref as prop); Tremor's chartColors class map replaced by CSS colour
// values (segments get `style.background`), shadcn tooltip instead of Tremor's Tooltip.

import * as React from "react";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const DEFAULT_COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

const getMarkerColor = (marker: number | undefined, values: number[], colors: string[]): string => {
  if (marker === undefined) return "";
  if (marker === 0) {
    for (let index = 0; index < values.length; index++) {
      if (values[index] > 0) return colors[index];
    }
  }
  let prefixSum = 0;
  for (let index = 0; index < values.length; index++) {
    prefixSum += values[index];
    if (prefixSum >= marker) return colors[index];
  }
  return colors[values.length - 1];
};

const getPositionLeft = (value: number | undefined, maxValue: number): number =>
  value && maxValue ? (value / maxValue) * 100 : 0;

const sumNumericArray = (arr: number[]) => arr.reduce((prefixSum, num) => prefixSum + num, 0);

const formatNumber = (num: number): string => (Number.isInteger(num) ? num.toString() : num.toFixed(1));

const BarLabels = ({ values, formatter = formatNumber }: { values: number[]; formatter?: (n: number) => string }) => {
  const sumValues = React.useMemo(() => sumNumericArray(values), [values]);
  let prefixSum = 0;
  let sumConsecutiveHiddenLabels = 0;

  return (
    <div className="relative mb-2 flex h-5 w-full text-sm font-medium text-muted-foreground">
      <div className="absolute bottom-0 left-0 flex items-center">0</div>
      {values.map((widthPercentage, index) => {
        prefixSum += widthPercentage;
        const showLabel =
          (widthPercentage >= 0.1 * sumValues || sumConsecutiveHiddenLabels >= 0.09 * sumValues) &&
          sumValues - prefixSum >= 0.1 * sumValues &&
          prefixSum >= 0.1 * sumValues &&
          prefixSum < 0.9 * sumValues;
        sumConsecutiveHiddenLabels = showLabel ? 0 : (sumConsecutiveHiddenLabels += widthPercentage);
        const widthPositionLeft = getPositionLeft(widthPercentage, sumValues);
        return (
          <div
            key={`item-${index}`}
            className="flex items-center justify-end pr-0.5"
            style={{ width: `${widthPositionLeft}%` }}
          >
            {showLabel ? <span className="block translate-x-1/2 text-sm tabular-nums">{formatter(prefixSum)}</span> : null}
          </div>
        );
      })}
      <div className="absolute bottom-0 right-0 flex items-center">{formatter(sumValues)}</div>
    </div>
  );
};

interface CategoryBarProps extends React.HTMLAttributes<HTMLDivElement> {
  values: number[];
  /** CSS colour values per segment, in fixed order (defaults to --chart-1..5). */
  colors?: string[];
  marker?: { value: number; tooltip?: string; showAnimation?: boolean };
  showLabels?: boolean;
  labelFormatter?: (n: number) => string;
  ref?: React.Ref<HTMLDivElement>;
}

function CategoryBar({
  values = [],
  colors = DEFAULT_COLORS,
  marker,
  showLabels = true,
  labelFormatter,
  className,
  ref,
  ...props
}: CategoryBarProps) {
  const markerColor = React.useMemo(() => getMarkerColor(marker?.value, values, colors), [marker, values, colors]);
  const maxValue = React.useMemo(() => sumNumericArray(values), [values]);
  const adjustedMarkerValue = React.useMemo(() => {
    if (marker === undefined) return undefined;
    if (marker.value < 0) return 0;
    if (marker.value > maxValue) return maxValue;
    return marker.value;
  }, [marker, maxValue]);
  const markerPositionLeft = React.useMemo(
    () => getPositionLeft(adjustedMarkerValue, maxValue),
    [adjustedMarkerValue, maxValue],
  );

  const markerDot = (
    <div
      aria-hidden="true"
      className="relative mx-auto h-4 w-1 rounded-full ring-2 ring-background"
      style={{ background: markerColor }}
    >
      <div aria-hidden className="absolute size-7 -translate-x-[45%] -translate-y-[15%]" />
    </div>
  );

  return (
    <div ref={ref} className={cn(className)} aria-label="Category bar" aria-valuenow={marker?.value} {...props}>
      {showLabels ? <BarLabels values={values} formatter={labelFormatter} /> : null}
      <div className="relative flex h-2 w-full items-center">
        <div className="flex h-full flex-1 items-center gap-0.5 overflow-hidden rounded-full">
          {values.map((value, index) => {
            const percentage = maxValue ? (value / maxValue) * 100 : 0;
            return (
              <div
                key={`item-${index}`}
                className={cn("h-full", percentage === 0 && "hidden")}
                style={{ width: `${percentage}%`, background: colors[index] ?? "var(--muted-foreground)" }}
              />
            );
          })}
        </div>
        {marker !== undefined ? (
          <div
            className={cn(
              "absolute w-2 -translate-x-1/2",
              marker.showAnimation && "transform-gpu transition-all duration-300 ease-in-out",
            )}
            style={{ left: `${markerPositionLeft}%` }}
          >
            {marker.tooltip ? (
              <Tooltip>
                <TooltipTrigger render={markerDot} />
                <TooltipContent>{marker.tooltip}</TooltipContent>
              </Tooltip>
            ) : (
              markerDot
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export { CategoryBar, type CategoryBarProps };
