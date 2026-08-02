import { useEffect, useState } from "react";
import { api, apiError } from "../lib/api";
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

function elapsed(sec: number) {
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60);
  return m < 60 ? `${m}m ${Math.round(sec % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

function StateIcon({ state }: { state: Machine["state"] }) {
  if (state === "done") return <CheckCircle2 className="h-5 w-5 text-emerald-500" />;
  if (state === "failed") return <XCircle className="h-5 w-5 text-red-500" />;
  if (state === "stalled") return <AlertTriangle className="h-5 w-5 text-amber-500" />;
  return <Loader2 className="h-5 w-5 animate-spin text-sky-500" />;
}

function MachineRow({ m, onForget }: { m: Machine; onForget: (id: string) => void }) {
  const bar =
    m.state === "failed" ? "bg-red-500"
    : m.state === "done" ? "bg-emerald-500"
    : m.state === "stalled" ? "bg-amber-500"
    : "bg-sky-500";

  return (
    <div className="border-b border-zinc-200 py-4 last:border-0 dark:border-zinc-800">
      <div className="flex items-start gap-3">
        <div className="pt-0.5"><StateIcon state={m.state} /></div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-sm font-medium">{m.id}</span>
            {m.address && <span className="text-xs text-zinc-500">{m.address}</span>}
            <Badge color={m.state === "failed" ? "red" : m.state === "done" ? "emerald"
                        : m.state === "stalled" ? "amber" : "sky"}>
              {PHASE_LABEL[m.phase] ?? m.phase}
            </Badge>
            <span className="ml-auto text-xs tabular-nums text-zinc-500">{elapsed(m.age)}</span>
          </div>

          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
            <div className={`h-full rounded-full transition-all duration-500 ${bar}`}
                 style={{ width: `${m.percent}%` }} />
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
            <span className="tabular-nums">{m.percent}%</span>
            {m.disk && <span className="inline-flex items-center gap-1">
              <HardDrive className="h-3 w-3" />{m.disk}</span>}
            {m.detail && <span>{m.detail}</span>}
            {m.image && <span className="truncate">{m.image.split("/").pop()}</span>}
            {/* A machine that has stopped reporting is called out, because the
                bar alone would look like slow progress rather than silence. */}
            {m.state === "stalled" &&
              <span className="text-amber-600">no report for {elapsed(m.stale_for)}</span>}
          </div>
        </div>

        {(m.state === "failed" || m.state === "stalled") && (
          <Button variant="ghost" size="sm" onClick={() => onForget(m.id)}>Dismiss</Button>
        )}
      </div>
    </div>
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
    <div className="space-y-6">
      <PageHeader
        title="Imaging"
        subtitle="Machines writing an image right now. Each disappears once it finishes or stops reporting."
      />

      {error && <Card><div className="p-4 text-sm text-red-600">{error}</div></Card>}
      {machines === null && <Spinner />}

      {machines !== null && (
        <Card>
          <div className="border-b border-zinc-200 px-5 py-3 dark:border-zinc-800">
            <h2 className="text-sm font-semibold">
              In progress {active.length > 0 && (
                <span className="ml-1 rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-700
                                 dark:bg-sky-950 dark:text-sky-300">{active.length}</span>
              )}
            </h2>
          </div>
          <div className="px-5">
            {active.length === 0 ? (
              <div className="py-12 text-center">
                <HardDrive className="mx-auto h-8 w-8 text-zinc-300 dark:text-zinc-700" />
                <p className="mt-3 text-sm text-zinc-500">No machines are imaging.</p>
                <p className="mt-1 text-xs text-zinc-400">
                  Boot a machine from the provisioning network and it will appear here.
                </p>
              </div>
            ) : active.map((m) => <MachineRow key={m.id} m={m} onForget={forget} />)}
          </div>
        </Card>
      )}

      {finished.length > 0 && (
        <Card>
          <div className="border-b border-zinc-200 px-5 py-3 dark:border-zinc-800">
            <h2 className="text-sm font-semibold">Just finished</h2>
          </div>
          <div className="px-5">
            {finished.map((m) => <MachineRow key={m.id} m={m} onForget={forget} />)}
          </div>
        </Card>
      )}

      <p className="text-xs text-zinc-400">
        Machines report as they work. One that finishes drops off shortly after; one that stops
        reporting is marked stalled and then removed, so this page only ever shows current work.
      </p>
    </div>
  );
}
