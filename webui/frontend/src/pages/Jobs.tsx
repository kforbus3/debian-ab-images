import { useEffect, useState } from "react";
import { ListChecks, RefreshCw, XCircle, ChevronDown, ChevronRight } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useToast } from "../components/Toast";
import { Button, Card, PageHeader, Spinner, Badge, LogView } from "../components/ui";

interface Job { id: string; type: string; label: string; status: string; started: string; finished: string; }

const STATUS_COLOR: Record<string, string> = { success: "green", running: "amber", failed: "red", canceled: "zinc" };

export default function Jobs() {
  const toast = useToast();
  const { canOperate } = useAuth();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<string>("");
  const [log, setLog] = useState<string[]>([]);

  async function load() {
    setLoading(true);
    try { setJobs((await api.get("/jobs")).data); }
    catch (e) { toast.error(apiError(e)); } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!jobs.some((j) => j.status === "running")) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [jobs]);

  async function toggle(id: string) {
    if (open === id) { setOpen(""); return; }
    try {
      const { data } = await api.get(`/jobs/${id}`);
      setLog((data.log || "").split("\n")); setOpen(id);
    } catch (e) { toast.error(apiError(e)); }
  }
  async function cancel(id: string) {
    try { await api.post(`/jobs/${id}/cancel`); toast.success("Cancel requested"); load(); }
    catch (e) { toast.error(apiError(e)); }
  }

  return (
    <div>
      <PageHeader title="Jobs" subtitle="Build history and logs (survives UI restarts)" actions={
        <Button variant="secondary" size="sm" onClick={load}><RefreshCw size={13} /> Refresh</Button>
      } />
      <Card>
        {loading ? <Spinner /> : jobs.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center text-zinc-500">
            <ListChecks size={32} /><p className="text-sm">No jobs yet.</p>
          </div>
        ) : (
          <div className="divide-y divide-zinc-800/70">
            {jobs.map((j) => (
              <div key={j.id}>
                <button onClick={() => toggle(j.id)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-zinc-800/40">
                  {open === j.id ? <ChevronDown size={15} className="text-zinc-500" /> : <ChevronRight size={15} className="text-zinc-500" />}
                  <span className="flex-1 text-sm font-medium text-zinc-200">{j.label}</span>
                  <span className="text-xs text-zinc-500">{j.started && new Date(j.started).toLocaleString()}</span>
                  <Badge color={STATUS_COLOR[j.status] || "zinc"}>{j.status}</Badge>
                  {j.status === "running" && (
                    <Button variant="danger" size="sm" disabled={!canOperate} title={canOperate ? undefined : "Your viewer role is read-only"}
                            onClick={(e) => { e.stopPropagation(); cancel(j.id); }}>
                      <XCircle size={13} /> Cancel
                    </Button>
                  )}
                </button>
                {open === j.id && <div className="px-4 pb-4"><LogView lines={log} /></div>}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
