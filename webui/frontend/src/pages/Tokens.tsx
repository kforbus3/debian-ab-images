import { useEffect, useState } from "react";
import { KeySquare, Plus, Trash2, RefreshCw, Copy } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, Spinner, Badge } from "../components/ui";

type Token = {
  id: string; name: string; role: string; created: number;
  created_by: string; expires: number | null; last_used: number | null;
};

const ROLE_COLOR: Record<string, string> = { admin: "brand", operator: "blue", viewer: "zinc" };
const fmtWhen = (t: number | null) => (t ? new Date(t * 1000).toLocaleString() : "never");

export default function Tokens() {
  const toast = useToast();
  const [tokens, setTokens] = useState<Token[] | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");
  const [expiresDays, setExpiresDays] = useState("");
  const [busy, setBusy] = useState(false);
  // The raw token, held only in this component's state — the backend stores a
  // hash and can never show it again.
  const [minted, setMinted] = useState<{ name: string; token: string } | null>(null);

  async function load() {
    try { setTokens((await api.get("/tokens")).data.tokens); }
    catch (e) { toast.error(apiError(e)); setTokens([]); }
  }
  useEffect(() => { load(); }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true);
    try {
      const { data } = await api.post("/tokens",
        { name, role, expires_days: expiresDays ? Number(expiresDays) : undefined });
      setMinted({ name: data.name, token: data.token });
      setName(""); setRole("viewer"); setExpiresDays(""); load();
    } catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  }
  async function revoke(t: Token) {
    if (!confirm(`Revoke ${t.name}? Anything using it stops working immediately.`)) return;
    try { await api.delete(`/tokens/${t.id}`); toast.success(`Revoked ${t.name}`); load(); }
    catch (e) { toast.error(apiError(e)); }
  }
  const copy = (t: string) => { navigator.clipboard?.writeText(t); toast.success("Copied"); };

  return (
    <div>
      <PageHeader title="API Tokens" subtitle="Credentials for automation — CI, scripts, cron" actions={
        <Button variant="secondary" size="sm" onClick={load}><RefreshCw size={13} /> Refresh</Button>
      } />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          {tokens === null ? <Spinner /> : tokens.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center text-zinc-500">
              <KeySquare size={32} /><p className="text-sm">No API tokens.</p>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-4 py-2.5 font-medium">Name</th><th className="px-4 py-2.5 font-medium">Role</th>
                <th className="px-4 py-2.5 font-medium">Created by</th>
                <th className="px-4 py-2.5 font-medium">Last used</th>
                <th className="px-4 py-2.5 font-medium">Expires</th><th className="px-4 py-2.5"></th>
              </tr></thead>
              <tbody className="divide-y divide-zinc-800/70">
                {tokens.map((t) => (
                  <tr key={t.id} className="hover:bg-zinc-800/40">
                    <td className="px-4 py-3 font-medium text-zinc-200">{t.name}</td>
                    <td className="px-4 py-3"><Badge color={ROLE_COLOR[t.role]}>{t.role}</Badge></td>
                    <td className="px-4 py-3 text-xs text-zinc-500">{t.created_by}</td>
                    <td className="px-4 py-3 text-xs text-zinc-500">{fmtWhen(t.last_used)}</td>
                    <td className="px-4 py-3 text-xs text-zinc-500">{t.expires ? fmtWhen(t.expires) : "—"}</td>
                    <td className="px-4 py-3"><div className="flex justify-end">
                      <Button size="sm" variant="ghost" title="Revoke" onClick={() => revoke(t)}>
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
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Plus size={15} /> New token</h2>
          <form onSubmit={create} className="space-y-3">
            <div><Label>Name</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="ci-pipeline" required /></div>
            <div><Label>Role</Label>
              <Select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="viewer">viewer</option>
                <option value="operator">operator</option>
                <option value="admin">admin</option>
              </Select></div>
            <div><Label>Expires after (days, optional)</Label>
              <Input type="number" min="1" value={expiresDays} onChange={(e) => setExpiresDays(e.target.value)}
                     placeholder="never" /></div>
            <Button type="submit" loading={busy} className="w-full">Create token</Button>
          </form>

          {minted && (
            <div className="mt-4 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3">
              <p className="text-xs font-semibold text-emerald-300">
                {minted.name} — copy it now, it is shown exactly once
              </p>
              <div className="mt-2 flex items-start justify-between gap-2">
                <code className="break-all font-mono text-xs text-emerald-200">{minted.token}</code>
                <button onClick={() => copy(minted.token)} className="shrink-0 text-emerald-400 hover:text-emerald-200">
                  <Copy size={13} />
                </button>
              </div>
              <p className="mt-2 text-xs text-emerald-200/60">
                Only its hash is stored; there is no way to see it again. Use it as{" "}
                <code>Authorization: Bearer &lt;token&gt;</code>.
              </p>
            </div>
          )}

          <p className="mt-4 text-xs text-zinc-500">
            Tokens act with their own name and role — the audit log shows{" "}
            <code className="text-zinc-400">token:&lt;name&gt;</code>, not whoever created it. Give
            automation the least role that works: a pipeline that only builds needs operator, a
            dashboard that only reads needs viewer.
          </p>
        </Card>
      </div>
    </div>
  );
}
