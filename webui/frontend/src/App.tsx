import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./lib/auth";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Build from "./pages/Build";
import Images from "./pages/Images";
import Provisioning from "./pages/Provisioning";

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
      <Route path="/provisioning" element={<Protected><Provisioning /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
