import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "@fontsource/figtree/400.css";
import "@fontsource/figtree/500.css";
import "@fontsource/figtree/600.css";
import "@fontsource/figtree/700.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { SettingsProvider } from "./lib/settings";
import { Sidebar } from "./components/Sidebar";
import { UploadDialog } from "./components/UploadDialog";
import { Ask } from "./pages/Ask";
import { Failures } from "./pages/Failures";
import { Quality } from "./pages/Quality";
import { Glossary } from "./pages/Glossary";
import { Tour } from "./pages/Tour";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <SettingsProvider>
        <div className="shell">
          <Sidebar />
          <main className="main">
            <Routes>
              <Route path="/" element={<Ask />} />
              <Route path="/c/:id" element={<Ask />} />
              <Route path="/failures" element={<Failures />} />
              <Route path="/quality" element={<Quality />} />
              <Route path="/glossary" element={<Glossary />} />
              <Route path="/tour" element={<Tour />} />
              <Route path="*" element={<Ask />} />
            </Routes>
          </main>
          <UploadDialog />
        </div>
      </SettingsProvider>
    </BrowserRouter>
  </StrictMode>,
);
