// The main page: ask a question, watch the team work, answer the approval, read the checked answer.
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, stream, type LiveEvent, type Turn } from "../lib/api";
import { useSettings } from "../lib/settings";
import { month } from "../lib/words";
import { Composer } from "../components/Composer";
import { TestLab } from "../components/TestLab";
import { TurnView } from "../components/TurnView";
import { RoleMark } from "../components/Bits";

export function Ask() {
  const { id } = useParams();
  const navigate = useNavigate();
  const settings = useSettings();
  const { setup, shop, userId, monitorOn, fault, setFault, refreshConversations } = settings;
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const threadRef = useRef<string | undefined>(id);
  const endRef = useRef<HTMLDivElement>(null);

  // Opening a past conversation loads its turns; a new question starts empty.
  useEffect(() => {
    if (id && id === threadRef.current && turns.length) return;   // we just created it ourselves
    threadRef.current = id;
    setTurns([]);
    if (id) api.conversation(id).then(setTurns).catch(() => setTurns([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);

  const update = (change: (turn: Turn) => Turn) =>
    setTurns((all) => [...all.slice(0, -1), change(all[all.length - 1])]);

  const apply = (event: LiveEvent) => {
    switch (event.type) {
      case "start":
        if (threadRef.current !== event.thread_id) {
          threadRef.current = event.thread_id;
          navigate(`/c/${event.thread_id}`, { replace: true });
        }
        break;
      case "step": update((t) => ({ ...t, steps: [...t.steps, { role: event.role, text: event.text, node: event.node }] })); break;
      case "sql": update((t) => ({ ...t, queries: [...t.queries, event.query] })); break;
      case "rows": update((t) => ({ ...t, results: [...t.results, { columns: event.columns, rows: event.rows, total: event.total, hidden: event.hidden }] })); break;
      case "tool_text": update((t) => ({ ...t, results: [...t.results, { columns: [], rows: [], total: 0, hidden: 0, error: event.text }] })); break;
      case "approval": update((t) => ({ ...t, approval: event.question, running: false })); break;
      case "answer": update((t) => ({ ...t, answer: event.answer, stats: event.stats, outcome: event.outcome, table: event.table, running: false, approval: undefined })); break;
      case "error": update((t) => ({ ...t, error: event.message, running: false })); break;
    }
  };

  const run = async (path: string, body: unknown) => {
    setBusy(true);
    try {
      await stream(path, body, apply);
    } catch {
      update((t) => ({ ...t, running: false, error: "Lost the connection to the QueryMend server." }));
    } finally {
      setBusy(false);
      refreshConversations();
    }
  };

  const ask = (question: string) => {
    setTurns((all) => [...all, { question, fault, monitor_on: monitorOn, steps: [], queries: [], results: [], running: true }]);
    setFault(null);   // a planted failure applies to one question
    run("/api/ask", { thread_id: threadRef.current, question, user_id: userId, monitor_on: monitorOn, fault });
  };

  const decide = (decision: "approve" | "reject") => {
    update((t) => ({ ...t, approval: undefined, running: true }));
    run("/api/resume", { thread_id: threadRef.current, decision });
  };

  const store = setup?.users.find((u) => u.id === userId)?.name;
  const faultName = (f: string | null) => setup?.faults.find((x) => x.id === f)?.name;

  return (
    <div className="ask">
      <header className="page-head">
        <div>
          <h1>{turns.length ? "Conversation" : "Ask"}</h1>
          <p className="page-sub">{store} · {shop ? `${shop.orders} orders, ${month(shop.first_order)} to ${month(shop.last_order)}` : "loading the shop"}</p>
        </div>
        <TestLab />
      </header>

      <div className="thread">
        {turns.length === 0 ? (
          <Welcome onPick={ask} examples={setup?.examples ?? []} />
        ) : (
          turns.map((turn, i) => (
            <TurnView key={i} turn={turn} faultName={faultName(turn.fault) ?? undefined}
              retryBudget={setup?.retry_budget ?? 3} onDecide={i === turns.length - 1 ? decide : undefined} />
          ))
        )}
        <div ref={endRef} />
      </div>

      <Composer busy={busy || turns.some((t) => t.approval)} onAsk={ask} />
    </div>
  );
}

function Welcome({ onPick, examples }: { onPick: (q: string) => void; examples: string[] }) {
  const { shop } = useSettings();
  return (
    <section className="welcome">
      <h2>Ask your shop anything.</h2>
      <p className="welcome-lead">
        QueryMend answers from your sales data and checks every number before you see it.
        If something is wrong, it fixes it, or tells you plainly that it can't.
      </p>

      {shop && (
        <dl className="shop-facts">
          <div><dt>Customers</dt><dd>{shop.customers}</dd></div>
          <div><dt>Products</dt><dd>{shop.products}</dd></div>
          <div><dt>Orders</dt><dd>{shop.orders}</dd></div>
          <div><dt>Period</dt><dd>{month(shop.first_order)} to {month(shop.last_order)}</dd></div>
        </dl>
      )}

      <div className="how">
        <div><RoleMark role="executor" /><p><strong>Executor</strong> writes the query and runs it</p></div>
        <span className="how-arrow" aria-hidden="true" />
        <div><RoleMark role="monitor" /><p><strong>Monitor</strong> checks the result against the real data</p></div>
        <span className="how-arrow" aria-hidden="true" />
        <div><RoleMark role="recovery" /><p><strong>Recovery</strong> fixes it, or stops instead of guessing</p></div>
      </div>

      <p className="rail-label">Try one of these</p>
      <div className="examples">
        {examples.map((q) => <button key={q} className="example" onClick={() => onPick(q)}>{q}</button>)}
      </div>
    </section>
  );
}
