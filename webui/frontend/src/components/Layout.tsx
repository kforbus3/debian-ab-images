import { ReactNode, useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { LayoutDashboard, Hammer, HardDrive, ListChecks, Network, LogOut, Boxes, MonitorSmartphone, Server, Package } from "lucide-react";
import { useAuth } from "../lib/auth";
import { api } from "../lib/api";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/build", label: "Build Image", icon: Hammer },
  { to: "/images", label: "Images", icon: HardDrive },
  { to: "/jobs", label: "Jobs", icon: ListChecks },
  { to: "/provisioning", label: "Provisioning", icon: Network },
  { to: "/imaging", label: "Imaging", icon: MonitorSmartphone },
  { to: "/fleet", label: "Fleet", icon: Server },
  { to: "/updates", label: "Updates", icon: Package },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const navigate = useNavigate();

  // Machines being imaged are the one thing happening away from the screen, so
  // the count follows you across every page rather than only the Imaging one.
  const [imaging, setImaging] = useState(0);
  useEffect(() => {
    const poll = () => api.get("/imaging").then((r) => setImaging(r.data.active)).catch(() => {});
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/40">
        <div className="flex items-center gap-2 px-5 py-4">
          <Boxes className="text-brand-400" /><span className="font-semibold tracking-tight">A/B Images</span>
        </div>
        <nav className="flex-1 space-y-0.5 px-3 py-2">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${isActive ? "bg-brand-600/20 text-brand-400" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"}`}>
              <n.icon size={17} />{n.label}
              {n.to === "/imaging" && imaging > 0 && (
                <span className="ml-auto inline-flex min-w-[1.25rem] justify-center rounded-full bg-brand-500/20 px-1.5 py-0.5 text-xs font-medium tabular-nums text-brand-300">
                  {imaging}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <button onClick={() => { logout(); navigate("/login"); }}
          className="m-3 flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-red-300">
          <LogOut size={17} /> Log out
        </button>
      </aside>
      <main className="flex-1 overflow-y-auto px-6 py-6">{children}</main>
    </div>
  );
}
