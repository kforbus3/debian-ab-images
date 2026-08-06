import { useEffect, useState } from "react";
import { KeyRound, PlugZap, Save, Eye, RefreshCw } from "lucide-react";
import { api, apiError } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, Input, Label, Select, PageHeader, Badge, Alert, Spinner } from "../components/ui";

type Config = {
  enabled: boolean;
  provider: string;
  address: string;
  mount: string;
  path_prefix: string;
  namespace: string;
  auth_method: string;
  role_id: string;
  ca_cert: string;
  tls_skip_verify: boolean;
  token_set?: boolean;
  secret_id_set?: boolean;
};

// Strips the compression suffix, matching secretstore.secret_name on the
// backend: `foo.img.zst` and `foo.img` are the same image and must resolve to
// the same store entry, or the badge below lies about every compressed build.
export const secretName = (image: string) => image.replace(/\.(zst|gz)$/, "");

export default function Secrets() {
  const toast = useToast();
  const [cfg, setCfg] = useState<Config | null>(null);
  const [token, setToken] = useState("");
  const [secretId, setSecretId] = useState("");
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [entries, setEntries] = useState<string[]>([]);
  const [revealed, setRevealed] = useState<Record<string, string>>({});

  const load = () => {
    api.get("/secrets/config").then((r) => setCfg(r.data.config)).catch((e) => toast.error(apiError(e)));
    api.get("/secrets/entries").then((r) => setEntries(r.data.entries || [])).catch(() => setEntries([]));
  };
  useEffect(load, []);

  const set = (k: keyof Config, v: any) => setCfg((c) => (c ? { ...c, [k]: v } : c));

  // Blank credential fields mean "leave what is saved alone" — the UI is never
  // shown the token it would otherwise be posting back, so sending an empty
  // string as a real value would blank a working configuration on every save.
  const payload = () => ({
    ...cfg,
    ...(token ? { token } : {}),
    ...(secretId ? { secret_id: secretId } : {}),
  });

  async function test() {
    setTesting(true); setResult(null);
    try {
      const { data } = await api.post("/secrets/test", payload());
      if (data.ok) {
        const i = data.info || {};
        const bits = [
          i.version ? `server ${i.version}` : "",
          i.mount ? `KV v2 at '${i.mount}'` : "",
          i.token_policies?.length ? `policies: ${i.token_policies.join(", ")}` : "",
        ].filter(Boolean);
        setResult({ ok: true, text: bits.join(" · ") || "Connected" });
      } else {
        setResult({ ok: false, text: data.error });
      }
    } catch (e) { setResult({ ok: false, text: apiError(e) }); }
    finally { setTesting(false); }
  }

  async function save() {
    setSaving(true);
    try {
      const { data } = await api.put("/secrets/config", payload());
      setCfg(data.config); setToken(""); setSecretId("");
      toast.success(data.config.enabled ? "Secrets manager enabled" : "Settings saved");
      api.get("/secrets/entries").then((r) => setEntries(r.data.entries || [])).catch(() => {});
    } catch (e) { toast.error(apiError(e)); }
    finally { setSaving(false); }
  }

  async function reveal(image: string) {
    try {
      const { data } = await api.get(`/secrets/passphrase/${encodeURIComponent(image)}`);
      setRevealed((r) => ({ ...r, [image]: data.passphrase }));
    } catch (e) { toast.error(apiError(e)); }
  }

  if (!cfg) return <Spinner />;
  const approle = cfg.auth_method === "approle";

  return (
    <div>
      <PageHeader title="Secrets Manager" subtitle="Where generated LUKS passphrases are kept" />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <label className="flex items-center gap-2 text-sm font-medium text-zinc-200">
            <input type="checkbox" checked={cfg.enabled} onChange={(e) => set("enabled", e.target.checked)} />
            Store LUKS passphrases in a secrets manager
          </label>
          <p className="mt-2 text-xs text-zinc-500">
            With this on, an encrypted build can generate its own recovery passphrase and
            file it under the image's name — nobody has to invent one, write it down, or
            produce it again years later when a TPM is cleared and a machine stops at the
            initramfs prompt. The passphrase is written <em>before</em> the build starts;
            if the store cannot take it, no image is produced.
          </p>

          <div className="mt-4 grid grid-cols-2 items-end gap-3">
            <div>
              <Label>Provider</Label>
              <Select value={cfg.provider} onChange={(e) => set("provider", e.target.value)}>
                <option value="openbao">OpenBao</option>
                <option value="vault">HashiCorp Vault</option>
              </Select>
            </div>
            <div>
              <Label>Authentication</Label>
              <Select value={cfg.auth_method} onChange={(e) => set("auth_method", e.target.value)}>
                <option value="token">Token</option>
                <option value="approle">AppRole</option>
              </Select>
            </div>
            <div className="col-span-2">
              <Label>Address</Label>
              <Input value={cfg.address} onChange={(e) => set("address", e.target.value)}
                     placeholder="https://bao.example.lan:8200" />
            </div>

            {!approle && (
              <div className="col-span-2">
                <Label>Token</Label>
                <Input type="password" value={token} onChange={(e) => setToken(e.target.value)}
                       placeholder={cfg.token_set ? "•••••••• (saved — leave blank to keep)" : "hvs.… / s.…"} />
                <p className="mt-1 text-xs text-zinc-500">
                  Or leave it empty and set <code>BAO_TOKEN</code> in <code>webui/.env</code>,
                  which takes precedence and never lands in a file here.
                </p>
              </div>
            )}
            {approle && (
              <>
                <div>
                  <Label>Role ID</Label>
                  <Input value={cfg.role_id || ""} onChange={(e) => set("role_id", e.target.value)} />
                </div>
                <div>
                  <Label>Secret ID</Label>
                  <Input type="password" value={secretId} onChange={(e) => setSecretId(e.target.value)}
                         placeholder={cfg.secret_id_set ? "•••••••• (saved)" : ""} />
                </div>
              </>
            )}

            <div>
              <Label>KV v2 mount</Label>
              <Input value={cfg.mount} onChange={(e) => set("mount", e.target.value)} placeholder="secret" />
            </div>
            <div>
              <Label>Path prefix</Label>
              <Input value={cfg.path_prefix} onChange={(e) => set("path_prefix", e.target.value)}
                     placeholder="debian-ab-images" />
            </div>
            <div className="col-span-2">
              <Label>Namespace (optional)</Label>
              <Input value={cfg.namespace} onChange={(e) => set("namespace", e.target.value)}
                     placeholder="admin/team — Vault Enterprise or HCP only" />
            </div>
            <div className="col-span-2">
              <Label>CA certificate (optional)</Label>
              <textarea
                value={cfg.ca_cert} onChange={(e) => set("ca_cert", e.target.value)}
                spellCheck={false} rows={4}
                placeholder={"-----BEGIN CERTIFICATE-----\n…"}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs outline-none placeholder:text-zinc-600 focus:border-brand-500"
              />
              <p className="mt-1 text-xs text-zinc-500">
                Needed when the store uses a private CA this container does not already trust.
              </p>
            </div>
            <label className="col-span-2 flex items-center gap-2 text-sm text-zinc-300">
              <input type="checkbox" checked={cfg.tls_skip_verify}
                     onChange={(e) => set("tls_skip_verify", e.target.checked)} />
              Skip TLS verification
              <span className="text-xs text-amber-400">— the passphrase is sent over this connection</span>
            </label>
          </div>

          <div className="mt-4 flex gap-2">
            <Button variant="secondary" loading={testing} onClick={test}>
              <PlugZap size={15} /> Test connection
            </Button>
            <Button loading={saving} onClick={save} disabled={cfg.enabled && !cfg.address}>
              <Save size={15} /> Save
            </Button>
          </div>
          {result && (
            <p className={`mt-3 text-xs ${result.ok ? "text-emerald-400" : "text-amber-400"}`}>
              {result.ok ? "Connected — " : "Failed — "}{result.text}
            </p>
          )}
          {/* Enabling is gated on a working connection, so this is the likelier
              mistake by far: a store that answers, and a build that then cannot
              file its passphrase because the policy is read-only. */}
          {cfg.enabled && (
            <Alert title="The credential needs write access, not just read" items={[
              `Builds write to ${cfg.mount || "secret"}/${cfg.path_prefix || ""}/<image>.img and read it back ` +
              `when packaging an update bundle. A read-only policy passes the test above and fails the first build.`,
            ]} />
          )}
        </Card>

        <Card className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Stored passphrases</h2>
            <Button variant="secondary" size="sm" onClick={load}><RefreshCw size={13} /></Button>
          </div>
          {!cfg.enabled ? (
            <p className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-500">
              Nothing yet — enable a store, then build an encrypted image with
              "Generate and store the passphrase".
            </p>
          ) : entries.length === 0 ? (
            <p className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-500">
              The store holds no passphrases under this prefix yet.
            </p>
          ) : (
            <ul className="space-y-2">
              {entries.map((e) => (
                <li key={e} className="rounded-lg border border-zinc-800 bg-zinc-950 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-xs text-zinc-300">{e}</span>
                    <Button variant="secondary" size="sm" onClick={() => reveal(e)}>
                      <Eye size={13} /> Reveal
                    </Button>
                  </div>
                  {revealed[e] && (
                    <code className="mt-2 block break-all rounded bg-zinc-900 px-2 py-1.5 font-mono text-xs text-amber-300">
                      {revealed[e]}
                    </code>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-4 border-t border-zinc-800 pt-4 text-xs text-zinc-500">
            <div className="mb-1 flex items-center gap-1.5 text-zinc-400">
              <KeyRound size={13} /> <span className="font-medium">What this passphrase is</span>
            </div>
            A machine imaged from an encrypted image unlocks from its TPM, from Tang, or
            from a keyfile. The stored passphrase is the recovery slot behind all three —
            it opens any encrypted partition on any machine imaged from that image, which
            is why it is worth keeping and worth keeping somewhere audited.
            {" "}<Badge color="amber">rebuilds overwrite</Badge>{" "}
            Building the same image name again files a new passphrase; KV v2 keeps the old
            version, and machines already deployed still need it.
          </div>
        </Card>
      </div>
    </div>
  );
}
