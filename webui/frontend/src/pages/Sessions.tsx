import { useEffect, useState } from "react";
import { Fingerprint, RefreshCw, XCircle } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, PageHeader, Spinner, Badge } from "../components/ui";

type Session = {
  id: string; username: string; created: number; expires: number;
  ip: string; current: boolean;
};

const fmtWhen = (t: number) => new Date(t * 1000).toLocaleString();

export default function Sessions() {
  const toast = useToast();
  const [sessions, setSessions] = useState<Session[] | null>(null);

  async function load() {
    try { setSessions((await api.get("/auth/sessions")).data.sessions); }
    catch (e) { toast.error(apiError(e)); setSessions([]); }
  }
  useEffect(() => { load(); }, []);

  async function revoke(s: Session) {
    if (s.current && !confirm("This is your own session — revoking it logs you out. Continue?")) return;
    try {
      await api.delete(`/auth/sessions/${s.id}`);
      toast.success(`Revoked ${s.username}'s session`);
      if (s.current) window.location.href = "/login";
      else load();
    } catch (e) { toast.error(apiError(e)); }
  }

  return (
    <div>
      <PageHeader title="Sessions" subtitle="Everyone signed in right now — revocation takes effect on their next request" actions={
        <Button variant="secondary" size="sm" onClick={load}><RefreshCw size={13} /> Refresh</Button>
      } />
      <Card>
        {sessions === null ? <Spinner /> : sessions.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center text-zinc-500">
            <Fingerprint size={32} /><p className="text-sm">No live sessions.</p>
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead><tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
              <th className="px-4 py-2.5 font-medium">User</th><th className="px-4 py-2.5 font-medium">From</th>
              <th className="px-4 py-2.5 font-medium">Signed in</th>
              <th className="px-4 py-2.5 font-medium">Expires</th><th className="px-4 py-2.5"></th>
            </tr></thead>
            <tbody className="divide-y divide-zinc-800/70">
              {sessions.map((s) => (
                <tr key={s.id} className="hover:bg-zinc-800/40">
                  <td className="px-4 py-3 font-medium text-zinc-200">
                    <div className="flex items-center gap-2">
                      {s.username}
                      {s.current && <Badge color="green">this session</Badge>}
                    </div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-400">{s.ip || "—"}</td>
                  <td className="px-4 py-3 text-xs text-zinc-500">{fmtWhen(s.created)}</td>
                  <td className="px-4 py-3 text-xs text-zinc-500">{fmtWhen(s.expires)}</td>
                  <td className="px-4 py-3"><div className="flex justify-end">
                    <Button size="sm" variant="ghost" title="Revoke — they are signed out immediately"
                            onClick={() => revoke(s)}>
                      <XCircle size={14} className="text-red-400" />
                    </Button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <p className="mt-4 text-xs text-zinc-500">
        Sessions slide 12 hours on each use and end 7 days after sign-in regardless. Only hashes are
        stored server-side, so a copy of the state file cannot be replayed as a session.
      </p>
    </div>
  );
}
