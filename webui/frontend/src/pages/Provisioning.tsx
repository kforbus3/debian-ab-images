import { useEffect, useState } from "react";
import { Play, Square, RefreshCw, Save, Monitor, Plus, Trash2 } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, Badge, Alert } from "../components/ui";

interface Assignment { mac: string; image: string; action: string; name: string; }
interface Iface { name: string; ip: string; prefixlen: number; network: string; netmask: string; mac: string; up: boolean; carrier: boolean; default: boolean; }
interface Suggestion { SERVER_IP?: string; prefixlen?: number; DHCP_NETMASK?: string; PROXY_SUBNET?: string; DHCP_RANGE_START?: string; DHCP_RANGE_END?: string; }

export default function Provisioning() {
  const toast = useToast();
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [clients, setClients] = useState<any[]>([]);
  const [images, setImages] = useState<string[]>([]);
  const [ifaces, setIfaces] = useState<Iface[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [advanced, setAdvanced] = useState(false);
  const [assign, setAssign] = useState<Assignment[]>([]);
  const [suggest, setSuggest] = useState<Suggestion>({});

  async function loadAll() {
    try {
      const [c, s, im] = await Promise.all([api.get("/server/config"), api.get("/server/status"), api.get("/images")]);
      setCfg(c.data); setRunning(s.data.running); setImages(im.data.images.map((x: any) => x.name));
    } catch (e) { toast.error(apiError(e)); }
    api.get("/server/interfaces").then((r) => { setIfaces(r.data.interfaces); setSuggest(r.data.suggestion || {}); }).catch(() => {});
    api.get("/server/preflight").then((r) => setProblems(r.data.problems)).catch(() => {});
    loadAssignments();
  }
  async function loadClients() { try { setClients((await api.get("/server/clients")).data); } catch {} }
  async function loadAssignments() {
    try { setAssign((await api.get("/server/assignments")).data); } catch {}
  }

  async function saveAssignments(next: Assignment[]) {
    try {
      const { data } = await api.put("/server/assignments", next);
      setAssign(data); toast.success("Assignments saved");
    } catch (e) { toast.error(apiError(e)); }
  }
  const addAssignment = (mac = "") =>
    setAssign((a) => [...a, { mac, image: images[0] || "", action: "", name: "" }]);
  const setAssignment = (i: number, k: keyof Assignment, v: string) =>
    setAssign((a) => a.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  const removeAssignment = (i: number) => setAssign((a) => a.filter((_, j) => j !== i));

  useEffect(() => { loadAll(); }, []);
  useEffect(() => {
    if (!running) return;
    loadClients();
    const t = setInterval(() => { loadClients(); api.get("/server/status").then((r) => setRunning(r.data.running)); }, 5000);
    return () => clearInterval(t);
  }, [running]);

  const set = (k: string, v: string) => setCfg((c) => ({ ...c, [k]: v }));

  // Choosing the interface is the only network decision the operator has to
  // make; its address and subnet determine everything else, so derive the rest
  // rather than asking for numbers they'd have to look up.
  function selectInterface(name: string) {
    const i = ifaces.find((f) => f.name === name);
    if (!i) { set("INTERFACE", name); return; }
    // A NIC with no IPv4 is the normal case for a dedicated provisioning port:
    // nothing on that segment hands out addresses, because this server is what
    // will. Propose a free subnet; the server assigns it to the NIC on start.
    if (!i.ip) {
      setCfg((c) => ({
        ...c, INTERFACE: i.name,
        SERVER_IP: suggest.SERVER_IP || "",
        SERVER_PREFIXLEN: String(suggest.prefixlen || 24),
        DHCP_NETMASK: suggest.DHCP_NETMASK || "",
        PROXY_SUBNET: suggest.PROXY_SUBNET || "",
        DHCP_RANGE_START: suggest.DHCP_RANGE_START || "",
        DHCP_RANGE_END: suggest.DHCP_RANGE_END || "",
      }));
      return;
    }
    const size = 2 ** (32 - i.prefixlen);
    const base = i.network.split(".").map(Number);
    const at = (off: number) => {
      const v = ((base[0] << 24) | (base[1] << 16) | (base[2] << 8) | base[3]) + off;
      return [(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255].join(".");
    };
    const lo = Math.min(100, Math.floor(size / 4));
    const hi = Math.min(200, size - 2);
    setCfg((c) => ({
      ...c,
      INTERFACE: i.name,
      SERVER_IP: i.ip,
      SERVER_PREFIXLEN: String(i.prefixlen),
      PROXY_SUBNET: i.network,
      DHCP_NETMASK: i.netmask,
      ...(size >= 8 && lo < hi ? { DHCP_RANGE_START: at(lo), DHCP_RANGE_END: at(hi) } : {}),
    }));
  }

  const selected = ifaces.find((f) => f.name === cfg.INTERFACE);

  async function save() {
    setBusy(true);
    try {
      await api.put("/server/config", cfg);
      toast.success("Configuration saved");
      const r = await api.get("/server/preflight");
      setProblems(r.data.problems);
    } catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
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
      <Alert title="Not ready to provision yet" items={problems} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-1 text-sm font-semibold">Server configuration</h2>
          <p className="mb-3 text-xs text-zinc-500">
            Pick the network the machines are on and the image to write. DHCP and TFTP
            are confined to that interface — no other network sees them.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <Label>Provisioning network</Label>
              <Select value={cfg.INTERFACE || ""} onChange={(e) => selectInterface(e.target.value)}>
                <option value="">— select an interface —</option>
                {ifaces.map((i) => (
                  <option key={i.name} value={i.name}>
                    {i.name} · {i.ip ? `${i.ip}/${i.prefixlen}` : "no IP address"}{i.default ? " (main LAN — carries the default route)" : ""}{!i.up ? " — link down" : ""}
                  </option>
                ))}
              </Select>
              {ifaces.length === 0 && <p className="mt-1 text-xs text-amber-400">No interfaces detected.</p>}
              {selected?.default && (
                <p className="mt-1 text-xs text-amber-400">
                  This is the host's main LAN. Standalone DHCP here will compete with the
                  network's existing DHCP server — use a dedicated NIC, or switch to proxy
                  mode under Advanced.
                </p>
              )}
            </div>
            <div><Label>Image to deploy</Label><Select value={cfg.IMAGE_FILE || ""} onChange={(e) => set("IMAGE_FILE", e.target.value)}>
              <option value="">— select —</option>{images.map((n) => <option key={n} value={n}>{n}</option>)}
            </Select></div>
            <div><Label>After imaging</Label><Select value={cfg.ACTION || "reboot"} onChange={(e) => set("ACTION", e.target.value)}><option value="reboot">reboot</option><option value="poweroff">poweroff</option><option value="shell">shell</option></Select></div>
          </div>

          {selected && (
            <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 text-xs text-zinc-400">
              <p className="mb-1 font-medium text-zinc-300">This server will:</p>
              {!selected.ip && cfg.SERVER_IP && (
                <p>give <span className="text-zinc-200">{selected.name}</span> the address{" "}
                  <span className="text-zinc-200">{cfg.SERVER_IP}/{cfg.SERVER_PREFIXLEN || 24}</span>{" "}
                  when it starts — the NIC has none, and this is applied at runtime only
                  (a reboot reverts it; starting again re-applies it)</p>
              )}
              <p>serve DHCP + TFTP on <span className="text-zinc-200">{selected.name}</span> only, as <span className="text-zinc-200">{cfg.SERVER_IP}</span></p>
              {cfg.MODE !== "proxy" && cfg.DHCP_RANGE_START && (
                <p>lease <span className="text-zinc-200">{cfg.DHCP_RANGE_START} – {cfg.DHCP_RANGE_END}</span> to machines that boot</p>
              )}
              {cfg.MODE === "proxy" && <p>answer only PXE requests on <span className="text-zinc-200">{cfg.PROXY_SUBNET}</span>, leaving IPs to the existing DHCP server</p>}
            </div>
          )}

          <button onClick={() => setAdvanced((a) => !a)} className="mt-4 block text-xs text-zinc-500 hover:text-zinc-300">
            {advanced ? "Hide" : "Show"} advanced network settings
          </button>
          {advanced && (
            <div className="mt-3 grid grid-cols-2 gap-3 border-t border-zinc-800 pt-3">
              <div><Label>Server IP</Label><Input value={cfg.SERVER_IP || ""} onChange={(e) => set("SERVER_IP", e.target.value)} /></div>
              <div><Label>DHCP mode</Label><Select value={cfg.MODE || "dhcp"} onChange={(e) => set("MODE", e.target.value)}><option value="dhcp">standalone (private)</option><option value="proxy">proxy (coexist with LAN DHCP)</option></Select></div>
              {cfg.MODE === "proxy" ? (
                <div className="col-span-2"><Label>Proxy subnet</Label><Input value={cfg.PROXY_SUBNET || ""} onChange={(e) => set("PROXY_SUBNET", e.target.value)} /></div>
              ) : (
                <>
                  <div><Label>Range start</Label><Input value={cfg.DHCP_RANGE_START || ""} onChange={(e) => set("DHCP_RANGE_START", e.target.value)} /></div>
                  <div><Label>Range end</Label><Input value={cfg.DHCP_RANGE_END || ""} onChange={(e) => set("DHCP_RANGE_END", e.target.value)} /></div>
                  <div><Label>Router (optional)</Label><Input value={cfg.DHCP_ROUTER || ""} onChange={(e) => set("DHCP_ROUTER", e.target.value)} placeholder="none — imaging needs no gateway" /></div>
                  <div><Label>DNS (optional)</Label><Input value={cfg.DHCP_DNS || ""} onChange={(e) => set("DHCP_DNS", e.target.value)} placeholder="none" /></div>
                </>
              )}
            </div>
          )}
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
              <thead><tr className="border-b border-zinc-800 text-xs uppercase text-zinc-500"><th className="px-3 py-2">MAC</th><th className="px-3 py-2">IP</th><th className="px-3 py-2">Status</th><th /></tr></thead>
              <tbody className="divide-y divide-zinc-800/70">
                {clients.map((c) => {
                  const targeted = assign.some((a) => a.mac === (c.mac || "").toLowerCase());
                  return <tr key={c.mac + c.ip}>
                    <td className="px-3 py-2 font-mono text-xs text-zinc-300">{c.mac}</td>
                    <td className="px-3 py-2 text-zinc-400">{c.ip || "—"}</td>
                    <td className="px-3 py-2"><Badge color={c.event === "imaged" ? "green" : "blue"}>{c.event || "seen"}</Badge></td>
                    <td className="px-3 py-2 text-right">
                      {targeted ? <span className="text-xs text-zinc-500">targeted</span>
                        : c.mac && c.mac !== "—" &&
                          <button onClick={() => addAssignment(c.mac.toLowerCase())}
                            className="text-xs text-brand-400 hover:text-brand-300">assign image…</button>}
                    </td>
                  </tr>;
                })}
              </tbody>
            </table>
          )}
        </Card>

        <Card className="p-5 lg:col-span-2">
          <div className="mb-1 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Per-machine images</h2>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={() => addAssignment()}><Plus size={13} /> Add machine</Button>
              <Button size="sm" onClick={() => saveAssignments(assign)}><Save size={13} /> Save assignments</Button>
            </div>
          </div>
          <p className="mb-3 text-xs text-zinc-500">
            Machines listed here get the image you choose. Matching is by MAC at boot,
            so you can plug in a whole switch and still send one box a different build.
          </p>
          <div className="mb-4 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
            <Label>Machines with no assignment</Label>
            <Select value={cfg.UNASSIGNED || "image"} onChange={(e) => set("UNASSIGNED", e.target.value)}>
              <option value="image">Image them with the default image above</option>
              <option value="hold">Hold — discover only, do not touch their disks</option>
            </Select>
            <p className="mt-2 text-xs text-zinc-500">
              {cfg.UNASSIGNED === "hold" ? (
                <>Unknown machines will show their MAC and retry every {cfg.RETRY_SECONDS || 30}s
                without writing anything. Power on the fleet, let them appear below, assign
                images, and each machine picks its assignment up on its next retry — no second
                power cycle. Save the configuration to apply this.</>
              ) : (
                <>Any machine that PXE-boots gets imaged immediately, including one you have not
                assigned yet. Switch to <em>Hold</em> to discover MACs safely first.</>
              )}
            </p>
          </div>
          {assign.length === 0 ? (
            <p className="py-6 text-center text-sm text-zinc-500">
              No per-machine targeting — every machine that boots gets{" "}
              <span className="text-zinc-300">{cfg.IMAGE_FILE || "the default image"}</span>.
            </p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b border-zinc-800 text-xs uppercase text-zinc-500">
                <th className="px-2 py-2">MAC address</th><th className="px-2 py-2">Label</th>
                <th className="px-2 py-2">Image</th><th className="px-2 py-2">After imaging</th><th />
              </tr></thead>
              <tbody className="divide-y divide-zinc-800/70">
                {assign.map((a, i) => (
                  <tr key={i}>
                    <td className="px-2 py-2"><Input className="font-mono text-xs" value={a.mac} placeholder="00:11:22:33:44:55"
                      onChange={(e) => setAssignment(i, "mac", e.target.value)} /></td>
                    <td className="px-2 py-2"><Input value={a.name} placeholder="optional"
                      onChange={(e) => setAssignment(i, "name", e.target.value)} /></td>
                    <td className="px-2 py-2"><Select value={a.image} onChange={(e) => setAssignment(i, "image", e.target.value)}>
                      <option value="">— select —</option>{images.map((n) => <option key={n} value={n}>{n}</option>)}
                    </Select></td>
                    <td className="px-2 py-2"><Select value={a.action} onChange={(e) => setAssignment(i, "action", e.target.value)}>
                      <option value="">same as default</option><option value="reboot">reboot</option>
                      <option value="poweroff">poweroff</option><option value="shell">shell</option>
                    </Select></td>
                    <td className="px-2 py-2 text-right">
                      <button onClick={() => removeAssignment(i)} className="text-zinc-500 hover:text-red-400"><Trash2 size={15} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
