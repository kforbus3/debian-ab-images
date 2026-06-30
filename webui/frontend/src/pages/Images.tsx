import { useEffect, useState } from "react";
import { HardDrive, Download, Trash2, RefreshCw } from "lucide-react";
import { api, apiError, fmtBytes } from "../lib/api";
import { useToast } from "../components/Toast";
import { Button, Card, PageHeader, Spinner, Badge } from "../components/ui";

interface Img { name: string; size: number; created: string; }

export default function Images() {
  const toast = useToast();
  const [images, setImages] = useState<Img[]>([]);
  const [imagerReady, setImagerReady] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try { const { data } = await api.get("/images"); setImages(data.images); setImagerReady(data.imager_ready); }
    catch (e) { toast.error(apiError(e)); } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);

  async function remove(name: string) {
    if (!confirm(`Delete ${name}?`)) return;
    try { await api.delete(`/images/${encodeURIComponent(name)}`); toast.success("Deleted"); load(); }
    catch (e) { toast.error(apiError(e)); }
  }
  async function download(name: string) {
    try {
      const res = await api.get(`/images/${encodeURIComponent(name)}/download`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
    } catch (e) { toast.error(apiError(e)); }
  }

  return (
    <div>
      <PageHeader title="Images" subtitle="Built A/B disk images" actions={
        <>
          <Badge color={imagerReady ? "green" : "amber"}>imager {imagerReady ? "ready" : "missing"}</Badge>
          <Button variant="secondary" size="sm" onClick={load}><RefreshCw size={13} /> Refresh</Button>
        </>
      } />
      <Card>
        {loading ? <Spinner /> : images.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center text-zinc-500">
            <HardDrive size={32} /><p className="text-sm">No images yet — build one from the Build page.</p>
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead><tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
              <th className="px-4 py-2.5 font-medium">Name</th><th className="px-4 py-2.5 font-medium">Size</th>
              <th className="px-4 py-2.5 font-medium">Created</th><th className="px-4 py-2.5"></th>
            </tr></thead>
            <tbody className="divide-y divide-zinc-800/70">
              {images.map((m) => (
                <tr key={m.name} className="hover:bg-zinc-800/40">
                  <td className="px-4 py-3 font-medium text-zinc-200">{m.name}</td>
                  <td className="px-4 py-3 text-zinc-400">{fmtBytes(m.size)}</td>
                  <td className="px-4 py-3 text-zinc-400">{new Date(m.created).toLocaleString()}</td>
                  <td className="px-4 py-3"><div className="flex justify-end gap-1">
                    <Button size="sm" variant="secondary" onClick={() => download(m.name)}><Download size={13} /> Download</Button>
                    <Button size="sm" variant="ghost" onClick={() => remove(m.name)}><Trash2 size={14} className="text-red-400" /></Button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
