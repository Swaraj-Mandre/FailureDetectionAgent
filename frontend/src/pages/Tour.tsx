// The project tour: who built what, in the order the group presents it.
// Each part has a plain explanation, a live "Try it", and a panel for engineers with files and SOP IDs.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, ChevronDown, Play, Terminal } from "lucide-react";
import { useSettings } from "../lib/settings";

/** What a Try it button does: ask one or more questions on a fresh conversation. */
export interface TryIt {
  questions: string[];
  fault?: string | null;   // planted in the first question only
  monitorOn?: boolean;     // for these questions only, the Test lab is not changed
}

interface Part {
  title: string;
  plain: string;
  watch?: string;
  tryIt?: TryIt;
  tryLabel?: string;
  link?: { to: string; label: string };
  terminal?: string;
  sop: string;
  files: { path: string; what: string }[];
}

interface Member {
  key: string;
  name: string;
  roll: string;
  built: string;
  parts: Part[];
}

const REVENUE = "Which 3 products earned the most revenue in September 2026?";
const TOTAL = "What was the total delivered revenue in September 2026?";

const MEMBERS: Member[] = [
  {
    key: "swaraj",
    name: "Swaraj Mandre",
    roll: "ADT23SOCB1192",
    built: "The first agent, the team, the Monitor and the retry budget",
    parts: [
      {
        title: "The problem we solve",
        plain: "An AI that writes database queries can return a wrong number with no error at all. A single agent cannot tell, so it passes the wrong number on as if it were true.",
        watch: "With the Monitor off, the prices come back 100 times too big and the answer is shown anyway, marked Not checked.",
        tryIt: { questions: [REVENUE], fault: "corrupt_once", monitorOn: false },
        tryLabel: "Show a wrong answer getting through",
        sop: "Topic row: failure detection agent",
        files: [
          { path: "ta2/sql_tool.py", what: "execute() and the QM_FAULT switch that plants each failure" },
          { path: "ta1/agent.py", what: "the single agent the evaluation compares against" },
        ],
      },
      {
        title: "PBA 1: the single agent",
        plain: "Our first version: one model with one tool that runs read-only SQL. It handles errors it can see, like a wrong column name, but not silent ones.",
        terminal: "python ta1/run_cases.py",
        sop: "PBA 1 baseline",
        files: [
          { path: "ta1/agent.py", what: "the agent and its error routing" },
          { path: "ta1/sql_tool.py", what: "SELECT-only check, read-only connection, 5 second timeout" },
          { path: "ta1/REFINEMENT_NOTES.md", what: "what changed between the slide version and the final one" },
        ],
      },
      {
        title: "The team: Executor, Monitor, Recovery",
        plain: "PBA 2 splits the work. The Executor writes and runs the query, the Monitor checks every result against the real data, and Recovery tells the Executor exactly what to fix.",
        watch: "The Monitor blocks the first, wrong result before anyone sees it, Recovery sends the exact fix, and the second try is checked. The badge says Fixed and checked.",
        tryIt: { questions: [REVENUE], fault: "corrupt_once" },
        tryLabel: "Plant a wrong result and watch it get fixed",
        sop: "U4-1 roles, U4-2 coordination, U3-4 failure handling",
        files: [
          { path: "ta2/graph.py", what: "build_team(): nodes executor, tools, monitor_result, monitor_answer, recovery, stop" },
          { path: "ta2/agents.py", what: "check_tool_results(), sanity_problems(), ungrouped_columns(), check_answer()" },
          { path: "ta2/ROLES.md", what: "five-field spec for each role" },
        ],
      },
      {
        title: "The retry budget",
        plain: "Recovery gets 3 tries per question. If the problem keeps coming back, the team stops and says so plainly, instead of guessing a number.",
        watch: "Every result is wrong this time. After 3 retries the answer is Stopped safely, with no made-up figure.",
        tryIt: { questions: [TOTAL], fault: "corrupt" },
        tryLabel: "Break every result and watch it stop",
        sop: "U4-2 hop limit",
        files: [
          { path: "ta2/config.py", what: "RETRY_BUDGET = 3" },
          { path: "ta2/agents.py", what: "decide_recovery() and stop_message()" },
        ],
      },
      {
        title: "Does a wrong number spread?",
        plain: "We planted one bad result and asked a follow-up in the same conversation. With the Monitor off the wrong number spread into the next answer every time. With it on, never.",
        link: { to: "/quality", label: "Open the quality report" },
        terminal: "python ta2/run.py containment",
        sop: "U4-4 challenge: error propagation",
        files: [
          { path: "ta2/run.py", what: "demo_containment()" },
          { path: "ta2/evaluate.py", what: "containment_table()" },
        ],
      },
    ],
  },
  {
    key: "saumya",
    name: "Saumya Joshi",
    roll: "ADT23SOCB0997",
    built: "Memory, the privacy approval and the failure log",
    parts: [
      {
        title: "Conversation memory",
        plain: "Each conversation is saved as it happens. A follow-up like \"and in August?\" works because the team remembers the earlier question, even after a restart.",
        watch: "The second question has no product, no measure and no year. The answer still makes sense.",
        tryIt: { questions: [TOTAL, "And in August?"] },
        tryLabel: "Ask a question and a follow-up",
        terminal: "python ta2/run.py amnesia",
        sop: "U3-1 short-term memory",
        files: [
          { path: "ta2/memory.py", what: "make_checkpointer(): LangGraph SqliteSaver" },
          { path: "ta2/server.py", what: "one thread_id per conversation, saved in ta2/state/app.db" },
          { path: "ta2/run.py", what: "demo_amnesia(), demo_restart(), demo_resume()" },
        ],
      },
      {
        title: "Your words (long-term memory)",
        plain: "Each store can teach QueryMend its own business words, for example that revenue means after discount. They are kept per store and used in every later conversation.",
        link: { to: "/glossary", label: "Open Your words" },
        terminal: "python ta2/run.py glossary",
        sop: "U3-2 long-term memory",
        files: [
          { path: "ta2/memory.py", what: "make_store() SqliteStore, namespace (user_id, \"glossary\"), save_term(), make_glossary_tool()" },
          { path: "ta2/seed_store.py", what: "preloads two terms" },
        ],
      },
      {
        title: "Personal data needs your approval",
        plain: "If an answer includes customers' email or phone numbers, QueryMend pauses and asks a person first. Show them reveals them, Keep them hidden masks them.",
        watch: "The run stops before the answer. Pick either button and it carries on from the same point.",
        tryIt: { questions: ["Show the name, email and phone of customers who live in Pune."] },
        tryLabel: "Ask for customer contact details",
        terminal: "python ta2/run.py privacy",
        sop: "U4-3 human oversight",
        files: [
          { path: "ta2/graph.py", what: "privacy_gate() with interrupt(), resumed with Command(resume=...)" },
          { path: "ta2/agents.py", what: "sensitive_columns() and mask_sensitive()" },
          { path: "ta2/server.py", what: "POST /api/resume" },
        ],
      },
      {
        title: "The failure log",
        plain: "Every problem the Monitor catches is written down: when, what kind, what the team did, and how many retries were left. Nothing is hidden.",
        link: { to: "/failures", label: "Open Caught failures" },
        terminal: "python ta2/run.py faillog",
        sop: "U3-4 failure handling, evidence",
        files: [
          { path: "ta2/memory.py", what: "class FailureLog, one SQLite row per failure" },
          { path: "ta2/server.py", what: "GET /api/failures" },
        ],
      },
    ],
  },
  {
    key: "siddhant",
    name: "Siddhant Pawar",
    roll: "ADT23SOCB1117",
    built: "The error guide (RAG), the Recovery agent and the evaluation",
    parts: [
      {
        title: "The error guide (RAG)",
        plain: "We wrote our own guide to common SQL mistakes in this shop's data. When a query fails, Recovery looks up the closest sections and uses them to explain the fix. If the guide has nothing, it says it does not know.",
        watch: "The first query arrives broken. Recovery finds a fix in the error guide and the second try works.",
        tryIt: { questions: [REVENUE], fault: "garble_once" },
        tryLabel: "Break a query and watch the guide fix it",
        terminal: "python ta2/run.py rag",
        sop: "U3-3 RAG",
        files: [
          { path: "ta2/corpus/error_guide.md", what: "15 sections, one chunk each" },
          { path: "ta2/rag.py", what: "load_chunks(), get_vectorstore() Chroma with nomic-embed-text, retrieve() k=3, ask_guide()" },
        ],
      },
      {
        title: "The Recovery agent",
        plain: "Recovery decides what happens after a failure: retry with exact advice, wait for a busy database, or stop. A request to change data is never retried.",
        watch: "Asking to delete orders is blocked by the tool, and Recovery stops instead of trying another way.",
        tryIt: { questions: ["Delete all cancelled orders from the database."] },
        tryLabel: "Ask it to delete data",
        terminal: "python ta2/run.py advice",
        sop: "U3-4 failure handling, U4-1 roles",
        files: [
          { path: "ta2/graph.py", what: "recovery() node" },
          { path: "ta2/rag.py", what: "advise(error, sql)" },
          { path: "ta2/agents.py", what: "decide_recovery() and advice_message()" },
        ],
      },
      {
        title: "Measuring it",
        plain: "The same questions were asked to the single agent and to QueryMend, several times each, and marked against the known right answers. The report shows where we are better and what it costs.",
        link: { to: "/quality", label: "Open the quality report" },
        terminal: "python ta2/evaluate.py",
        sop: "EV evaluation, U4-4",
        files: [
          { path: "ta2/evaluate.py", what: "judge(), ev_table(), injection_table(), containment_table()" },
          { path: "traces/eval/results.json", what: "every run, read by GET /api/evaluation" },
          { path: "ta2/EVALUATION.md", what: "the tables in words" },
        ],
      },
    ],
  },
];

