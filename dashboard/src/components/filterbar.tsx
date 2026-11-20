import { CalendarIcon } from "lucide-react";
import { Suspense, lazy, useState } from "react";
import type { DateRange } from "react-day-picker";
import type { Bucket } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { DayRange, fmtRange, isoDay, lastNDays, previousRange, sameRange, utcDay } from "@/lib/period";
import type { Compare } from "@/lib/use-filters";

const RangeCalendar = lazy(() => import("./range-calendar"));

const PRESETS = [7, 14, 30, 90] as const;
const PRESET_ITEMS = [
  ...PRESETS.map((n) => ({ value: String(n), label: `Last ${n} days` })),
  { value: "custom", label: "Custom range" },
];
const COMPARE_ITEMS = [
  { value: "previous", label: "vs. previous period" },
  { value: "none", label: "No comparison" },
];

export interface DimensionFilter {
  key: "skill" | "agent" | "user";
  label: string;
  options: string[];
  value: string | null;
}

interface FilterbarProps {
  range: DayRange;
  onRange: (r: DayRange) => void;
  compare?: Compare;
  onCompare?: (c: Compare) => void;
  bucket?: Bucket;
  onBucket?: (b: Bucket) => void;
  /** Skill / agent / user selectors; options come from the page's data. */
  dimensions?: DimensionFilter[];
  onDimension?: (key: DimensionFilter["key"], value: string | null) => void;
}

/** One filter row above everything it scopes (D4): date range + preset, comparison,
 * bucket and dimension selectors, all written through to the URL by the caller. */
export function Filterbar({ range, onRange, compare, onCompare, bucket, onBucket, dimensions, onDimension }: FilterbarProps) {
  const [open, setOpen] = useState(false);
  const preset = PRESETS.find((n) => sameRange(range, lastNDays(n)));
  const prev = previousRange(range);

  // The calendar works in local dates; we treat the picked y/m/d as a UTC day.
  const selected: DateRange = { from: toLocal(range.from), to: toLocal(range.to) };

  const onSelect = (r: DateRange | undefined) => {
    if (!r?.from) return;
    const from = fromLocal(r.from);
    const to = r.to ? fromLocal(r.to) : from;
    onRange({ from, to });
    if (r.to) setOpen(false);
  };

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
      <Select
        value={preset ? String(preset) : "custom"}
        onValueChange={(v) => {
          if (v && v !== "custom") onRange(lastNDays(Number(v)));
        }}
        items={PRESET_ITEMS}
      >
        <SelectTrigger className="w-36" aria-label="Period preset">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {PRESET_ITEMS.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger render={<Button variant="outline" className="font-normal" />}>
          <CalendarIcon className="size-4 text-muted-foreground" />
          {fmtRange(range)}
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Suspense fallback={<Skeleton className="m-3 h-[280px] w-[520px] max-w-[80vw]" />}>
            <RangeCalendar selected={selected} onSelect={onSelect} />
          </Suspense>
        </PopoverContent>
      </Popover>

      {onCompare && (
        <Select value={compare ?? "previous"} onValueChange={(v) => v && onCompare(v as Compare)} items={COMPARE_ITEMS}>
          <SelectTrigger className="w-44" aria-label="Comparison period">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {COMPARE_ITEMS.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {onBucket && (
        <ToggleGroup
          value={[bucket ?? "day"]}
          onValueChange={(v) => {
            const next = v[0];
            if (next === "day" || next === "week") onBucket(next);
          }}
          variant="outline"
          aria-label="Bucket"
        >
          <ToggleGroupItem value="day">Day</ToggleGroupItem>
          <ToggleGroupItem value="week">Week</ToggleGroupItem>
        </ToggleGroup>
      )}

      {dimensions?.map((dim) => {
        const items = [{ value: "__all", label: `All ${dim.label.toLowerCase()}s` }, ...dim.options.map((o) => ({ value: o, label: o }))];
        return (
          <Select
            key={dim.key}
            value={dim.value ?? "__all"}
            onValueChange={(v) => onDimension?.(dim.key, v && v !== "__all" ? v : null)}
            items={items}
          >
            <SelectTrigger className="w-44" aria-label={dim.label}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {items.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );
      })}

      {compare !== "none" && <span className="text-sm text-muted-foreground">vs. {fmtRange(prev)}</span>}
    </div>
  );
}

function toLocal(day: string): Date {
  const d = utcDay(day);
  return new Date(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

function fromLocal(d: Date): string {
  return isoDay(new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())));
}
