import { useEffect, useRef, useState } from "react";
import { Hammer, Cpu } from "lucide-react";
import { api, apiError, tokens } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, LogView, Badge } from "../components/ui";

export default function Build() {
  const toast = useToast();
  const [opts, setOpts] = useState({
    suite: "trixie", hostname: "debian-ab", username: "admin", password: "",
    image_size: 8, root_size: 3072, compress: "zstd", packages: "",
  });
  const [log, setLog] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string>("");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => () => esRef.current?.close(), []);

  function stream(jobId: string) {
    setLog([]); setRunning(true); setStatus("running");
    const es = new EventSource(`/api/jobs/${jobId}/stream?token=${tokens.value}`);
    esRef.current = es;
    es.onmessage = (e) => setLog((l) => [...l, e.data]);
    es.addEventListener("end", (e: any) => {
      es.close(); setRunning(false); setStatus(e.data);
      e.data === "success" ? toast.success("Build finished") : toast.error(`Build ${e.data}`);
    });
    es.onerror = () => { es.close(); setRunning(false); };
  }

  async function startImage() {
    try { const { data } = await api.post("/builds", opts); stream(data.id); }
    catch (e) { toast.error(apiError(e)); }
  }
  async function startImager() {
    try { const { data } = await api.post("/imager/build"); stream(data.id); }
    catch (e) { toast.error(apiError(e)); }
  }
  const set = (k: string, v: any) => setOpts((o) => ({ ...o, [k]: v }));

  return (
    <div>
      <PageHeader title="Build Image" subtitle="Produce a bootable Debian A/B image" actions={
        <Button variant="secondary" onClick={startImager} disabled={running}><Cpu size={15} /> Build netboot imager</Button>
      } />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Debian suite</Label><Select value={opts.suite} onChange={(e) => set("suite", e.target.value)}><option value="trixie">trixie (13)</option><option value="bookworm">bookworm (12)</option></Select></div>
            <div><Label>Compression</Label><Select value={opts.compress} onChange={(e) => set("compress", e.target.value)}><option value="zstd">zstd</option><option value="gzip">gzip</option><option value="none">none</option></Select></div>
            <div><Label>Hostname</Label><Input value={opts.hostname} onChange={(e) => set("hostname", e.target.value)} /></div>
            <div><Label>Username</Label><Input value={opts.username} onChange={(e) => set("username", e.target.value)} /></div>
            <div><Label>Password</Label><Input type="password" value={opts.password} onChange={(e) => set("password", e.target.value)} placeholder="login password" /></div>
            <div><Label>Image size (GiB)</Label><Input type="number" value={opts.image_size} onChange={(e) => set("image_size", +e.target.value)} /></div>
            <div><Label>Root slot size (MiB)</Label><Input type="number" value={opts.root_size} onChange={(e) => set("root_size", +e.target.value)} /></div>
            <div className="col-span-2"><Label>Extra packages (space-separated)</Label><Input value={opts.packages} onChange={(e) => set("packages", e.target.value)} placeholder="vim curl qemu-guest-agent" /></div>
          </div>
          <Button className="mt-4 w-full" loading={running} onClick={startImage} disabled={!opts.password}>
            <Hammer size={15} /> {running ? "Building…" : "Start build"}
          </Button>
          {!opts.password && <p className="mt-2 text-xs text-amber-400">Set a login password to enable the build.</p>}
        </Card>
        <Card className="p-5">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Build log</h2>
            {status && <Badge color={status === "success" ? "green" : status === "running" ? "amber" : "red"}>{status}</Badge>}
          </div>
          <LogView lines={log} />
        </Card>
      </div>
    </div>
  );
}
