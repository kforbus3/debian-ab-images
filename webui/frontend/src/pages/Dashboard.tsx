import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { HardDrive, Cpu, Network, Hammer } from "lucide-react";
import { api } from "../lib/api";
import { Card, PageHeader, Badge } from "../components/ui";

export default function Dashboard() {
  const [images, setImages] = useState<any[]>([]);
  const [imagerReady, setImagerReady] = useState(false);
  const [server, setServer] = useState<{ running: boolean } | null>(null);

  useEffect(() => {
    api.get("/images").then((r) => { setImages(r.data.images); setImagerReady(r.data.imager_ready); }).catch(() => {});
    api.get("/server/status").then((r) => setServer(r.data)).catch(() => setServer({ running: false }));
  }, []);

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Build images and provision machines over the network" />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <Card className="p-5">
          <div className="flex items-center justify-between"><HardDrive className="text-brand-400" size={22} /><span className="text-3xl font-semibold">{images.length}</span></div>
          <p className="mt-2 text-sm text-zinc-400">Built images</p>
        </Card>
        <Card className="p-5">
          <div className="flex items-center justify-between"><Cpu className="text-sky-400" size={22} />{imagerReady ? <Badge color="green">ready</Badge> : <Badge color="amber">not built</Badge>}</div>
          <p className="mt-2 text-sm text-zinc-400">Netboot imager</p>
        </Card>
        <Card className="p-5">
          <div className="flex items-center justify-between"><Network className="text-emerald-400" size={22} />{server?.running ? <Badge color="green">running</Badge> : <Badge color="zinc">stopped</Badge>}</div>
          <p className="mt-2 text-sm text-zinc-400">Provisioning server</p>
        </Card>
      </div>
      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        <Link to="/build"><Card className="flex items-center gap-3 p-5 hover:border-brand-500/50"><Hammer className="text-brand-400" /><div><p className="font-medium">Build a new image</p><p className="text-xs text-zinc-500">Configure and watch the build live</p></div></Card></Link>
        <Link to="/provisioning"><Card className="flex items-center gap-3 p-5 hover:border-brand-500/50"><Network className="text-emerald-400" /><div><p className="font-medium">Provision machines</p><p className="text-xs text-zinc-500">Start the PXE server and monitor imaging</p></div></Card></Link>
      </div>
    </div>
  );
}
