// Everything the page asks the Python server, in one place.

export type Role = "executor" | "monitor" | "recovery" | "person" | "stop";
export type Outcome = "checked" | "recovered" | "stopped" | "approved" | "masked" | "unchecked" | "verify";

export interface Step { role: Role; text: string; node: string }
export interface Preview { columns: string[]; rows: string[][]; total: number; hidden: number; error?: string }
export interface Stats { executor_steps: number; tool_calls: number; retries: number; seconds: number }

export interface Turn {
  question: string;
  fault: string | null;
  monitor_on: boolean;
  steps: Step[];
  queries: string[];
  results: Preview[];
  answer?: string;
  table?: Preview | null;
  stats?: Stats;
  outcome?: Outcome;
  approval?: string;
  error?: string;
  running?: boolean;
  queued?: boolean;   // other people are asking, so this one waits its turn
}

export interface Store { id: string; name: string; uploaded: boolean }
export interface Setup {
  users: Store[];
  faults: { id: string; name: string }[];
  retry_budget: number;
  public: boolean;   // the online copy, shared with strangers
  keep_hours: number; max_files: number; max_file_mb: number; question_length: number;
}
export interface Status {
  ollama: boolean; chat_model: string; chat_model_ready: boolean;
  embed_model: string; embed_model_ready: boolean; database: boolean;
}
export interface Shop {
  name: string; uploaded: boolean; facts: { label: string; value: string }[]; examples: string[];
  files: string[]; tables: { name: string; rows: number }[];
}
export interface UploadPreview {
  token: string; name: string; files: string[]; questions: string[];
  tables: {
    name: string; source: string; rows: number; notes: string[]; joins: string[];
    columns: { name: string; type: string; personal: boolean }[]; sample: (string | number | null)[][];
  }[];
}
export interface Conversation { id: string; title: string; user_id: string; created_at: string; turns: number }
export interface FailureRow {
  logged_at: string; thread_id: string; agent: string; failure_type: string;
  sql: string | null; error: string | null; action: string; retries_left: number;
}
export interface Failures { counts: { type: string; count: number }[]; rows: FailureRow[] }
export interface Evaluation {
  repeats: number;
  ev: { system: string; case: string; runs: number; outcomes: Record<string, number>; model_calls: number; seconds: number }[];
  injections: { kind: string; runs: number; detected: number; outcomes: Record<string, number>; retries: number }[];
  containment: { monitor_on: boolean; runs: number; in_history: number; turn2_wrong: number; turn1_correct: number; model_calls: number; seconds: number }[];
}
export interface Term { term: string; meaning: string }

// A refused form comes back as a list, which is not meant for people.
async function problem(reply: Response): Promise<string> {
  const detail = (await reply.json().catch(() => null))?.detail;
  if (typeof detail === "string") return detail;
  return reply.status === 422 ? "That is too long, or something is missing." : `The server answered ${reply.status}.`;
}

async function get<T>(path: string): Promise<T> {
  const reply = await fetch(path);
  if (!reply.ok) throw new Error(await problem(reply));
  return reply.json();
}

async function send<T>(path: string, body: unknown, method = "POST"): Promise<T> {
  const form = body instanceof FormData;   // files; the browser sets the header itself
  const reply = await fetch(path, {
    method, headers: form ? undefined : { "Content-Type": "application/json" }, body: form ? body : JSON.stringify(body),
  });
  if (!reply.ok) throw new Error(await problem(reply));
  return reply.json();
}

export const api = {
  status: () => get<Status>("/api/status"),
  setup: () => get<Setup>("/api/setup"),
  shop: (user: string) => get<Shop>(`/api/shop?user_id=${encodeURIComponent(user)}`),
  previewUpload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return send<UploadPreview>("/api/shops/preview", form);
  },
  acceptUpload: (token: string, name: string) => send<{ id: string; users: Store[] }>("/api/shops", { token, name }),
  discardUpload: (token: string) => send(`/api/shops/preview/${token}`, {}, "DELETE"),
  removeShop: (id: string) => send<{ users: Store[] }>(`/api/shops/${id}`, {}, "DELETE"),
  deleteMyData: () => send<{ users: Store[] }>("/api/session", {}, "DELETE"),
  conversations: () => get<Conversation[]>("/api/conversations"),
  conversation: (id: string) => get<Turn[]>(`/api/conversations/${id}`),
  failures: () => get<Failures>("/api/failures"),
  evaluation: () => get<Evaluation>("/api/evaluation"),
  glossary: (user: string) => get<Term[]>(`/api/glossary/${user}`),
  addTerm: (user: string, term: Term) => send<Term[]>(`/api/glossary/${user}`, term),
};

export type LiveEvent =
  | { type: "start"; thread_id: string }
  | { type: "queued" }
  | ({ type: "step" } & Step)
  | { type: "sql"; query: string; index: number }
  | ({ type: "rows" } & Preview)
  | { type: "tool_text"; text: string }
  | { type: "memory"; term: string; meaning: string }
  | { type: "approval"; question: string }
  | { type: "answer"; answer: string; stats: Stats; outcome: Outcome; table: Preview | null }
  | { type: "error"; message: string };

/** POSTs to a streaming endpoint and calls onEvent for every Server-Sent Event as it arrives. */
export async function stream(path: string, body: unknown, onEvent: (event: LiveEvent) => void) {
  const reply = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!reply.ok || !reply.body) {
    onEvent({ type: "error", message: await problem(reply) });
    return;
  }
  const reader = reply.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 2);
      if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
