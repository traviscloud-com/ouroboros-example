import { useState } from "react";
import { useMe } from "@/components/me-provider";
import { PageHeader } from "@/components/page-header";
import { ThemeMode, useThemeMode } from "@/components/theme-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DEFAULT_DAYS_KEY, defaultDays } from "@/lib/use-filters";

const THEME_ITEMS = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];
const DAYS_ITEMS = [7, 14, 30, 90].map((n) => ({ value: String(n), label: `Last ${n} days` }));

export default function GeneralSettingsPage() {
  const { mode, setMode } = useThemeMode();
  const { me } = useMe();
  const [days, setDays] = useState(() => String(defaultDays()));

  const saveDays = (v: string) => {
    setDays(v);
    try {
      localStorage.setItem(DEFAULT_DAYS_KEY, v);
    } catch {
      /* storage unavailable */
    }
  };

  return (
    <>
      <PageHeader title="General" description="Preferences stored in this browser; the server holds no per-user settings." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
            <CardDescription>Class-strategy dark mode; "System" follows the OS setting.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2">
            <Label htmlFor="theme">Theme</Label>
            <Select value={mode} onValueChange={(v) => v && setMode(v as ThemeMode)} items={THEME_ITEMS}>
              <SelectTrigger id="theme" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {THEME_ITEMS.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Defaults</CardTitle>
            <CardDescription>Applied when a page is opened without a range in the URL.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2">
            <Label htmlFor="days">Default range</Label>
            <Select value={days} onValueChange={(v) => v && saveDays(v)} items={DAYS_ITEMS}>
              <SelectTrigger id="days" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DAYS_ITEMS.map((i) => (
                  <SelectItem key={i.value} value={i.value}>
                    {i.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Your identity</CardTitle>
            <CardDescription>As resolved by the reporting service from the Gateway header.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm">
            <div>{me?.email}</div>
            <div className="mt-1 flex gap-2">
              <Badge>{me?.role}</Badge>
              <Badge variant="outline">via {me?.via}</Badge>
              <Badge variant="outline">lands on {me?.landing}</Badge>
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
