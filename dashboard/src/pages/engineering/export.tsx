import { DownloadIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api";
import { EXPORT_DATASETS, type ExportDataset, type ExportFormat } from "@/api/types";
import { useMe } from "@/components/me-provider";
import { Filterbar } from "@/components/filterbar";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { fmtRange, rangeDays } from "@/lib/period";
import { useFilters } from "@/lib/use-filters";

const DATASET_HELP: Record<ExportDataset, string> = {
  sessions: "One row per Claude Code session (title is sensitive).",
  turns: "User prompts and assistant responses per turn (sensitive text).",
  tool_calls: "Tool invocations with duration, success and captured content (sensitive).",
  usage_facts: "Token and cost facts per API request, with skill / agent attribution.",
  events: "Skill outcome events with feedback and interaction text (sensitive).",
  artifact_versions: "Artifact registry versions with content text and token estimates.",
};
const DATASET_ITEMS = EXPORT_DATASETS.map((d) => ({ value: d, label: d }));
const FORMAT_ITEMS = [
  { value: "ndjson", label: "NDJSON" },
  { value: "parquet", label: "Parquet" },
];

export default function ExportPage() {
  const f = useFilters();
  const { me } = useMe();
  const [dataset, setDataset] = useState<ExportDataset>("events");
  const [format, setFormat] = useState<ExportFormat>("ndjson");
  const [redact, setRedact] = useState(false);
  const url = api.export.url(dataset, { from: f.range.from, to: f.range.to, format, redact });

  const [busy, setBusy] = useState(false);

  /** One request per export (the server audits each one): stream into a Blob, then save it.
   * Errors (403 for leadership, 501 without pyarrow) surface as a toast, not a broken file. */
  const download = async () => {
    setBusy(true);
    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => ({}))) as { detail?: string };
        toast.error(`Export refused (HTTP ${resp.status})`, { description: detail.detail });
        return;
      }
      const blob = await resp.blob();
      const disposition = resp.headers.get("Content-Disposition") ?? "";
      const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? `${dataset}.${format === "parquet" ? "parquet" : "ndjson"}`;
      const total = resp.headers.get("X-Export-Total-Rows");
      const truncated = resp.headers.get("X-Export-Truncated") === "true";
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
      toast.success(`Exported ${dataset}`, {
        description: `${total ?? "?"} rows${truncated ? ` (capped at ${resp.headers.get("X-Export-Row-Cap")})` : ""} · logged to audit_log`,
      });
    } catch (e) {
      toast.error("Export failed", { description: String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Export" description="Download a modeling dataset for the selected event-time range. Exports stream from the reporting service and are capped at EXPORT_MAX_ROWS.">
        <Filterbar range={f.range} onRange={f.setRange} />
      </PageHeader>

      <Alert role="note">
        <AlertTitle>Every export is audited</AlertTitle>
        <AlertDescription>
          The reporting service writes an <code>audit_log</code> row with your identity ({me?.email}), the dataset, the range and whether text was redacted. Verbatim text leaves the platform only through this page; share redacted files unless the recipient is cleared for prompt content.
        </AlertDescription>
      </Alert>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Dataset</CardTitle>
          <CardDescription>
            {fmtRange(f.range)} ({rangeDays(f.range)} days, UTC, event time)
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <div className="grid gap-2">
            <Label htmlFor="dataset">Dataset</Label>
            <Select value={dataset} onValueChange={(v) => v && setDataset(v as ExportDataset)} items={DATASET_ITEMS}>
              <SelectTrigger id="dataset" className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATASET_ITEMS.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">{DATASET_HELP[dataset]}</p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="format">Format</Label>
            <Select value={format} onValueChange={(v) => v && setFormat(v as ExportFormat)} items={FORMAT_ITEMS}>
              <SelectTrigger id="format" className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FORMAT_ITEMS.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {format === "parquet" && <p className="text-sm text-muted-foreground">Parquet needs the reporting image's <code>parquet</code> extra (pyarrow); otherwise the server answers 501.</p>}
          </div>
          <div className="flex items-center gap-3">
            <Switch id="redact" checked={redact} onCheckedChange={(v) => setRedact(Boolean(v))} />
            <Label htmlFor="redact">Redact sensitive text (shareable file)</Label>
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={download} disabled={busy}>
              <DownloadIcon /> Download {dataset}.{format}
            </Button>
            <code className="truncate text-xs text-muted-foreground">{url}</code>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
