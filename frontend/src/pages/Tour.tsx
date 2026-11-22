// Guided tour: short live demos of what QueryMend does, grouped by capability.
// With Technical details on, each demo also lists its SOP component, files and terminal command.
import { useNavigate } from "react-router-dom";
import { ArrowRight, Play, Terminal } from "lucide-react";
import { useSettings } from "../lib/settings";

/** What a demo button does: ask one or more questions on a fresh conversation. */
export interface TryIt {
  questions: string[];
  fault?: string | null;   // planted in the first question only
  monitorOn?: boolean;     // for these questions only, the Test lab is not changed
}

interface Demo {
  title: string;
  text: string;
  tryIt?: TryIt;
  action: string;
  link?: string;
  sop: string;
  files: string[];
  terminal?: string;
}

const REVENUE = "Which 3 products earned the most revenue in September 2026?";
const TOTAL = "What was the total delivered revenue in September 2026?";

const GROUPS: { name: string; demos: Demo[] }[] = [
  {
    name: "Catching wrong answers",
    demos: [
      {
        title: "Without a checker",
        text: "With the Monitor off, prices come back 100 times too big and the wrong answer is shown as if it were true.",
        tryIt: { questions: [REVENUE], fault: "corrupt_once", monitorOn: false },
        action: "Run without the Monitor",
        sop: "Baseline: the PBA 1 single agent",
        files: ["ta1/agent.py", "ta2/sql_tool.py: QM_FAULT"],
        terminal: "python ta1/run_cases.py",
      },
      {
        title: "Blocked and fixed",
        text: "The same bug with the Monitor on. The wrong result is blocked, Recovery says what to fix, and only the checked answer is shown.",
        tryIt: { questions: [REVENUE], fault: "corrupt_once" },
        action: "Run with the Monitor",
        sop: "U4-1 roles, U4-2 coordination, U3-4 failure handling",
        files: ["ta2/graph.py: build_team()", "ta2/agents.py: sanity_problems(), check_answer()"],
        terminal: "python ta2/run.py team",
      },
      {
        title: "Retry limit",
        text: "When every result is wrong, the team stops after 3 retries and says so, instead of guessing.",
        tryIt: { questions: [TOTAL], fault: "corrupt" },
        action: "Run with a lasting fault",
        sop: "U4-2 hop limit",
        files: ["ta2/config.py: RETRY_BUDGET", "ta2/agents.py: decide_recovery()"],
        terminal: "python ta2/run.py budget",
      },
      {
        title: "Fixes from the error guide",
        text: "A broken query is matched against a built-in guide to SQL mistakes, and the fix from the guide is applied.",
        tryIt: { questions: [REVENUE], fault: "garble_once" },
        action: "Run with a broken query",
        sop: "U3-3 RAG",
        files: ["ta2/corpus/error_guide.md", "ta2/rag.py: get_vectorstore(), advise()"],
        terminal: "python ta2/run.py rag",
      },
    ],
  },
  {
    name: "Memory",
    demos: [
      {
        title: "Follow-up questions",
        text: "Each conversation is saved, so a short follow-up like \"And in August?\" is understood.",
        tryIt: { questions: [TOTAL, "And in August?"] },
        action: "Ask a question and a follow-up",
        sop: "U3-1 short-term memory",
        files: ["ta2/memory.py: make_checkpointer()"],
        terminal: "python ta2/run.py amnesia",
      },
      {
        title: "Business words",
        text: "Each store saves its own terms, like what \"premium product\" means, and they apply to every future question.",
        link: "/glossary",
        action: "Open Your words",
        sop: "U3-2 long-term memory",
        files: ["ta2/memory.py: make_store(), save_term()"],
        terminal: "python ta2/run.py glossary",
      },
    ],
  },
  {
    name: "Safety",
    demos: [
      {
        title: "Personal data needs approval",
        text: "If an answer includes customer email or phone numbers, QueryMend pauses and asks before showing them.",
        tryIt: { questions: ["Show the name, email and phone of customers who live in Pune."] },
        action: "Ask for contact details",
        sop: "U4-3 human oversight",
        files: ["ta2/graph.py: privacy_gate()", "ta2/agents.py: mask_sensitive()"],
        terminal: "python ta2/run.py privacy",
      },
      {
        title: "Data cannot be changed",
        text: "Requests to delete or edit data are refused and never retried. The database is opened read-only.",
        tryIt: { questions: ["Delete all cancelled orders from the database."] },
        action: "Ask to delete data",
        sop: "U3-4 failure handling",
        files: ["ta2/sql_tool.py: check_query()", "ta2/agents.py: decide_recovery()"],
      },
    ],
  },
  {
    name: "Evidence",
    demos: [
      {
        title: "Failure log",
        text: "Every problem caught is recorded with what was done about it.",
        link: "/failures",
        action: "Open Caught failures",
        sop: "U3-4 failure handling",
        files: ["ta2/memory.py: FailureLog"],
        terminal: "python ta2/run.py faillog",
      },
      {
        title: "Measured results",
        text: "The same questions asked to a plain single agent and to QueryMend, marked against the right answers.",
        link: "/quality",
        action: "Open Quality report",
        sop: "EV evaluation, U4-4 error propagation",
        files: ["ta2/evaluate.py", "traces/eval/results.json"],
        terminal: "python ta2/evaluate.py",
      },
    ],
  },
];

export function Tour() {
  const navigate = useNavigate();
  const { technical } = useSettings();
  let number = 0;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Guided tour</h1>
          <p className="page-sub">Short live demos of what QueryMend does. Each one starts a new conversation.</p>
        </div>
      </header>

      {GROUPS.map((group) => (
        <section key={group.name} className="tour-group">
          <h2>{group.name}</h2>
          <ol className="tour-parts">
            {group.demos.map((d) => {
              number += 1;
              const run = () => (d.tryIt ? navigate("/", { state: { tryIt: d.tryIt } }) : navigate(d.link!));
              return (
                <li key={d.title} className="tour-part">
                  <span className="tour-number">{number}</span>
                  <div className="tour-body">
                    <h3>{d.title}</h3>
                    <p className="tour-plain">{d.text}</p>
                    {technical && (
                      <div className="tour-tech">
                        <p className="tour-sop">{d.sop}</p>
                        <ul>{d.files.map((f) => <li key={f}><code>{f}</code></li>)}</ul>
                        {d.terminal && <p className="tour-terminal"><Terminal size={14} /> <code>{d.terminal}</code></p>}
                      </div>
                    )}
                  </div>
                  <button className={`button ${d.tryIt ? "button-brand" : "button-quiet"} tour-run`} onClick={run}>
                    {d.tryIt && <Play size={15} />} {d.action} {!d.tryIt && <ArrowRight size={15} />}
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}
