import { useEffect, useState } from "react";
import { Play, Square, RefreshCw, Save, Monitor } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, Badge } from "../components/ui";

export default function Provisioning() {
  const toast = useToast();
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [clients, setClients] = useState<any[]>([]);
  const [images, setImages] = useState<string[]>([]);

  async function loadAll() {
    try {
      const [c, s, im] = await Promise.all([api.get("/server/config"), api.get("/server/status"), api.get("/images")]);
      setCfg(c.data); setRunning(s.data.running); setImages(im.data.images.map((x: any) => x.name));
    } catch (e) { toast.error(apiError(e)); }
  }
  async function loadClients() { try { setClients((await api.get("/server/clients")).data); } catch {} }

  useEffect(() => { loadAll(); }, []);
  useEffect(() => {
    if (!running) return;
    loadClients();
    const t = setInterval(() => { loadClients(); api.get("/server/status").then((r) => setRunning(r.data.running)); }, 5000);
    return () => clearInterval(t);
  }, [running]);

  const set = (k: string, v: string) => setCfg((c) => ({ ...c, [k]: v }));

  async function save() {
    setBusy(true);
    try { await api.put("/server/config", cfg); toast.success("Configuration saved"); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  }
  async function ctrl(action: "up" | "down") {
    setBusy(true);
    try { await api.post(`/server/${action}`); toast.success(action === "up" ? "Server starting" : "Server stopped"); await loadAll(); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <div>
      <PageHeader title="Provisioning" subtitle="PXE network imaging server" actions={
        <>
          <Badge color={running ? "green" : "zinc"}>{running ? "running" : "stopped"}</Badge>
          {running
            ? <Button variant="danger" size="sm" loading={busy} onClick={() => ctrl("down")}><Square size={13} /> Stop</Button>
            : <Button size="sm" loading={busy} onClick={() => ctrl("up")}><Play size={13} /> Start</Button>}
        </>
      } />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-3 text-sm font-semibold">Server configuration</h2>
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Server IP</Label><Input value={cfg.SERVER_IP || ""} onChange={(e) => set("SERVER_IP", e.target.value)} /></div>
            <div><Label>Interface</Label><Input value={cfg.INTERFACE || ""} onChange={(e) => set("INTERFACE", e.target.value)} placeholder="eth0" /></div>
            <div><Label>Image to deploy</Label><Select value={cfg.IMAGE_FILE || ""} onChange={(e) => set("IMAGE_FILE", e.target.value)}>
              <option value="">— select —</option>{images.map((n) => <option key={n} value={n}>{n}</option>)}
            </Select></div>
            <div><Label>After imaging</Label><Select value={cfg.ACTION || "reboot"} onChange={(e) => set("ACTION", e.target.value)}><option value="reboot">reboot</option><option value="poweroff">poweroff</option><option value="shell">shell</option></Select></div>
            <div><Label>DHCP mode</Label><Select value={cfg.MODE || "proxy"} onChange={(e) => set("MODE", e.target.value)}><option value="proxy">proxy (coexist)</option><option value="dhcp">standalone DHCP</option></Select></div>
            {cfg.MODE === "dhcp" ? (
              <>
                <div><Label>Range start</Label><Input value={cfg.DHCP_RANGE_START || ""} onChange={(e) => set("DHCP_RANGE_START", e.target.value)} /></div>
                <div><Label>Range end</Label><Input value={cfg.DHCP_RANGE_END || ""} onChange={(e) => set("DHCP_RANGE_END", e.target.value)} /></div>
                <div><Label>Router</Label><Input value={cfg.DHCP_ROUTER || ""} onChange={(e) => set("DHCP_ROUTER", e.target.value)} /></div>
              </>
            ) : (
              <div><Label>Proxy subnet</Label><Input value={cfg.PROXY_SUBNET || ""} onChange={(e) => set("PROXY_SUBNET", e.target.value)} placeholder="192.168.1.0" /></div>
            )}
          </div>
          <Button className="mt-4" loading={busy} onClick={save}><Save size={14} /> Save configuration</Button>
        </Card>

        <Card className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold flex items-center gap-2"><Monitor size={15} /> Machines imaging now</h2>
            <Button variant="secondary" size="sm" onClick={loadClients}><RefreshCw size={13} /></Button>
          </div>
          {clients.length === 0 ? (
            <p className="py-10 text-center text-sm text-zinc-500">{running ? "No machines have PXE-booted yet." : "Start the server to monitor machines."}</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b border-zinc-800 text-xs uppercase text-zinc-500"><th className="px-3 py-2">MAC</th><th className="px-3 py-2">IP</th><th className="px-3 py-2">Status</th></tr></thead>
              <tbody className="divide-y divide-zinc-800/70">
                {clients.map((c) => (
                  <tr key={c.mac}><td className="px-3 py-2 font-mono text-xs text-zinc-300">{c.mac}</td><td className="px-3 py-2 text-zinc-400">{c.ip || "—"}</td><td className="px-3 py-2"><Badge color="blue">{c.event || "seen"}</Badge></td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