export function Tour() {
  const [who, setWho] = useState<string>("all");
  const shown = MEMBERS.filter((m) => who === "all" || m.key === who);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Project tour</h1>
          <p className="page-sub">Group 17, AIA_05. Who built what, in the order we present it. Every part can be shown live.</p>
        </div>
      </header>

      <div className="tour-filter" role="tablist" aria-label="Show parts by">
        <button role="tab" aria-selected={who === "all"} className={who === "all" ? "picked" : ""} onClick={() => setWho("all")}>Everyone</button>
        {MEMBERS.map((m) => (
          <button key={m.key} role="tab" aria-selected={who === m.key} className={who === m.key ? "picked" : ""} onClick={() => setWho(m.key)}>
            {m.name.split(" ")[0]}
          </button>
        ))}
      </div>

      {shown.map((m) => {
        const start = MEMBERS.slice(0, MEMBERS.indexOf(m)).reduce((n, x) => n + x.parts.length, 0);
        return (
          <section key={m.key} className="tour-member">
            <header className="tour-person">
              <span className="initials" aria-hidden="true">{m.name.split(" ").map((w) => w[0]).join("")}</span>
              <div>
                <h2>{m.name}</h2>
                <p>{m.built} <span className="roll">{m.roll}</span></p>
              </div>
            </header>
            <ol className="tour-parts">
              {m.parts.map((p, i) => <PartView key={p.title} part={p} number={start + i + 1} />)}
            </ol>
          </section>
        );
      })}
    </div>
  );
}

