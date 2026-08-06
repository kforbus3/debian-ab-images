import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./lib/auth";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Build from "./pages/Build";
import Images from "./pages/Images";
import Files from "./pages/Files";
import Jobs from "./pages/Jobs";
import Provisioning from "./pages/Provisioning";
import Imaging from "./pages/Imaging";
import Fleet from "./pages/Fleet";
import Updates from "./pages/Updates";
import Secrets from "./pages/Secrets";

function Protected({ children }: { children: JSX.Element }) {
  const { authed, loading } = useAuth();
  if (loading) return <Spinner />;
  if (!authed) return <Navigate to="/login" replace />;
  return <Layout>{children}</Layout>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Protected><Dashboard /></Protected>} />
      <Route path="/build" element={<Protected><Build /></Protected>} />
      <Route path="/images" element={<Protected><Images /></Protected>} />
      <Route path="/files" element={<Protected><Files /></Protected>} />
      <Route path="/jobs" element={<Protected><Jobs /></Protected>} />
      <Route path="/provisioning" element={<Protected><Provisioning /></Protected>} />
      <Route path="/imaging" element={<Protected><Imaging /></Protected>} />
      <Route path="/fleet" element={<Protected><Fleet /></Protected>} />
      <Route path="/updates" element={<Protected><Updates /></Protected>} />
      <Route path="/secrets" element={<Protected><Secrets /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
