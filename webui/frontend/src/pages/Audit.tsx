import { useEffect, useState } from "react";
import { ScrollText, RefreshCw } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, PageHeader, Spinner, Badge } from "../components/ui";

type Event = {
  ts: number; actor: string; role: string; method: string; path: string;
  status: number; ip: string; summary?: string;
};

const METHOD_COLOR: Record<string, string> = { POST: "blue", PUT: "amber", PATCH: "amber", DELETE: "red", GET: "zinc" };
const statusColor = (s: number) => (s < 300 ? "green" : s < 500 ? "amber" : "red");

export default function Audit() {
  const toast = useToast();
  const [events, setEvents] = useState<Event[] | null>(null);
  const [actor, setActor] = useState("");

  async function load(filterActor = actor) {
    try {
      const { data } = await api.get("/audit", { params: { limit: 500, actor: filterActor || undefined } });
      setEvents(data.events);
    } catch (e) { toast.error(apiError(e)); setEvents([]); }
  }
  useEffect(() => { load(); }, []);

  return (
    <div>
      <PageHeader title="Audit Log" subtitle="Who did what, from where — newest first" actions={
        <div className="flex items-center gap-2">
          <Input className="w-44" value={actor} placeholder="filter by actor…"
                 onChange={(e) => setActor(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") load((e.target as HTMLInputElement).value); }} />
          <Button variant="secondary" size="sm" onClick={() => load()}><RefreshCw size={13} /></Button>
        </div>
      } />
      <Card>
        {events === null ? <Spinner /> : events.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center text-zinc-500">
            <ScrollText size={32} /><p className="text-sm">Nothing recorded{actor ? ` for ${actor}` : " yet"}.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-4 py-2.5 font-medium">When</th><th className="px-4 py-2.5 font-medium">Actor</th>
                <th className="px-4 py-2.5 font-medium">Action</th>
                <th className="px-4 py-2.5 font-medium">Status</th><th className="px-4 py-2.5 font-medium">From</th>
              </tr></thead>
              <tbody className="divide-y divide-zinc-800/70">
                {events.map((e, i) => (
                  <tr key={i} className="hover:bg-zinc-800/40">
                    <td className="whitespace-nowrap px-4 py-2.5 text-xs text-zinc-500">
                      {new Date(e.ts * 1000).toLocaleString()}
                    </td>
                    <td className="px-4 py-2.5">
                      <button className="font-medium text-zinc-200 hover:text-brand-400"
                              title={`Filter by ${e.actor}`}
                              onClick={() => { setActor(e.actor); load(e.actor); }}>
                        {e.actor}
                      </button>
                      {e.role && <span className="ml-1.5 text-xs text-zinc-500">{e.role}</span>}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <Badge color={METHOD_COLOR[e.method] || "zinc"}>{e.method}</Badge>
                        <code className="font-mono text-xs text-zinc-400">{e.path}</code>
                      </div>
                      {e.summary && <p className="mt-0.5 text-xs text-zinc-500">{e.summary}</p>}
                    </td>
                    <td className="px-4 py-2.5"><Badge color={statusColor(e.status)}>{e.status}</Badge></td>
                    <td className="px-4 py-2.5 font-mono text-xs text-zinc-500">{e.ip || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="mt-4 text-xs text-zinc-500">
        Kept in <code>output/audit.jsonl</code>, trimmed oldest-first past ~20,000 events. Failed
        logins record the attempted username, never the password.
      </p>
    </div>
  );
}
