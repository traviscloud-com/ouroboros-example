import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

function Shell({ code, title, children }: { code: string; title: string; children: React.ReactNode }) {
  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-6 text-foreground">
      <div className="max-w-md text-center">
        <p className="text-sm font-medium text-muted-foreground">{code}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">{title}</h1>
        <div className="mt-3 text-sm text-muted-foreground">{children}</div>
      </div>
    </main>
  );
}

/** 401: the reporting service did not see a Gateway identity (D12). */
export function UnauthenticatedPage() {
  return (
    <Shell code="401" title="Not signed in">
      <p>
        No identity reached the dashboard. Open it through the platform hostname so the Gateway sign-in
        (oauth2-proxy) can forward your account; direct in-cluster or local calls are rejected.
      </p>
      <Button className="mt-4" render={<a href="/" />}>
        Reload
      </Button>
    </Shell>
  );
}

/** 403: signed in but no `principals` row (reporting-auth spec: request-access page). */
export function ForbiddenPage({ detail }: { detail?: string }) {
  return (
    <Shell code="403" title="Access not yet granted">
      <p>
        You are signed in, but no role is assigned to this account{detail ? ` (${detail})` : ""}. Ask an
        Ouroboros admin to add you under Settings → Users as leadership, engineer or admin.
      </p>
    </Shell>
  );
}

export function RoleRequiredPage({ minimum }: { minimum: string }) {
  return (
    <Shell code="403" title="This page needs a higher role">
      <p>Your role cannot open this page; it requires {minimum} or higher.</p>
      <Button variant="outline" className="mt-4" render={<Link to="/" />}>
        Back to your landing page
      </Button>
    </Shell>
  );
}

export function ServiceErrorPage({ detail }: { detail: string }) {
  return (
    <Shell code="Error" title="The reporting service is unavailable">
      <p className="break-all">{detail}</p>
      <Button className="mt-4" render={<a href="/" />}>
        Retry
      </Button>
    </Shell>
  );
}

export function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <p className="text-sm font-medium text-muted-foreground">404</p>
      <h1 className="mt-1 text-xl font-semibold">Nothing here</h1>
      <Button variant="outline" className="mt-4" render={<Link to="/" />}>
        Back to your landing page
      </Button>
    </div>
  );
}
