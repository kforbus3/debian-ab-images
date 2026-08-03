import { useEffect, useState } from "react";
import { api, apiError } from "../lib/api";
import { Card, PageHeader, Badge, Spinner } from "../components/ui";
import { Server, CheckCircle2, AlertTriangle, Clock } from "lucide-react";

type Machine = {
  id: string;
  image: string;
  disk: string;
  address: string;
  hostname: string;
  slot: string;
  version: string;
  imaged_at: number | null;
  booted_at: number | null;
  state: "running" | "imaged" | "never-booted";
};

const STATE = {
  running:       { badge: "green", label: "Running",      icon: <CheckCircle2 size={16} className="text-emerald-400" /> },
  imaged:        { badge: "blue",  label: "Imaged",       icon: <Clock size={16} className="text-sky-400" /> },
  "never-booted":{ badge: "amber", label: "Never booted", icon: <AlertTriangle size={16} className="text-amber-400" /> },
} as const;

function when(ts: number | null) {
  if (!ts) return "—";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export default function Fleet() {
  const [rows, setRows] = useState<Machine[] | null>(null);
  const [counts, setCounts] = useState({ running: 0, never_booted: 0 });
  const [error, setError] = useState("");

  async function load() {
    try {
      const { data } = await api.get("/deployments");
      setRows(data.machines);
      setCounts({ running: data.running, never_booted: data.never_booted });
      setError("");
    } catch (e) { setError(apiError(e)); }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  return (
    <div>
      <PageHeader title="Fleet" subtitle="Every machine this server has imaged, and whether it came back" />

      {error && <Card className="mb-6 border-red-500/40 bg-red-500/10">
        <p className="p-4 text-sm text-red-300">{error}</p></Card>}
      {rows === null && <Spinner />}

      {rows !== null && (
        <>
          {/* A machine that imaged and never reported back is the failure this
              page exists to make visible: the imager's own report is sent
              before the reboot, so on its own it cannot tell you this. */}
          {counts.never_booted > 0 && (
            <Card className="mb-6 border-amber-500/40 bg-amber-500/10">
              <p className="flex items-center gap-2 p-4 text-sm text-amber-200">
                <AlertTriangle size={15} />
                {counts.never_booted} machine{counts.never_booted === 1 ? "" : "s"} finished
                imaging but never reported booting. They may have failed to boot what was
                written, or simply moved to a network that cannot reach this server.
              </p>
            </Card>
          )}

          <Card>
            <div className="flex items-center gap-2 border-b border-zinc-800 px-5 py-3">
              <h2 className="text-sm font-semibold text-zinc-200">Machines</h2>
              {rows.length > 0 && <Badge color="brand">{rows.length}</Badge>}
              {counts.running > 0 && <Badge color="green">{counts.running} running</Badge>}
            </div>

            {rows.length === 0 ? (
              <div className="py-14 text-center">
                <Server className="mx-auto text-zinc-700" size={30} />
                <p className="mt-3 text-sm text-zinc-400">No machines have been imaged yet.</p>
                <p className="mt-1 text-xs text-zinc-500">
                  Machines appear here once they finish imaging, and are marked running
                  when they boot and report back.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-zinc-800 text-xs uppercase text-zinc-500">
                      <th className="px-5 py-2">Machine</th>
                      <th className="px-3 py-2">State</th>
                      <th className="px-3 py-2">Image</th>
                      <th className="px-3 py-2">Slot</th>
                      <th className="px-3 py-2">Imaged</th>
                      <th className="px-3 py-2">Booted</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/70">
                    {rows.map((m) => {
                      const st = STATE[m.state];
                      return (
                        <tr key={m.id}>
                          <td className="px-5 py-2.5">
                            <div className="flex items-center gap-2">
                              {st.icon}
                              <div className="min-w-0">
                                <div className="font-mono text-xs text-zinc-200">{m.id}</div>
                                <div className="text-xs text-zinc-500">
                                  {m.hostname || m.address || "—"}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-2.5"><Badge color={st.badge}>{st.label}</Badge></td>
                          <td className="px-3 py-2.5 text-xs text-zinc-400">
                            <span className="block max-w-[16rem] truncate">
                              {m.image ? m.image.split("/").pop() : "—"}
                            </span>
                            {m.version && <span className="text-zinc-600">{m.version}</span>}
                          </td>
                          <td className="px-3 py-2.5 text-zinc-400">{m.slot || "—"}</td>
                          <td className="px-3 py-2.5 text-xs tabular-nums text-zinc-400">{when(m.imaged_at)}</td>
                          <td className="px-3 py-2.5 text-xs tabular-nums text-zinc-400">{when(m.booted_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <p className="mt-4 text-xs text-zinc-600">
            Machines report once when imaging finishes and again when they boot the image.
            This record is kept on disk and does not expire — unlike the Imaging page, which
            shows only what is happening right now.
          </p>
        </>
      )}
    </div>
  );
}
