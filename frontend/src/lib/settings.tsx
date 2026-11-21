// App-wide state: which store, the test lab switches, the technical details switch,
// server status and the conversation list.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Conversation, type Setup, type Shop, type Status } from "./api";

interface Settings {
  setup: Setup | null;
  status: Status | null;
  shop: Shop | null;
  userId: string;
  setUserId: (id: string) => void;
  monitorOn: boolean;
  setMonitorOn: (on: boolean) => void;
  fault: string | null;
  setFault: (fault: string | null) => void;
  technical: boolean;
  setTechnical: (on: boolean) => void;
  conversations: Conversation[];
  refreshConversations: () => void;
}

// The technical switch is remembered in this browser only. Storage can be blocked, so both calls are guarded.
const TECH_KEY = "querymend.technical";
// A link ending in #how opens with the details on, for sharing the full trail of one answer.
const readTechnical = () => {
  if (window.location.hash === "#how") return true;
  try { return localStorage.getItem(TECH_KEY) === "1"; } catch { return false; }
};
const saveTechnical = (on: boolean) => { try { localStorage.setItem(TECH_KEY, on ? "1" : "0"); } catch { /* not saved */ } };

const SettingsContext = createContext<Settings | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [setup, setSetup] = useState<Setup | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [shop, setShop] = useState<Shop | null>(null);
  const [userId, setUserId] = useState("owner_pune");
  const [monitorOn, setMonitorOn] = useState(true);
  const [fault, setFault] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [technical, setTechnicalState] = useState(readTechnical);
  const setTechnical = (on: boolean) => { setTechnicalState(on); saveTechnical(on); };

  const refreshConversations = useCallback(() => {
    api.conversations().then(setConversations).catch(() => setConversations([]));
  }, []);

  useEffect(() => {
    api.setup().then(setSetup).catch(() => setSetup(null));
    api.shop().then(setShop).catch(() => setShop(null));
    refreshConversations();
    const check = () => api.status().then(setStatus).catch(() => setStatus(null));
    check();
    const timer = setInterval(check, 15000);
    return () => clearInterval(timer);
  }, [refreshConversations]);

  return (
    <SettingsContext.Provider value={{ setup, status, shop, userId, setUserId, monitorOn, setMonitorOn,
      fault, setFault, technical, setTechnical, conversations, refreshConversations }}>
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings() {
  const value = useContext(SettingsContext);
  if (!value) throw new Error("useSettings must be used inside SettingsProvider");
  return value;
}
