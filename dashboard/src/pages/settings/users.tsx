import { Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api";
import type { Role } from "@/api/types";
import { ErrorNotice } from "@/components/empty-state";
import { useMe } from "@/components/me-provider";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtDateTime } from "@/lib/format";
import { useQuery } from "@/lib/use-query";

const ROLE_ITEMS = [
  { value: "leadership", label: "leadership - aggregates and redacted detail" },
  { value: "engineer", label: "engineer - verbatim text, export, low-risk approvals" },
  { value: "admin", label: "admin - everything, principals, all approvals" },
];

export default function UsersPage() {
  const { me } = useMe();
  const { data, error, loading, refetch } = useQuery(() => api.settings.users(), []);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("engineer");
  const [busy, setBusy] = useState(false);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const row = await api.settings.addUser(email.trim().toLowerCase(), role);
      toast.success(`${row.email} is now ${row.role}`);
      setEmail("");
      refetch();
    } catch (err) {
      toast.error("Could not save principal", { description: String(err) });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (target: string) => {
    if (!window.confirm(`Remove ${target}? Their next request will get 403.`)) return;
    try {
      await api.settings.removeUser(target);
      toast.success(`${target} removed`);
      refetch();
    } catch (err) {
      toast.error("Could not remove principal", { description: String(err) });
    }
  };

  return (
    <>
      <PageHeader title="Users" description="Principals the reporting service knows. Google sign-in at the Gateway is the first gate; a row here is the authorization. Every change is written to audit_log." />
      {error && <ErrorNotice error={error} what="users" />}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Principals</CardTitle>
            <CardDescription>Bootstrap admins come from ADMIN_EMAILS and cannot be changed here (the server answers 409).</CardDescription>
          </CardHeader>
          <CardContent className={loading ? "opacity-60 transition-opacity" : ""}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Added</TableHead>
                  <TableHead>By</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.users.map((u) => {
                  const bootstrap = data.bootstrap_admins.includes(u.email);
                  return (
                    <TableRow key={u.email}>
                      <TableCell className="font-medium">
                        {u.email}
                        {u.email === me?.email && <span className="ml-2 text-xs text-muted-foreground">(you)</span>}
                      </TableCell>
                      <TableCell>
                        <Badge variant={u.role === "admin" ? "default" : "secondary"}>{u.role}</Badge>
                        {bootstrap && <Badge variant="outline" className="ml-1">bootstrap</Badge>}
                      </TableCell>
                      <TableCell className="text-xs">{fmtDateTime(u.created_at)}</TableCell>
                      <TableCell className="text-xs">{u.created_by}</TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon" disabled={bootstrap} aria-label={`Remove ${u.email}`} onClick={() => remove(u.email)}>
                          <Trash2Icon />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
                {data && data.users.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                      No principals yet.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Add or change a principal</CardTitle>
            <CardDescription>Posting an existing email updates its role.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="grid gap-4" onSubmit={add}>
              <div className="grid gap-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@example.com" />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="role">Role</Label>
                <Select value={role} onValueChange={(v) => v && setRole(v as Role)} items={ROLE_ITEMS}>
                  <SelectTrigger id="role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLE_ITEMS.map((i) => (
                      <SelectItem key={i.value} value={i.value}>
                        {i.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button type="submit" disabled={busy || !email}>
                Save
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
