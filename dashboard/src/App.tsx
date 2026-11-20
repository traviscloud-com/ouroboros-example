import { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { Role } from "@/api/types";
import { AppShell } from "@/components/app-shell";
import {
  ForbiddenPage,
  NotFoundPage,
  RoleRequiredPage,
  ServiceErrorPage,
  UnauthenticatedPage,
} from "@/components/error-pages";
import { atLeast, useMe } from "@/components/me-provider";
import { Skeleton } from "@/components/ui/skeleton";

/* Route modules are lazy (task 4.14): Recharts loads with the first chart page,
   react-day-picker with the first calendar open, TanStack table with the first table. */
const OverviewPage = lazy(() => import("@/pages/overview"));
const ArtifactsPage = lazy(() => import("@/pages/engineering/artifacts"));
const ArtifactDetailPage = lazy(() => import("@/pages/engineering/artifact-detail"));
const FailuresPage = lazy(() => import("@/pages/engineering/failures"));
const SessionsPage = lazy(() => import("@/pages/engineering/sessions"));
const SessionDetailPage = lazy(() => import("@/pages/engineering/session-detail"));
const InspectorPage = lazy(() => import("@/pages/engineering/inspector"));
const ExportPage = lazy(() => import("@/pages/engineering/export"));
const ProposalsPage = lazy(() => import("@/pages/proposals"));
const ProposalDetailPage = lazy(() => import("@/pages/proposals/detail"));
const UsersPage = lazy(() => import("@/pages/settings/users"));
const GeneralSettingsPage = lazy(() => import("@/pages/settings/general"));

function PageFallback() {
  return (
    <div className="flex flex-col gap-4" aria-busy>
      <Skeleton className="h-8 w-48" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
      <Skeleton className="h-72" />
    </div>
  );
}

function RequireRole({ minimum, children }: { minimum: Role; children: React.ReactNode }) {
  const { me } = useMe();
  if (!atLeast(me?.role, minimum)) return <RoleRequiredPage minimum={minimum} />;
  return <>{children}</>;
}

/** `/` → the server-provided landing (D4), keeping any filter search params. */
function Landing() {
  const { me } = useMe();
  const { search } = useLocation();
  return <Navigate to={`${me?.landing ?? "/overview"}${search}`} replace />;
}

export default function App() {
  const state = useMe();
  if (state.status === "loading") return <PageFallback />;
  if (state.status === "unauthenticated") return <UnauthenticatedPage />;
  if (state.status === "forbidden") return <ForbiddenPage detail={state.detail} />;
  if (state.status === "error") return <ServiceErrorPage detail={state.detail} />;

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Landing />} />
        <Route
          path="/overview"
          element={
            <Suspense fallback={<PageFallback />}>
              <OverviewPage />
            </Suspense>
          }
        />
        <Route path="/engineering">
          <Route index element={<Navigate to="artifacts" replace />} />
          <Route path="artifacts" element={<Lazy><ArtifactsPage /></Lazy>} />
          <Route path="artifacts/:id" element={<Lazy><ArtifactDetailPage /></Lazy>} />
          <Route path="failures" element={<Lazy><FailuresPage /></Lazy>} />
          <Route path="sessions" element={<Lazy><SessionsPage /></Lazy>} />
          <Route path="sessions/:id" element={<Lazy><SessionDetailPage /></Lazy>} />
          <Route path="inspector" element={<Lazy><InspectorPage /></Lazy>} />
          <Route
            path="export"
            element={
              <RequireRole minimum="engineer">
                <Lazy><ExportPage /></Lazy>
              </RequireRole>
            }
          />
        </Route>
        <Route path="/proposals" element={<Lazy><ProposalsPage /></Lazy>} />
        <Route path="/proposals/:id" element={<Lazy><ProposalDetailPage /></Lazy>} />
        <Route path="/settings">
          <Route index element={<Navigate to="general" replace />} />
          <Route path="general" element={<Lazy><GeneralSettingsPage /></Lazy>} />
          <Route
            path="users"
            element={
              <RequireRole minimum="admin">
                <Lazy><UsersPage /></Lazy>
              </RequireRole>
            }
          />
        </Route>
        {/* Pre-Phase-4 paths */}
        <Route path="/reports" element={<Navigate to="/overview" replace />} />
        <Route path="/sentiment" element={<Navigate to="/overview" replace />} />
        <Route path="/search" element={<LegacySearchRedirect />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

function Lazy({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<PageFallback />}>{children}</Suspense>;
}

function LegacySearchRedirect() {
  const { search } = useLocation();
  const session = new URLSearchParams(search).get("session");
  return <Navigate to={session ? `/engineering/sessions/${encodeURIComponent(session)}` : "/engineering/inspector"} replace />;
}
