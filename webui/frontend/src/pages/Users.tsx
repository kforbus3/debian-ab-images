import { useEffect, useState } from "react";
import { Users as UsersIcon, UserPlus, KeyRound, Trash2, Ban, CircleCheck, RefreshCw } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, Spinner, Badge, Modal } from "../components/ui";

type User = {
  username: string; role: string; disabled: boolean;
  created: number; last_login: number | null; source: string;
};

const ROLE_COLOR: Record<string, string> = { admin: "brand", operator: "blue", viewer: "zinc" };
const fmtWhen = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");

export default function Users() {
  const toast = useToast();
  const { username: me } = useAuth();
  const [users, setUsers] = useState<User[] | null>(null);
  const [roles, setRoles] = useState<string[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState("");
  const [newPassword, setNewPassword] = useState("");

  async function load() {
    try {
      const { data } = await api.get("/users");
      setUsers(data.users); setRoles(data.roles);
    } catch (e) { toast.error(apiError(e)); setUsers([]); }
  }
  useEffect(() => { load(); }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try {
      await api.post("/users", { username, password, role });
      toast.success(`Created ${username}`);
      setUsername(""); setPassword(""); setRole("viewer"); load();
    } catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  }
  async function setUserRole(u: User, newRole: string) {
    try { await api.patch(`/users/${u.username}`, { role: newRole }); load(); }
    catch (e) { toast.error(apiError(e)); }
  }
  async function toggleDisabled(u: User) {
    if (!u.disabled && !confirm(`Disable ${u.username}? Their sessions end immediately.`)) return;
    try { await api.patch(`/users/${u.username}`, { disabled: !u.disabled }); load(); }
    catch (e) { toast.error(apiError(e)); }
  }
  async function remove(u: User) {
    if (!confirm(`Delete ${u.username}? Their sessions end immediately; the audit log keeps their history.`)) return;
    try { await api.delete(`/users/${u.username}`); toast.success(`Deleted ${u.username}`); load(); }
    catch (e) { toast.error(apiError(e)); }
  }
  async function resetPassword(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.patch(`/users/${resetting}`, { password: newPassword });
      toast.success(resetting === me
        ? "Password changed — your sessions were revoked, log in again"
        : `Password reset for ${resetting} — their sessions were revoked`);
      setResetting(""); setNewPassword("");
    } catch (e) { toast.error(apiError(e)); }
  }

  return (
    <div>
      <PageHeader title="Users" subtitle="Who can sign in, and with what rank" actions={
        <Button variant="secondary" size="sm" onClick={load}><RefreshCw size={13} /> Refresh</Button>
      } />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          {users === null ? <Spinner /> : users.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center text-zinc-500">
              <UsersIcon size={32} /><p className="text-sm">No users.</p>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-4 py-2.5 font-medium">User</th><th className="px-4 py-2.5 font-medium">Role</th>
                <th className="px-4 py-2.5 font-medium">Last login</th><th className="px-4 py-2.5"></th>
              </tr></thead>
              <tbody className="divide-y divide-zinc-800/70">
                {users.map((u) => (
                  <tr key={u.username} className={`hover:bg-zinc-800/40 ${u.disabled ? "opacity-50" : ""}`}>
                    <td className="px-4 py-3 font-medium text-zinc-200">
                      <div className="flex items-center gap-2">
                        {u.username}
                        {u.username === me && <Badge color="green">you</Badge>}
                        {u.disabled && <Badge color="red">disabled</Badge>}
                        {u.source && u.source !== "local" && <Badge color="amber">{u.source}</Badge>}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <Badge color={ROLE_COLOR[u.role]}>{u.role}</Badge>
                        <Select className="w-28" value={u.role} onChange={(e) => setUserRole(u, e.target.value)}>
                          {roles.map((r) => <option key={r} value={r}>{r}</option>)}
                        </Select>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-500">{fmtWhen(u.last_login)}</td>
                    <td className="px-4 py-3"><div className="flex justify-end gap-1">
                      {u.source !== "oidc" && (
                        // An SSO user has no password here to reset — their
                        // credential lives at the IdP. Disable/delete still apply.
                        <Button size="sm" variant="secondary" title="Set a new password"
                                onClick={() => { setResetting(u.username); setNewPassword(""); }}>
                          <KeyRound size={13} />
                        </Button>
                      )}
                      <Button size="sm" variant="secondary" title={u.disabled ? "Enable" : "Disable — ends their sessions now"}
                              onClick={() => toggleDisabled(u)}>
                        {u.disabled ? <CircleCheck size={13} className="text-emerald-400" /> : <Ban size={13} className="text-amber-400" />}
                      </Button>
                      <Button size="sm" variant="ghost" title="Delete" onClick={() => remove(u)}>
                        <Trash2 size={14} className="text-red-400" />
                      </Button>
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card className="h-fit p-5">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><UserPlus size={15} /> New user</h2>
          <form onSubmit={create} className="space-y-3">
            <div><Label>Username</Label>
              <Input value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())}
                     placeholder="lowercase letters, digits, . _ -" required /></div>
            <div><Label>Password</Label>
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                     placeholder="at least 8 characters" autoComplete="new-password" required /></div>
            <div><Label>Role</Label>
              <Select value={role} onChange={(e) => setRole(e.target.value)}>
                {roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </Select></div>
            <Button type="submit" loading={busy} className="w-full">Create user</Button>
          </form>
          <p className="mt-4 text-xs text-zinc-500">
            <strong className="text-zinc-400">viewer</strong> sees everything and changes nothing.{" "}
            <strong className="text-zinc-400">operator</strong> builds images and bundles, manages image
            files, and runs the provisioning server.{" "}
            <strong className="text-zinc-400">admin</strong> additionally manages users, tokens, sessions,
            server configuration and the secrets manager. The last enabled admin cannot be removed.
          </p>
        </Card>
      </div>

      <Modal open={!!resetting} onClose={() => setResetting("")} title={`Set a new password for ${resetting}`}
             subtitle="Their existing sessions are revoked — whoever knew the old password is out.">
        <form onSubmit={resetPassword} className="space-y-3">
          <div><Label>New password</Label>
            <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                   placeholder="at least 8 characters" autoComplete="new-password" autoFocus required /></div>
          <Button type="submit" className="w-full">Set password</Button>
        </form>
      </Modal>
    </div>
  );
}
