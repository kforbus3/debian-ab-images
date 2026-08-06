import { useEffect, useRef, useState } from "react";
import { Hammer, Cpu, XCircle } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, LogView, Badge, Alert, ProgressBar } from "../components/ui";

// One list, used for the Architecture selector and the imager buttons, so the
// two can never drift apart.
const ARCHES = [
  { value: "amd64", label: "x86_64" },
  { value: "arm64", label: "ARM64" },
];

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
    store_passphrase: false,
    run_script: "", own_paths: "",
  });
  const [store, setStore] = useState<{ configured: boolean; provider: string } | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [jobId, setJobId] = useState<string>("");
  const [problems, setProblems] = useState<string[]>([]);
  const [progress, setProgress] = useState<{ step: number; total: number; label: string } | null>(null);
  const [imagerArches, setImagerArches] = useState<Record<string, boolean> | null>(null);
  const [overlay, setOverlay] = useState<{ files: { path: string; size: number }[]; dir: string } | null>(null);
  const [customOpen, setCustomOpen] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const loadImagerArches = () =>
    api.get("/images").then((r) => setImagerArches(r.data.imager_arches || null)).catch(() => {});

  useEffect(() => () => esRef.current?.close(), []);
  useEffect(() => {
    api.get("/preflight").then((r) => setProblems(r.data.problems)).catch(() => {});
    loadImagerArches();
    api.get("/overlay").then((r) => setOverlay(r.data)).catch(() => {});
    api.get("/secrets/config")
      .then((r) => setStore({ configured: r.data.configured, provider: r.data.config.provider }))
      .catch(() => setStore({ configured: false, provider: "" }));
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
    try {
      const { data } = await api.post("/builds", opts);
      // Said once, at the moment it becomes true. The passphrase is written
      // before the build starts, so this is already a fact rather than a plan.
      if (data.passphrase_stored_at) toast.success(`Passphrase stored at ${data.passphrase_stored_at}`);
      await stream(data.id);
    } catch (e) { toast.error(apiError(e)); }
  }
  // The imager is built for the architecture selected above, because it is a
  // kernel the target machine runs: an amd64 imager cannot netboot an arm64
  // machine, so building an arm64 image without one leaves it undeployable.
  async function startImager(arch: string) {
    try {
      const { data } = await api.post("/imager/build", { arch });
      await stream(data.id);
      loadImagerArches();
    } catch (e) { toast.error(apiError(e)); }
  }
  async function cancel() {
    try { await api.post(`/jobs/${jobId}/cancel`); toast.success("Cancel requested"); }
    catch (e) { toast.error(apiError(e)); }
  }
  const set = (k: string, v: any) => setOpts((o) => ({ ...o, [k]: v }));
  // Generating and storing is the right default wherever the passphrase is only
  // ever a recovery key — which is every unlock method except the one that
  // prompts for it at every boot.
  const generatable = (unlock: string) => !!store?.configured && unlock !== "passphrase";
  const setEncrypt = (on: boolean) =>
    setOpts((o) => ({ ...o, encrypt: on, store_passphrase: on && generatable(o.unlock) }));
  const setUnlock = (unlock: string) =>
    setOpts((o) => ({ ...o, unlock, store_passphrase: generatable(unlock) }));
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
      {/* Both architectures are listed whether or not either is built. A single
          button following the Architecture selector further down the form read
          as "this app only does amd64", because that is the default and nothing
          on screen suggested otherwise. */}
      <PageHeader title="Build Image" subtitle="Produce a bootable Debian or Ubuntu A/B image" actions={
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500">Netboot imager:</span>
          {ARCHES.map((a) => {
            const built = imagerArches?.[a.value];
            return (
              <Button key={a.value} variant="secondary" size="sm" disabled={running}
                      onClick={() => startImager(a.value)}
                      title={built ? `Rebuild the ${a.label} imager` : `Build the ${a.label} imager`}>
                <Cpu size={13} />
                {a.label}
                <span className={built ? "text-emerald-400" : "text-zinc-500"}>
                  {built ? "built" : "not built"}
                </span>
              </Button>
            );
          })}
        </div>
      } />
      {/* An image is undeployable without an imager of the same architecture,
          and the only symptom is a machine that PXE-boots into nothing. */}
      {imagerArches && !imagerArches[opts.arch] && (
        <Alert title={`No ${opts.arch} netboot imager has been built`} items={[
          `Machines cannot be imaged over the network for ${opts.arch} until one exists — ` +
          `the imager is a kernel the machine itself runs, so it has to match. ` +
          `Build it with the ${opts.arch} button above; it can be built on this server ` +
          `whatever architecture the server itself is. Images built here can still be ` +
          `written to a disk directly in the meantime.`,
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
                {ARCHES.map((a) => (
                  <option key={a.value} value={a.value}>{a.label} ({a.value})</option>
                ))}
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
            <button type="button" onClick={() => setCustomOpen((v) => !v)}
              className="flex w-full items-center justify-between text-sm font-medium text-zinc-200">
              <span>Customize the filesystem</span>
              <span className="text-xs text-zinc-500">
                {overlay && overlay.files.length > 0 ? `${overlay.files.length} file(s) staged` : "optional"}
                {customOpen ? " \u25be" : " \u25b8"}
              </span>
            </button>

            {customOpen && (
              <div className="mt-3 space-y-4">
                <div>
                  <Label>Files copied into the image</Label>
                  {overlay && overlay.files.length > 0 ? (
                    <ul className="max-h-32 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-2 font-mono text-xs text-zinc-300">
                      {overlay.files.map((f) => <li key={f.path}>{f.path}</li>)}
                    </ul>
                  ) : (
                    <p className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-500">
                      Nothing staged. Put files under{" "}
                      <span className="text-zinc-300">{overlay?.dir || "overlay.d"}</span>{" "}
                      and they are copied over the image root, keeping their paths.
                    </p>
                  )}
                  {/* The shadowing rule is the surprising part, so it is stated
                      where the files are, not only in the documentation. */}
                  <p className="mt-2 text-xs text-zinc-500">
                    A file here replaces the machine's own copy at the same path on the
                    update that delivers it. Other files in the same directory are left alone.
                  </p>
                </div>

                <div>
                  <Label>Also let the image own these paths</Label>
                  <Input value={opts.own_paths} onChange={(e) => set("own_paths", e.target.value)}
                         placeholder="/etc/hosts /etc/resolv.conf" />
                  <p className="mt-1 text-xs text-zinc-500">
                    Space-separated, for paths you are not shipping a file for but still
                    want the image to win.
                  </p>
                </div>

                <div>
                  <Label>Run inside the image after packages are installed</Label>
                  <textarea
                    value={opts.run_script}
                    onChange={(e) => set("run_script", e.target.value)}
                    spellCheck={false}
                    rows={6}
                    placeholder={"systemctl enable my-agent\nusermod -aG dialout admin"}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs outline-none placeholder:text-zinc-600 focus:border-brand-500"
                  />
                  <p className="mt-1 text-xs text-zinc-500">
                    Runs as root in a chroot, so <code>systemctl enable</code> works but
                    starting a service does not \u2014 there is no running system yet. A
                    non-zero exit fails the build.
                  </p>
                </div>
              </div>
            )}
          </div>

          <div className="mt-4 border-t border-zinc-800 pt-4">
            <label className="flex items-center gap-2 text-sm font-medium text-zinc-200">
              <input type="checkbox" checked={opts.encrypt} onChange={(e) => setEncrypt(e.target.checked)} />
              Encrypt disk (LUKS2)
            </label>
            {opts.encrypt && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div><Label>Auto-unlock method</Label><Select value={opts.unlock} onChange={(e) => setUnlock(e.target.value)}>
                  <option value="tpm2">TPM2 (recommended)</option>
                  <option value="tang">Tang / NBDE (network)</option>
                  <option value="keyfile">Keyfile (universal, weaker)</option>
                  <option value="passphrase">Passphrase (no auto-unlock)</option>
                </Select></div>
                <div><Label>LUKS passphrase (recovery)</Label><Input type="password" value={opts.luks_passphrase} onChange={(e) => set("luks_passphrase", e.target.value)} placeholder={opts.store_passphrase ? "generated" : "required"} disabled={opts.store_passphrase} /></div>
                {opts.unlock === "tang" && <div className="col-span-2"><Label>Tang server URL</Label><Input value={opts.tang_url} onChange={(e) => set("tang_url", e.target.value)} placeholder="http://tang.lan:7500" /></div>}
                {/* Offered for every unlock method, but defaulted on only where
                    the passphrase is pure recovery material. Under
                    unlock=passphrase somebody types it at every boot, and a
                    43-character random string is the wrong answer for that. */}
                {store?.configured && (
                  <label className="col-span-2 flex items-start gap-2 text-sm text-zinc-300">
                    <input type="checkbox" className="mt-0.5" checked={opts.store_passphrase}
                           onChange={(e) => set("store_passphrase", e.target.checked)} />
                    <span>
                      Generate a random passphrase and store it in {store.provider === "vault" ? "Vault" : "OpenBao"}
                      <span className="block text-xs text-zinc-500">
                        Filed under this image's name before the build starts. If the store
                        will not take it, nothing is built.
                        {opts.unlock === "passphrase" && " This one is typed at every boot — a generated passphrase makes that painful."}
                      </span>
                    </span>
                  </label>
                )}
                {store && !store.configured && (
                  <p className="col-span-2 text-xs text-zinc-500">
                    Configure a secrets manager to have this passphrase generated and kept
                    for you instead of typed here.
                  </p>
                )}
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
            disabled={!opts.password || sizeTooSmall || (opts.encrypt && !opts.luks_passphrase && !opts.store_passphrase) || (opts.encrypt && opts.unlock === "tang" && !opts.tang_url)}>
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
