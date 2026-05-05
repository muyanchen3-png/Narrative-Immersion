import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import AssetLibraryPage from "./pages/AssetLibraryPage";
import ConfigPage from "./pages/ConfigPage";
import HomePage from "./pages/HomePage";
import JobsPage from "./pages/JobsPage";
import UploadPage from "./pages/UploadPage";
import WatchPage from "./pages/WatchPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/watch/:videoId" element={<WatchPage />} />
        <Route path="/library" element={<AssetLibraryPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/config" element={<ConfigPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