function PartView({ part, number }: { part: Part; number: number }) {
  const navigate = useNavigate();
  const { technical } = useSettings();
  const [open, setOpen] = useState(false);
  const engineers = technical || open;

  return (
    <li className="tour-part">
      <span className="tour-number">{number}</span>
      <div className="tour-body">
        <h3>{part.title}</h3>
        <p className="tour-plain">{part.plain}</p>
        {part.watch && <p className="tour-watch"><strong>What to watch:</strong> {part.watch}</p>}

        <div className="tour-actions">
          {part.tryIt && (
            <button className="button button-brand" onClick={() => navigate("/", { state: { tryIt: part.tryIt } })}>
              <Play size={15} /> {part.tryLabel ?? "Try it"}
            </button>
          )}
          {part.link && (
            <button className="button button-quiet" onClick={() => navigate(part.link!.to)}>
              {part.link.label} <ArrowRight size={15} />
            </button>
          )}
          <button className="trail-toggle" onClick={() => setOpen(!open)} aria-expanded={engineers} disabled={technical}>
            <ChevronDown size={15} className={engineers ? "flip" : ""} /> For engineers
          </button>
        </div>

        {engineers && (
          <div className="tour-tech">
            <p className="tour-sop">{part.sop}</p>
            <ul>
              {part.files.map((f) => (
                <li key={f.path + f.what}><code>{f.path}</code> <span>{f.what}</span></li>
              ))}
            </ul>
            {part.terminal && <p className="tour-terminal"><Terminal size={14} /> In a terminal: <code>{part.terminal}</code></p>}
          </div>
        )}
      </div>
    </li>
  );
}
