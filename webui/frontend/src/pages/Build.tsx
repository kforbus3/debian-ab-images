import { useEffect, useRef, useState } from "react";
import { Hammer, Cpu, XCircle } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, LogView, Badge, Alert, ProgressBar } from "../components/ui";

const SUITES: Record<string, { value: string; label: string }[]> = {
  debian: [
    { value: "trixie", label: "trixie (13)" },
    { value: "bookworm", label: "bookworm (12)" },
  ],
  ubuntu: [
    { value: "resolute", label: "resolute (26.04 LTS)" },
    { value: "noble", label: "noble (24.04 LTS)" },
    { value: "jammy", label: "jammy (22.04 LTS)" },
  ],
};

export default function Build() {
  const toast = useToast();
  const [opts, setOpts] = useState({
    distro: "debian", suite: "trixie", arch: "amd64",
    hostname: "debian-ab", username: "admin", password: "",
    image_size: 0, root_size: 3072, compress: "zstd", packages: "",
    ssh_key: "", ssh_key_only: false,
    encrypt: false, unlock: "keyfile", luks_passphrase: "", tang_url: "",
  });
  const [log, setLog] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [jobId, setJobId] = useState<string>("");
  const [problems, setProblems] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ step: number; total: number; label: string } | null>(null);
  const [imagerArches, setImagerArches] = useState<Record<string, boolean> | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const loadImagerArches = () =>
    api.get("/images").then((r) => setImagerArches(r.data.imager_arches || null)).catch(() => {});

  useEffect(() => () => esRef.current?.close(), []);
  useEffect(() => {
    api.get("/preflight").then((r) => setProblems(r.data.problems)).catch(() => {});
    loadImagerArches();
    // Builds run for many minutes, so navigating away or reloading must not lose
    // the live log — reattach to whatever build is still running. The stream
    // replays the job's backlog before going live, so nothing is missed.
    api.get("/jobs").then((r) => {
      const job = r.data.find((j: any) => j.status === "running" && (j.type === "image" || j.type === "imager"));
      if (job) stream(job.id);
    }).catch(() => {});
  }, []);

  async function stream(id: string) {
    setLog([]); setProgress(null); setRunning(true); setStatus("running"); setJobId(id);
    // Streams are authorized by a short-lived per-job token, not the session JWT.
    const { data } = await api.get(`/jobs/${id}/stream-token`);
    const es = new EventSource(`/api/jobs/${id}/stream?token=${data.token}`);
    esRef.current = es;
    es.onmessage = (e) => setLog((l) => [...l, e.data]);
    es.addEventListener("progress", (e: any) => { try { setProgress(JSON.parse(e.data)); } catch {} });
    es.addEventListener("end", (e: any) => {
      es.close(); setRunning(false); setStatus(e.data);
      e.data === "success" ? toast.success("Build finished") : toast.error(`Build ${e.data}`);
    });
    es.onerror = () => { es.close(); setRunning(false); };
  }

  async function startImage() {
    try { const { data } = await api.post("/builds", opts); await stream(data.id); }
    catch (e) { toast.error(apiError(e)); }
  }
  // The imager is built for the architecture selected above, because it is a
  // kernel the target machine runs: an amd64 imager cannot netboot an arm64
  // machine, so building an arm64 image without one leaves it undeployable.
  async function startImager() {
    try {
      const { data } = await api.post("/imager/build", { arch: opts.arch });
      await stream(data.id);
      loadImagerArches();
    } catch (e) { toast.error(apiError(e)); }
  }
  async function cancel() {
    try { await api.post(`/jobs/${jobId}/cancel`); toast.success("Cancel requested"); }
    catch (e) { toast.error(apiError(e)); }
  }
  const set = (k: string, v: any) => setOpts((o) => ({ ...o, [k]: v }));
  // The builder raises the root slot to a per-distro floor (Ubuntu's kernel
  // hard-depends on linux-firmware + linux-modules-extra, ~1.7 GiB Debian never
  // installs). Mirror that here so the size warning and the note below reflect
  // what will actually be built rather than what was typed.
  const MIN_ROOT: Record<string, number> = { ubuntu: 5120, debian: 2560 };
  const minRoot = MIN_ROOT[opts.distro] ?? 2560;
  const effRoot = Math.max(+opts.root_size || 0, minRoot);
  const rootRaised = effRoot > (+opts.root_size || 0);
  const neededMiB = 2 * effRoot + 512 + 128 + 2 + 256;
  // 0 = auto: the builder picks the smallest size (it expands on first boot).
  const sizeTooSmall = +opts.image_size > 0 && +opts.image_size * 1024 < neededMiB;
  const setDistro = (d: string) => setOpts((o) => ({
    ...o, distro: d, suite: SUITES[d][0].value,
    hostname: o.hostname === `${o.distro}-ab` ? `${d}-ab` : o.hostname,
  }));

  return (
    <div>
      <PageHeader title="Build Image" subtitle="Produce a bootable Debian or Ubuntu A/B image" actions={
        <Button variant="secondary" onClick={startImager} disabled={running}>
          <Cpu size={15} /> Build {opts.arch} netboot imager
        </Button>
      } />
      {/* An arm64 image is undeployable without an arm64 imager, and the only
          symptom is a machine that PXE-boots into nothing — so say it here,
          next to the button that fixes it, rather than leaving it to be found. */}
      {imagerArches && !imagerArches[opts.arch] && (
        <Alert title={`No ${opts.arch} netboot imager has been built`} items={[
          `Machines cannot be imaged over the network for ${opts.arch} until one exists — ` +
          `the imager is a kernel the machine itself runs, so it has to match. ` +
          `Use "Build ${opts.arch} netboot imager" above. Images built here can still be written to a disk directly.`,
        ]} />
      )}
      <Alert title="Builds cannot run yet" items={problems} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          {/* items-end keeps the inputs on a row level with each other when one
              label wraps to a second line and its neighbour does not. */}
          <div className="grid grid-cols-2 items-end gap-3">
            <div><Label>Distribution</Label><Select value={opts.distro} onChange={(e) => setDistro(e.target.value)}><option value="debian">Debian</option><option value="ubuntu">Ubuntu</option></Select></div>
            <div><Label>Release</Label><Select value={opts.suite} onChange={(e) => set("suite", e.target.value)}>{SUITES[opts.distro].map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</Select></div>
            <div>
              <Label>Architecture</Label>
              <Select value={opts.arch} onChange={(e) => set("arch", e.target.value)}>
                <option value="amd64">x86_64 (amd64)</option>
                <option value="arm64">ARM64 (aarch64)</option>
              </Select>
            </div>
            <div><Label>Compression</Label><Select value={opts.compress} onChange={(e) => set("compress", e.target.value)}><option value="zstd">zstd</option><option value="gzip">gzip</option><option value="none">none</option></Select></div>
            {/* Fields are grouped rather than left to flow in source order: the
                two-column grid had put Username and Password diagonally opposite
                each other, and left Root slot size alone in a half-empty row.
                Hostname spans the row so the credentials sit together on theirs
                and the two size fields share the next one. */}
            <div className="col-span-2"><Label>Hostname</Label><Input value={opts.hostname} onChange={(e) => set("hostname", e.target.value)} /></div>
            <div><Label>Username</Label><Input value={opts.username} onChange={(e) => set("username", e.target.value)} /></div>
            <div><Label>Password</Label><Input type="password" value={opts.password} onChange={(e) => set("password", e.target.value)} placeholder="login password" /></div>
            <div><Label>Image size (GiB, 0 = smallest)</Label><Input type="number" min={0} value={opts.image_size} onChange={(e) => set("image_size", +e.target.value)} /></div>
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
            disabled={!opts.password || sizeTooSmall || (opts.encrypt && !opts.luks_passphrase) || (opts.encrypt && opts.unlock === "tang" && !opts.tang_url)}>
            <Hammer size={15} /> {running ? "Building…" : "Start build"}
          </Button>
          {!opts.password && <p className="mt-2 text-xs text-amber-400">Set a login password to enable the build.</p>}
          {sizeTooSmall && <p className="mt-2 text-xs text-amber-400">
            Image too small: two {effRoot} MiB root slots + boot + overlay need ≈{Math.ceil(neededMiB / 1024)} GiB.
          </p>}
          {rootRaised && <p className="mt-2 text-xs text-zinc-500">
            Root slot will be raised to {effRoot} MiB — {opts.distro === "ubuntu" ? "Ubuntu" : "this distribution"}
            {" "}needs it for the kernel and firmware. Expect a ≈{Math.ceil(neededMiB / 1024)} GiB image.
          </p>}
        </Card>
        <Card className="p-5">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Build log</h2>
            <div className="flex items-center gap-2">
              {running && <Button variant="danger" size="sm" onClick={cancel}><XCircle size={13} /> Cancel</Button>}
              {status && <Badge color={status === "success" ? "green" : status === "running" ? "amber" : "red"}>{status}</Badge>}
            </div>
          </div>
          {progress && <ProgressBar {...progress} />}
          <LogView lines={log} />
        </Card>
      </div>
    </div>
  );
}
