import { Area, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";
import { ChartConfig, ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { fmtDay } from "@/lib/period";
import type { AlignedPoint } from "@/lib/series";

interface TimeSeriesChartProps {
  data: AlignedPoint[];
  label: string;
  /** Series colour; the previous period is always `--chart-previous`, dashed. */
  color?: string;
  format?: (n: number) => string;
  height?: number;
  /** Fixed y-domain (e.g. sentiment −1..1). */
  domain?: [number, number];
  /** Draw the current series as a line instead of a filled area. */
  line?: boolean;
  id: string;
}

/** Current period as an area (or line), previous period as a dashed line aligned by bucket. */
export function TimeSeriesChart({ data, label, color = "var(--chart-1)", format, height = 240, domain, line, id }: TimeSeriesChartProps) {
  const hasPrevious = data.some((d) => d.previous !== null);
  const config = {
    current: { label, color },
    previous: { label: `${label} (previous)`, color: "var(--chart-previous)" },
  } satisfies ChartConfig;
  const gradient = `fill-${id}`;
  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height }}>
      <ComposedChart data={data} accessibilityLayer margin={{ left: 0, right: 12, top: 8 }}>
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--color-current)" stopOpacity={0.3} />
            <stop offset="95%" stopColor="var(--color-current)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={8} minTickGap={32} tickFormatter={fmtDay} />
        <YAxis tickLine={false} axisLine={false} width={44} domain={domain} tickFormatter={format} allowDecimals={!!domain} />
        <ChartTooltip
          cursor={{ stroke: "var(--border)" }}
          content={
            <ChartTooltipContent
              indicator="line"
              formatter={(value, name, item) => (
                <div className="flex w-full items-center justify-between gap-3">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <span className="size-2 shrink-0 rounded-[2px]" style={{ background: item.color }} aria-hidden />
                    {config[name as keyof typeof config]?.label ?? name}
                  </span>
                  <span className="font-medium tabular-nums">{value == null ? "—" : format ? format(Number(value)) : Number(value).toLocaleString()}</span>
                </div>
              )}
              labelFormatter={(_, payload) => {
                const p = payload?.[0]?.payload as AlignedPoint | undefined;
                if (!p) return "";
                return p.previousDay ? `${fmtDay(p.day)} · prev. ${fmtDay(p.previousDay)}` : fmtDay(p.day);
              }}
            />
          }
        />
        {hasPrevious && <ChartLegend content={<ChartLegendContent />} />}
        {line ? (
          <Line dataKey="current" type="monotone" stroke="var(--color-current)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls />
        ) : (
          <Area dataKey="current" type="monotone" fill={`url(#${gradient})`} stroke="var(--color-current)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
        )}
        {hasPrevious && (
          <Line dataKey="previous" type="monotone" stroke="var(--color-previous)" strokeWidth={2} strokeDasharray="4 4" dot={false} activeDot={{ r: 4 }} connectNulls />
        )}
      </ComposedChart>
    </ChartContainer>
  );
}
