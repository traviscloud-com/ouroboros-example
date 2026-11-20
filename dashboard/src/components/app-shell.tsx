import {
  ActivityIcon,
  BoxesIcon,
  DownloadIcon,
  LayoutDashboardIcon,
  LightbulbIcon,
  ListTreeIcon,
  SearchIcon,
  SettingsIcon,
  TriangleAlertIcon,
  UsersIcon,
} from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import type { Role } from "@/api/types";
import { useMe } from "@/components/me-provider";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Separator } from "@/components/ui/separator";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { useFilters } from "@/lib/use-filters";
import { ThemeToggle } from "./theme-toggle";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboardIcon;
  group: string;
  /** Minimum role that can use the page (D4/D12); leadership sees redacted detail pages. */
  minRole: Role;
  /** Carry the URL filters along when navigating. */
  keepFilters?: boolean;
}

export const NAV: NavItem[] = [
  { to: "/overview", label: "Overview", icon: LayoutDashboardIcon, group: "Leadership", minRole: "leadership", keepFilters: true },
  { to: "/engineering/artifacts", label: "Artifacts", icon: BoxesIcon, group: "Engineering", minRole: "leadership", keepFilters: true },
  { to: "/engineering/failures", label: "Failures", icon: TriangleAlertIcon, group: "Engineering", minRole: "leadership", keepFilters: true },
  { to: "/engineering/sessions", label: "Sessions", icon: ListTreeIcon, group: "Engineering", minRole: "leadership", keepFilters: true },
  { to: "/engineering/inspector", label: "Inspector", icon: SearchIcon, group: "Engineering", minRole: "leadership", keepFilters: true },
  { to: "/engineering/export", label: "Export", icon: DownloadIcon, group: "Engineering", minRole: "engineer", keepFilters: true },
  { to: "/proposals", label: "Proposals", icon: LightbulbIcon, group: "Improvement loop", minRole: "leadership" },
  { to: "/settings/general", label: "General", icon: SettingsIcon, group: "Settings", minRole: "leadership" },
  { to: "/settings/users", label: "Users", icon: UsersIcon, group: "Settings", minRole: "admin" },
];

const RANK: Record<Role, number> = { leadership: 0, engineer: 1, admin: 2 };

export function navFor(role: Role | undefined): NavItem[] {
  if (!role) return [];
  return NAV.filter((n) => RANK[role] >= RANK[n.minRole]);
}

const GROUP_ORDER = ["Leadership", "Engineering", "Improvement loop", "Settings"];

export function AppShell() {
  const { pathname } = useLocation();
  const { me } = useMe();
  const { search } = useFilters();
  const items = navFor(me?.role);
  const current = NAV.find((n) => pathname === n.to || pathname.startsWith(`${n.to}/`));
  const landing = me?.landing ?? "/overview";

  return (
    <SidebarProvider>
      <Sidebar variant="inset" collapsible="icon">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" render={<Link to={landing} />}>
                <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary font-semibold text-primary-foreground">
                  O
                </div>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-semibold">Ouroboros</span>
                  <span className="truncate text-xs text-muted-foreground">Claude Code analytics</span>
                </div>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          {GROUP_ORDER.filter((g) => items.some((n) => n.group === g)).map((group) => (
            <SidebarGroup key={group}>
              <SidebarGroupLabel>{group}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {items
                    .filter((n) => n.group === group)
                    .map((item) => (
                      <SidebarMenuItem key={item.to}>
                        <SidebarMenuButton
                          isActive={current?.to === item.to}
                          tooltip={item.label}
                          render={<NavLink to={item.keepFilters ? `${item.to}${search}` : item.to} />}
                        >
                          <item.icon />
                          <span>{item.label}</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          ))}
        </SidebarContent>
        <SidebarFooter>
          {me && (
            <div className="flex items-center gap-2 px-2 py-1 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
              <ActivityIcon className="size-3.5 shrink-0" aria-hidden />
              <span className="truncate" title={me.email}>
                {me.email}
              </span>
              <span className="ml-auto rounded border px-1 capitalize">{me.role}</span>
            </div>
          )}
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 data-[orientation=vertical]:h-4" />
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem className="hidden md:block">
                <BreadcrumbLink render={<Link to={landing} />}>Ouroboros</BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator className="hidden md:block" />
              {current && current.group !== "Leadership" && (
                <>
                  <BreadcrumbItem className="hidden md:block">
                    <span className="text-muted-foreground">{current.group}</span>
                  </BreadcrumbItem>
                  <BreadcrumbSeparator className="hidden md:block" />
                </>
              )}
              <BreadcrumbItem>
                <BreadcrumbPage>{current?.label ?? "Ouroboros"}</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
          </div>
        </header>
        <div className="flex flex-1 flex-col gap-4 p-4 md:p-6">
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
