import { useEffect, useState } from "react";
import { api, apiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Card, PageHeader, Badge, Button, Spinner } from "../components/ui";
import { HardDrive, CheckCircle2, XCircle, AlertTriangle, Loader2 } from "lucide-react";

type Machine = {
  id: string;
  phase: string;
  percent: number;
  detail: string;
  disk: string;
  image: string;
  address: string;
  age: number;
  stale_for: number;
  state: "active" | "stalled" | "done" | "failed";
  history: { phase: string; at: number }[];
};

const PHASE_LABEL: Record<string, string> = {
  booted: "Booted",
  detected: "Disk found",
  downloading: "Downloading",
  writing: "Writing image",
  verified: "Verified",
  expanding: "Expanding",
  done: "Complete",
  failed: "Failed",
};

// One place to decide what a state looks like, so the icon, the badge and the
// progress bar can never disagree about whether a machine is in trouble.
const STATE_STYLE: Record<Machine["state"], { badge: string; bar: string; icon: JSX.Element }> = {
  active:  { badge: "blue",  bar: "bg-brand-500",   icon: <Loader2 size={16} className="animate-spin text-brand-400" /> },
  stalled: { badge: "amber", bar: "bg-amber-500",   icon: <AlertTriangle size={16} className="text-amber-400" /> },
  done:    { badge: "green", bar: "bg-emerald-500", icon: <CheckCircle2 size={16} className="text-emerald-400" /> },
  failed:  { badge: "red",   bar: "bg-red-500",     icon: <XCircle size={16} className="text-red-400" /> },
};

function elapsed(sec: number) {
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  return m < 60 ? `${m}m ${Math.round(sec % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

function MachineRow({ m, onForget }: { m: Machine; onForget: (id: string) => void }) {
  const { canOperate } = useAuth();
  const st = STATE_STYLE[m.state];

  return (
    <div className="border-b border-zinc-800 px-5 py-4 last:border-0">
      <div className="flex items-start gap-3">
        <div className="pt-0.5">{st.icon}</div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-sm font-medium text-zinc-100">{m.id}</span>
            {m.address && <span className="text-xs text-zinc-500">{m.address}</span>}
            <Badge color={st.badge}>{PHASE_LABEL[m.phase] ?? m.phase}</Badge>
            <span className="ml-auto shrink-0 text-xs tabular-nums text-zinc-500">{elapsed(m.age)}</span>
          </div>

          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
            <div className={`h-full rounded-full transition-all duration-500 ${st.bar}`}
                 style={{ width: `${m.percent}%` }} />
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
            <span className="tabular-nums text-zinc-400">{m.percent}%</span>
            {m.disk && <span className="inline-flex items-center gap-1">
              <HardDrive size={12} />{m.disk}</span>}
            {m.detail && <span className="truncate">{m.detail}</span>}
            {m.image && <span className="truncate">{m.image.split("/").pop()}</span>}
            {/* A machine that has stopped reporting is called out, because the
                bar alone would look like slow progress rather than silence. */}
            {m.state === "stalled" &&
              <span className="text-amber-400">no report for {elapsed(m.stale_for)}</span>}
          </div>
        </div>

        {(m.state === "failed" || m.state === "stalled") && canOperate && (
          <Button variant="ghost" size="sm" onClick={() => onForget(m.id)}>Dismiss</Button>
        )}
      </div>
    </div>
  );
}

function Section({ title, count, children }:
    { title: string; count?: number; children: React.ReactNode }) {
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-zinc-800 px-5 py-3">
        <h2 className="text-sm font-semibold text-zinc-200">{title}</h2>
        {count !== undefined && count > 0 && <Badge color="brand">{count}</Badge>}
      </div>
      {children}
    </Card>
  );
}

export default function Imaging() {
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [error, setError] = useState("");

  async function load() {
    try {
      const { data } = await api.get<{ machines: Machine[] }>("/imaging");
      setMachines(data.machines);
      setError("");
    } catch (e) {
      setError(apiError(e));
    }
  }

  useEffect(() => {
    load();
    // Machines report on phase changes, and writing a large image is a long
    // quiet stretch, so a short poll keeps the elapsed time honest without
    // waiting on the next phase.
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  async function forget(id: string) {
    try {
      await api.delete(`/imaging/${encodeURIComponent(id)}`);
    } catch {
      /* the row expires on its own; a failed dismiss is not worth an error */
    }
    load();
  }

  const active = (machines ?? []).filter((m) => m.state === "active" || m.state === "stalled");
  const finished = (machines ?? []).filter((m) => m.state === "done" || m.state === "failed");

  return (
    <div>
      <PageHeader
        title="Imaging"
        subtitle="Machines writing an image right now"
      />

      {error && <Card className="mb-6 border-red-500/40 bg-red-500/10">
        <p className="p-4 text-sm text-red-300">{error}</p>
      </Card>}

      {machines === null && <Spinner />}

      {machines !== null && (
        <div className="space-y-4">
          <Section title="In progress" count={active.length}>
            {active.length === 0 ? (
              <div className="py-14 text-center">
                <HardDrive className="mx-auto text-zinc-700" size={30} />
                <p className="mt-3 text-sm text-zinc-400">No machines are imaging.</p>
                <p className="mt-1 text-xs text-zinc-500">
                  Boot a machine from the provisioning network and it will appear here.
                </p>
              </div>
            ) : active.map((m) => <MachineRow key={m.id} m={m} onForget={forget} />)}
          </Section>

          {finished.length > 0 && (
            <Section title="Just finished">
              {finished.map((m) => <MachineRow key={m.id} m={m} onForget={forget} />)}
            </Section>
          )}

          <p className="text-xs text-zinc-600">
            Machines report as they work. One that finishes drops off shortly after; one that
            stops reporting is marked stalled and then removed, so this page only ever shows
            current work.
          </p>
        </div>
      )}
    </div>
  );
}
