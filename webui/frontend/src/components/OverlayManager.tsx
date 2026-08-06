import { useEffect, useRef, useState } from "react";
import { FilePlus, Upload, Trash2, Pencil, Download, RefreshCw, Save, X, CornerUpRight } from "lucide-react";
import { api, apiError, fmtBytes } from "../lib/api";
import { useToast } from "./Toast";
import { Button, Input, Label, Badge, Alert } from "./ui";

export type OverlayFile = {
  path: string; size: number; mode: string; executable: boolean; modified: string;
};

type Editing = { path: string; original: string; content: string; mode: string; isNew: boolean };

// Paths that need the executable bit far more often than not. Getting this
// wrong is quiet: cp -a preserves the mode, so a 0644 script lands on the
// machine and simply does not run.
const LIKELY_EXECUTABLE = /^\/(usr\/local\/(s?bin)|usr\/(s?bin)|s?bin|opt\/[^/]+\/bin|etc\/(cron\.(daily|hourly|weekly|monthly)|network\/if-up\.d|initramfs-tools\/(hooks|scripts)))\//;

export default function OverlayManager({ onChange }: { onChange?: (count: number) => void }) {
  const toast = useToast();
  const [files, setFiles] = useState<OverlayFile[] | null>(null);
  const [dir, setDir] = useState("");
  const [readonlyReason, setReadonlyReason] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadPath, setUploadPath] = useState("");
  const uploadRef = useRef<HTMLInputElement>(null);
  // State, not a ref: choosing a file has to reveal the destination-path input,
  // and a ref would not re-render to show it.
  const [pending, setPending] = useState<File | null>(null);

  async function load() {
    try {
      const { data } = await api.get("/overlay");
      setFiles(data.files); setDir(data.dir); setReadonlyReason(data.readonly_reason || "");
      onChange?.(data.files.length);
    } catch (e) { toast.error(apiError(e)); setFiles([]); }
  }
  useEffect(() => { load(); }, []);

  const ro = !!readonlyReason;

  function newFile() {
    setEditing({ path: "", original: "", content: "", mode: "0644", isNew: true });
  }

  async function edit(f: OverlayFile) {
    try {
      const { data } = await api.get("/overlay/file", { params: { path: f.path } });
      if (!data.editable) {
        toast.error(`${f.path} cannot be edited here (${data.reason}) — download it instead`);
        return;
      }
      setEditing({ path: f.path, original: f.path, content: data.content, mode: data.mode, isNew: false });
    } catch (e) { toast.error(apiError(e)); }
  }

  async function save() {
    if (!editing) return;
    const path = editing.path.trim();
    if (!path.startsWith("/")) { toast.error("The path must start with / — it is where the file lands on the machine"); return; }
    setBusy(true);
    try {
      await api.put("/overlay/file", { path, content: editing.content, mode: editing.mode });
      // A rename is a write to the new path plus a delete of the old one; doing
      // it in that order means a failure leaves the original intact.
      if (!editing.isNew && editing.original !== path) {
        await api.delete("/overlay/file", { params: { path: editing.original } });
      }
      toast.success(`Saved ${path}`);
      setEditing(null); await load();
    } catch (e) { toast.error(apiError(e)); }
    finally { setBusy(false); }
  }

  async function remove(f: OverlayFile) {
    if (!confirm(`Remove ${f.path} from future images?`)) return;
    try {
      await api.delete("/overlay/file", { params: { path: f.path } });
      toast.success(`Removed ${f.path}`); await load();
    } catch (e) { toast.error(apiError(e)); }
  }

  async function toggleExec(f: OverlayFile) {
    try {
      await api.put("/overlay/file", { path: f.path, mode: f.executable ? "0644" : "0755" });
      await load();
    } catch (e) { toast.error(apiError(e)); }
  }

  async function download(f: OverlayFile) {
    try {
      const res = await api.get("/overlay/download", { params: { path: f.path }, responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url; a.download = f.path.split("/").pop() || "file"; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error(apiError(e)); }
  }

  function pickUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setPending(file);
    // A bare filename is a guess, not an answer — the destination directory is
    // the part only the operator knows, so it is prefilled and left focused.
    setUploadPath((p) => p || `/${file.name}`);
  }

  async function upload() {
    const file = pending;
    if (!file) { toast.error("Choose a file first"); return; }
    const path = uploadPath.trim();
    if (!path.startsWith("/")) { toast.error("The destination path must start with /"); return; }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("path", path);
      form.append("file", file);
      form.append("executable", String(LIKELY_EXECUTABLE.test(path)));
      await api.post("/overlay/upload", form);
      toast.success(`Uploaded ${path}`);
      setPending(null); setUploadPath("");
      if (uploadRef.current) uploadRef.current.value = "";
      await load();
    } catch (e) { toast.error(apiError(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      {ro && (
        <Alert title="Files cannot be changed from here" items={[
          readonlyReason,
          `They can still be managed on the host, under ${dir}.`,
        ]} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={newFile} disabled={ro}><FilePlus size={13} /> New file</Button>
        <label className={`inline-flex ${ro ? "pointer-events-none opacity-50" : ""}`}>
          <input ref={uploadRef} type="file" className="hidden" onChange={pickUpload} disabled={ro} />
          <span className="inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs font-medium text-zinc-100 transition hover:bg-zinc-700">
            <Upload size={13} /> Choose a file…
          </span>
        </label>
        {pending && (
          <>
            <Input className="max-w-xs" value={uploadPath} onChange={(e) => setUploadPath(e.target.value)}
                   placeholder="/usr/local/bin/tool" />
            <Button size="sm" loading={busy} onClick={upload}><CornerUpRight size={13} /> Upload here</Button>
          </>
        )}
        <Button size="sm" variant="secondary" className="ml-auto" onClick={load}><RefreshCw size={13} /></Button>
      </div>

      {editing && (
        <div className="rounded-lg border border-brand-600/40 bg-zinc-950 p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_7rem]">
            <div>
              <Label>Path on the machine</Label>
              <Input value={editing.path} autoFocus={editing.isNew} spellCheck={false}
                     onChange={(e) => setEditing({ ...editing, path: e.target.value })}
                     placeholder="/etc/netplan/10-corp.yaml" />
            </div>
            <div>
              <Label>Mode</Label>
              <Input value={editing.mode} spellCheck={false}
                     onChange={(e) => setEditing({ ...editing, mode: e.target.value })} placeholder="0644" />
            </div>
          </div>
          <textarea
            value={editing.content} spellCheck={false} rows={14}
            onChange={(e) => setEditing({ ...editing, content: e.target.value })}
            placeholder={"network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true"}
            className="mt-3 w-full rounded-lg border border-zinc-700 bg-black px-3 py-2 font-mono text-xs leading-relaxed outline-none placeholder:text-zinc-600 focus:border-brand-500"
          />
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" loading={busy} onClick={save}><Save size={13} /> Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}><X size={13} /> Cancel</Button>
            {!editing.isNew && editing.original !== editing.path.trim() && (
              <span className="text-xs text-amber-400">Saving moves it from {editing.original}</span>
            )}
            {/* Said here rather than in the docs, because the mode is invisible
                once the file is on the machine and wrong is silent. */}
            {LIKELY_EXECUTABLE.test(editing.path) && !/[1357]$/.test(editing.mode) && (
              <span className="text-xs text-amber-400">This looks like a program — 0755 makes it runnable</span>
            )}
          </div>
        </div>
      )}

      {files === null ? (
        <p className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-500">Loading…</p>
      ) : files.length === 0 ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-4 text-xs text-zinc-500">
          <p className="text-zinc-400">Nothing staged.</p>
          <p className="mt-1">
            Add a file above and it is copied over the image root, keeping its path —
            <code className="mx-1 text-zinc-300">/etc/hosts</code> here becomes
            <code className="mx-1 text-zinc-300">/etc/hosts</code> on every machine imaged from it.
          </p>
        </div>
      ) : (
        <ul className="divide-y divide-zinc-800/70 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950">
          {files.map((f) => (
            <li key={f.path} className="flex items-center gap-3 px-3 py-2 hover:bg-zinc-900/60">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-xs text-zinc-200">{f.path}</span>
                  {f.executable && <Badge color="brand">exec</Badge>}
                </div>
                <div className="mt-0.5 text-xs text-zinc-500">
                  {fmtBytes(f.size)} · {f.mode} · {new Date(f.modified).toLocaleString()}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button size="sm" variant="ghost" title="Edit" onClick={() => edit(f)} disabled={ro}><Pencil size={13} /></Button>
                <Button size="sm" variant="ghost" title={f.executable ? "Make it 0644" : "Make it executable"}
                        onClick={() => toggleExec(f)} disabled={ro}>
                  <span className="font-mono text-xs">{f.executable ? "0644" : "0755"}</span>
                </Button>
                <Button size="sm" variant="ghost" title="Download" onClick={() => download(f)}><Download size={13} /></Button>
                <Button size="sm" variant="ghost" title="Remove" onClick={() => remove(f)} disabled={ro}>
                  <Trash2 size={13} className="text-red-400" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* The shadowing rule is the surprising part, so it is stated where the
          files are, not only in the documentation. */}
      <p className="text-xs text-zinc-500">
        A file here replaces the machine's own copy at the same path on the update that
        delivers it. Other files in the same directory are left alone.
      </p>
    </div>
  );
}
