import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { ChartConfig, ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { fmtDay } from "@/lib/period";
import { slotColor, type StackedRow } from "@/lib/series";

interface StackedBarsProps {
  data: StackedRow[];
  keys: string[];
  /** Colour per key; defaults to the categorical slots in fixed order ("Other" muted). */
  colors?: Record<string, string>;
  labels?: Record<string, string>;
  format?: (n: number) => string;
  height?: number;
}

/** Stacked bars per bucket: 2px surface gap between segments, rounded top segment only. */
export function StackedBars({ data, keys, colors, labels, format, height = 240 }: StackedBarsProps) {
  const config = Object.fromEntries(
    keys.map((k, i) => [k, { label: labels?.[k] ?? k, color: colors?.[k] ?? slotColor(i, k) }]),
  ) satisfies ChartConfig;
  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
      <BarChart data={data} accessibilityLayer margin={{ left: 0, right: 12, top: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={8} minTickGap={32} tickFormatter={fmtDay} />
        <YAxis tickLine={false} axisLine={false} width={44} tickFormatter={format} allowDecimals={false} />
        <ChartTooltip
          cursor={{ fill: "var(--muted)", opacity: 0.5 }}
          content={
            <ChartTooltipContent
              labelFormatter={(label) => fmtDay(String(label))}
              formatter={(value, name, item) => (
                <div className="flex w-full items-center justify-between gap-3">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <span className="size-2 shrink-0 rounded-[2px]" style={{ background: item.color }} aria-hidden />
                    {config[name as string]?.label ?? name}
                  </span>
                  <span className="font-medium tabular-nums">{format ? format(Number(value ?? 0)) : Number(value ?? 0).toLocaleString()}</span>
                </div>
              )}
            />
          }
        />
        {keys.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {keys.map((k, i) => (
          <Bar
            key={k}
            dataKey={k}
            stackId="s"
            fill={config[k].color}
            stroke="var(--background)"
            strokeWidth={2}
            radius={i === keys.length - 1 ? [4, 4, 0, 0] : 0}
            maxBarSize={28}
          />
        ))}
      </BarChart>
    </ChartContainer>
  );
}
