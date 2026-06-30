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
    ssh_key: "", ssh_key_only: false,
    encrypt: false, unlock: "keyfile", luks_passphrase: "", tang_url: "",
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
            <div className="col-span-2"><Label>SSH public key (optional)</Label><Input value={opts.ssh_key} onChange={(e) => set("ssh_key", e.target.value)} placeholder="ssh-ed25519 AAAA… user@host" /></div>
            <label className="col-span-2 flex items-center gap-2 text-sm text-zinc-300">
              <input type="checkbox" checked={opts.ssh_key_only} disabled={!opts.ssh_key} onChange={(e) => set("ssh_key_only", e.target.checked)} />
              SSH key-only (disable password login) {!opts.ssh_key && <span className="text-xs text-zinc-500">— add a key first</span>}
            </label>
          </div>

          <div className="mt-4 border-t border-zinc-800 pt-4">
            <label className="flex items-center gap-2 text-sm font-medium text-zinc-200">
              <input type="checkbox" checked={opts.encrypt} onChange={(e) => set("encrypt", e.target.checked)} />
              Encrypt disk (LUKS2)
            </label>
            {opts.encrypt && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div><Label>Auto-unlock method</Label><Select value={opts.unlock} onChange={(e) => set("unlock", e.target.value)}>
                  <option value="tpm2">TPM2 (recommended)</option>
                  <option value="tang">Tang / NBDE (network)</option>
                  <option value="keyfile">Keyfile (universal, weaker)</option>
                  <option value="passphrase">Passphrase (no auto-unlock)</option>
                </Select></div>
                <div><Label>LUKS passphrase (recovery)</Label><Input type="password" value={opts.luks_passphrase} onChange={(e) => set("luks_passphrase", e.target.value)} placeholder="required" /></div>
                {opts.unlock === "tang" && <div className="col-span-2"><Label>Tang server URL</Label><Input value={opts.tang_url} onChange={(e) => set("tang_url", e.target.value)} placeholder="http://tang.lan:7500" /></div>}
                <p className="col-span-2 text-xs text-zinc-500">
                  {opts.unlock === "tpm2" && "Sealed to each machine's TPM on first boot; no key left on disk."}
                  {opts.unlock === "tang" && "Unlocks from a Tang server on your LAN; no key on disk."}
                  {opts.unlock === "keyfile" && "Auto-unlocks anywhere, but the key sits on the same disk — weak at-rest protection."}
                  {opts.unlock === "passphrase" && "Prompts for the passphrase at every boot — most secure, not unattended."}
                </p>
              </div>
            )}
          </div>

          <Button className="mt-4 w-full" loading={running} onClick={startImage}
            disabled={!opts.password || (opts.encrypt && !opts.luks_passphrase) || (opts.encrypt && opts.unlock === "tang" && !opts.tang_url)}>
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
