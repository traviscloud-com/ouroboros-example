// Tremor Tracker [v1.0.0] — vendored from tremorlabs/tremor (Apache-2.0), see NOTICE.
// Ported to React 19 (ref as prop), shadcn/ui tooltip (Base UI) instead of Radix HoverCard,
// and theme tokens; block colours are CSS colour values (e.g. `var(--status-good)`).

import * as React from "react";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface TrackerBlockProps {
  key?: string | number;
  /** CSS colour value for the block; falls back to `defaultColor`. */
  color?: string;
  tooltip?: string;
  hoverEffect?: boolean;
  defaultColor?: string;
}

const Block = ({ color, tooltip, defaultColor, hoverEffect }: TrackerBlockProps) => {
  const inner = (
    <div
      className={cn("size-full rounded-[1px]", hoverEffect ? "hover:opacity-50" : "")}
      style={{ background: color || defaultColor }}
    />
  );
  const cell = (
    <div className="size-full overflow-hidden px-[0.5px] transition first:rounded-l-[4px] first:pl-0 last:rounded-r-[4px] last:pr-0 sm:px-px">
      {inner}
    </div>
  );
  if (!tooltip) return cell;
  return (
    <Tooltip>
      <TooltipTrigger render={cell} />
      <TooltipContent side="top">{tooltip}</TooltipContent>
    </Tooltip>
  );
};

interface TrackerProps extends React.HTMLAttributes<HTMLDivElement> {
  data: TrackerBlockProps[];
  defaultColor?: string;
  hoverEffect?: boolean;
  ref?: React.Ref<HTMLDivElement>;
}

function Tracker({
  data = [],
  defaultColor = "var(--muted-foreground)",
  className,
  hoverEffect,
  ref,
  ...props
}: TrackerProps) {
  return (
    <div ref={ref} className={cn("group flex h-8 w-full items-center", className)} {...props}>
      {data.map((block, index) => (
        <Block key={block.key ?? index} defaultColor={defaultColor} hoverEffect={hoverEffect} {...block} />
      ))}
    </div>
  );
}

export { Tracker, type TrackerBlockProps };
