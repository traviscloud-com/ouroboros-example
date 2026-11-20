import { cn } from "cn";
import type { OutcomeVerdict, ProposalStatus, RiskClass } from "@/api/types";
import { Badge } from "@/components/ui/badge";

/* Risk and status carry an explicit label; colour (reserved status tokens) is a secondary cue. */
export function RiskBadge({ risk }: { risk: RiskClass }) {
  return (
    <Badge
      variant="outline"
      className={cn(risk === "high" && "text-status-crit", risk === "medium" && "text-status-warn", risk === "low" && "text-status-good")}
    >
      {risk} risk
    </Badge>
  );
}

export function ProposalStatusBadge({ status }: { status: ProposalStatus }) {
  if (status === "failed") return <Badge variant="outline" className="text-status-crit">failed</Badge>;
  const variant = status === "proposed" ? "secondary" : status === "rejected" || status === "superseded" ? "outline" : "default";
  return <Badge variant={variant}>{status === "pr_opened" ? "PR opened" : status}</Badge>;
}

export function VerdictBadge({ verdict }: { verdict: OutcomeVerdict }) {
  return (
    <Badge
      variant="outline"
      className={cn(verdict === "improved" && "text-status-good", verdict === "worse" && "text-status-crit")}
    >
      {verdict.replace("_", " ")}
    </Badge>
  );
}
