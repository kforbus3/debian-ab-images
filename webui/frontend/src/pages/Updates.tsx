import { useEffect, useState } from "react";
import { api, apiError, fmtBytes } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, Badge, Spinner, LogView } from "../components/ui";
import { Package, Copy, RefreshCw, KeyRound } from "lucide-react";
import { secretName } from "./Secrets";

type Bundle = {
  name: string; size: number; created: string;
  version?: string; compatible?: string; source?: string; description?: string;
};

export default function Updates() {
  const toast = useToast();
  const [bundles, setBundles] = useState<Bundle[] | null>(null);
  const [running, setRunning] = useState<Record<string, number>>({});
  const [images, setImages] = useState<string[]>([]);
  const [image, setImage] = useState("");
  const [version, setVersion] = useState("");
  const [luks, setLuks] = useState("");
  const [encrypted, setEncrypted] = useState<Record<string, boolean>>({});
  const [inStore, setInStore] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  async function load() {
    try {
      const { data } = await api.get("/bundles");
      setBundles(data.bundles);
      setRunning(data.running_versions || {});
    } catch (e) { toast.error(apiError(e)); }
    try {
      const { data } = await api.get("/images");
      // Compressed images are fine: the builder decompresses first, because it
      // has to mount the root slot out of the image and cannot do that through
      // zstd or gzip. Most images this app produces are compressed, so
      // excluding them here made the update path unreachable for the usual case.
      const imgs = data.images.filter((i: any) => /\.img(\.zst|\.gz)?$/.test(i.name));
      setImages(imgs.map((i: any) => i.name));
      // Encrypted images need their passphrase to be read; the field only
      // appears when the selected image actually requires it.
      setEncrypted(Object.fromEntries(imgs.map((i: any) => [i.name, !!i.meta?.encrypted])));
    } catch { /* the selector simply stays empty */ }
    try {
      // An image built with a generated passphrase already has it filed here,
      // so the prompt below is not just inconvenient -- it asks for something
      // the operator was deliberately never given.
      const { data } = await api.get("/secrets/entries");
      setInStore(new Set<string>(data.entries || []));
    } catch { /* no store configured; the prompt stays */ }
  }

  useEffect(() => { load(); }, []);
  useEffect(() => { if (!image && images.length) setImage(images[0]); }, [images]);

  async function build() {
    setBusy(true); setLog([]);
    try {
      const { data } = await api.post("/bundles/build",
        { image, version, luks_passphrase: encrypted[image] ? luks : undefined });
      toast.success("Building bundle");
      const token = (await api.post(`/jobs/${data.id}/stream-token`)).data.token;
      const es = new EventSource(`/api/jobs/${data.id}/stream?token=${token}`);
      es.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.line) setLog((l) => [...l, m.line]);
        if (m.status && m.status !== "running") {
          es.close(); setBusy(false); load();
          if (m.status === "succeeded") toast.success("Bundle ready");
          else toast.error("Bundle build failed — see the log");
        }
      };
      es.onerror = () => { es.close(); setBusy(false); load(); };
    } catch (e) { toast.error(apiError(e)); setBusy(false); }
  }

  const stored = !!image && inStore.has(secretName(image));

  const copy = (t: string) => {
    navigator.clipboard?.writeText(t);
    toast.success("Copied");
  };

  return (
    <div>
      <PageHeader
        title="Updates"
        subtitle="Patch machines in place instead of re-imaging them"
        actions={<Button variant="secondary" size="sm" onClick={load}><RefreshCw size={13} /></Button>}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-1 text-sm font-semibold">Build an update bundle</h2>
          <p className="mb-4 text-xs text-zinc-500">
            A bundle carries the root filesystem only. Installing it writes the slot the
            machine is <em>not</em> running on, then reboots into it — if that slot fails to
            come up, GRUB falls back to the one that was working. The overlay is untouched,
            so /home and machine configuration survive the update.
          </p>

          <div className="grid grid-cols-2 items-end gap-3">
            <div className="col-span-2">
              <Label>Source image</Label>
              <Select value={image} onChange={(e) => setImage(e.target.value)}>
                {images.length === 0 && <option value="">No images available</option>}
                {images.map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </div>
            <div className="col-span-2">
              <Label>Version (optional — defaults to a timestamp)</Label>
              <Input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="2026.08.03" />
            </div>
          </div>

          {encrypted[image] && stored && (
            <p className="mt-3 flex items-start gap-1.5 text-xs text-emerald-400">
              <KeyRound size={13} className="mt-0.5 shrink-0" />
              <span>
                The secrets manager holds this image's passphrase — it is read from there
                to open the root slot, and never appears in the bundle.
              </span>
            </p>
          )}

          {encrypted[image] && !stored && (
            <div className="mt-3">
              <Label>LUKS passphrase for {image}</Label>
              <Input type="password" value={luks} onChange={(e) => setLuks(e.target.value)}
                     placeholder="required to read the encrypted root slot" />
              <p className="mt-1 text-xs text-zinc-500">
                Used only while building. The bundle carries a plain filesystem and
                installs on encrypted and unencrypted machines alike.
              </p>
            </div>
          )}

          <Button className="mt-4" loading={busy}
                  disabled={!image || (encrypted[image] && !stored && !luks)} onClick={build}>
            <Package size={14} /> Build bundle
          </Button>

          {image && /\.(zst|gz)$/.test(image) && (
            <p className="mt-3 text-xs text-zinc-500">
              This image is compressed; the builder decompresses it first, which adds a
              few minutes and needs room for the expanded image.
            </p>
          )}

          {log.length > 0 && <div className="mt-4"><LogView lines={log} /></div>}
        </Card>

        <Card className="p-5">
          <h2 className="mb-1 text-sm font-semibold">Installing an update</h2>
          <p className="mb-3 text-xs text-zinc-500">
            On the machine, as root. It downloads the bundle, writes the inactive slot and
            leaves the running system alone until you reboot.
          </p>
          <div className="rounded-lg border border-zinc-800 bg-black p-3 font-mono text-xs text-zinc-300">
            <div className="flex items-start justify-between gap-2">
              <span>ab-update</span>
              <button onClick={() => copy("ab-update")} className="text-zinc-500 hover:text-zinc-300">
                <Copy size={12} />
              </button>
            </div>
            <p className="mt-2 text-zinc-600">
              # or a specific bundle:
              <br />ab-update http://&lt;server&gt;/bundles/&lt;name&gt;.raucb
              <br /># then: systemctl reboot
            </p>
          </div>
          <p className="mt-3 text-xs text-zinc-500">
            A machine only accepts bundles signed by the certificate baked into its image
            when it was built. Images built before the first bundle have no such certificate
            and must be rebuilt once before they can be updated.
          </p>
        </Card>
      </div>

      <Card className="mt-6">
        <div className="flex items-center gap-2 border-b border-zinc-800 px-5 py-3">
          <h2 className="text-sm font-semibold text-zinc-200">Bundles</h2>
          {bundles && bundles.length > 0 && <Badge color="brand">{bundles.length}</Badge>}
        </div>
        {bundles === null ? <Spinner /> : bundles.length === 0 ? (
          <div className="py-12 text-center">
            <Package className="mx-auto text-zinc-700" size={28} />
            <p className="mt-3 text-sm text-zinc-400">No update bundles yet.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-800 text-xs uppercase text-zinc-500">
                  <th className="px-5 py-2">Bundle</th><th className="px-3 py-2">Version</th>
                  <th className="px-3 py-2">Compatible</th><th className="px-3 py-2">Size</th>
                  <th className="px-3 py-2">Built</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70">
                {bundles.map((b) => (
                  <tr key={b.name}>
                    <td className="px-5 py-2.5 font-mono text-xs text-zinc-300">{b.name}</td>
                    <td className="px-3 py-2.5 text-zinc-400">{b.version || "—"}</td>
                    <td className="px-3 py-2.5 text-xs text-zinc-500">{b.compatible || "—"}</td>
                    <td className="px-3 py-2.5 tabular-nums text-zinc-400">{fmtBytes(b.size)}</td>
                    <td className="px-3 py-2.5 text-xs text-zinc-500">{b.created.replace("T", " ").replace("+00:00", "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {Object.keys(running).length > 0 && (
        <Card className="mt-6 p-5">
          <h2 className="mb-3 text-sm font-semibold">What the fleet is running</h2>
          <div className="flex flex-wrap gap-2">
            {Object.entries(running).map(([v, n]) => (
              <Badge key={v} color="green">{v} · {n}</Badge>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
