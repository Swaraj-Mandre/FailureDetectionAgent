// Turns the agents' trace lines into plain sentences for a shop owner.
// The technical detail stays available underneath each sentence.
import type { Outcome, Role, Step } from "./api";

export const ROLE_NAME: Record<Role, string> = {
  executor: "Executor",
  monitor: "Monitor",
  recovery: "Recovery",
  person: "You",
  stop: "Safe stop",
};

export const ROLE_JOB: Record<Role, string> = {
  executor: "Writes and runs the query",
  monitor: "Checks every result",
  recovery: "Fixes or stops safely",
  person: "Approves personal data",
  stop: "Ends without guessing",
};

const inside = (text: string) => text.match(/\((.*)\)\s*$/)?.[1] ?? "";

export function describe(step: Step): { title: string; detail: string; alarm: boolean } {
  const t = step.text;
  switch (step.role) {
    case "executor":
      if (t.startsWith("calls")) return { title: "Wrote a query and ran it", detail: "", alarm: false };
      return { title: "Wrote the answer", detail: "", alarm: false };
    case "monitor":
      if (t.startsWith("OFF")) return { title: "Monitor is off, nothing was checked", detail: t, alarm: true };
      if (t.startsWith("silent bad result")) return { title: "Blocked a wrong result before anyone used it", detail: inside(t), alarm: true };
      if (t.startsWith("tool error")) return { title: "Caught an error from the database", detail: `type: ${t.split("'")[1] ?? "unknown"}`, alarm: true };
      if (t.startsWith("result kept")) return { title: "Not sure this fits the question, so please verify it", detail: inside(t), alarm: true };
      if (t.startsWith("personal data")) return { title: "Found personal data and asked you first", detail: (t.match(/\[(.*)\]/)?.[1] ?? "").replace(/'/g, ""), alarm: false };
      if (t.startsWith("answer does not match")) return { title: "Rejected an answer that did not match the data", detail: inside(t), alarm: true };
      if (t.startsWith("answer matches")) return { title: "Confirmed the answer matches the data", detail: `value ${inside(t)}`, alarm: false };
      if (t.startsWith("answer")) return { title: "Approved the answer", detail: "", alarm: false };
      return { title: "Checked the result: it looks right", detail: "", alarm: false };
    case "recovery":
      if (t.startsWith("STOP")) return { title: "Stopped instead of guessing", detail: inside(t), alarm: true };
      return {
        title: t.includes("guide sections") ? "Found a fix in the error guide" : "Told the Executor exactly what to fix",
        detail: t.replace(/^advice from /, "").replace(/guide sections /, "").replace(/[[\]]/g, "").replace(/'([^']*)'/g, "$1"),
        alarm: false,
      };
    case "person":
      return t.includes("APPROVED")
        ? { title: "You allowed the personal data", detail: "", alarm: false }
        : { title: "You hid the personal data", detail: "personal columns masked", alarm: false };
    case "stop":
      return { title: "Wrote a safe answer with no guessed numbers", detail: "", alarm: true };
  }
}

export const OUTCOME: Record<Outcome, { label: string; tone: Role | "danger" }> = {
  checked: { label: "Checked", tone: "recovery" },
  recovered: { label: "Fixed and checked", tone: "recovery" },
  approved: { label: "Shown with your approval", tone: "person" },
  masked: { label: "Personal data hidden", tone: "person" },
  stopped: { label: "Stopped safely", tone: "danger" },
  unchecked: { label: "Not checked", tone: "monitor" },
  verify: { label: "Please verify", tone: "monitor" },
};

export const FAILURE_NAME: Record<string, string> = {
  bad_result: "Wrong numbers",
  no_group_by: "Missing GROUP BY",
  schema: "Unknown table or column",
  syntax: "Broken query",
  busy: "Database busy",
  unsafe: "Tried to change data",
  unavailable: "Database unreachable",
  unsupported_answer: "Answer not backed by data",
  arguments: "Bad tool input",
  wrong_query: "Query adds up wrongly",
  doubt: "Query does not fit the question",
  invented_number: "Number not in the data",
  answer_mismatch: "Answer does not match the data",
};

export function plural(n: number, word: string, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}

export function when(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** "recovery advised a retry using guide sections ['A', 'B']" becomes "Retried with advice from the guide: A, B". */
export function action(text: string) {
  const clean = (t: string) => t.replace(/[[\]]/g, "").replace(/'([^']*)'/g, "$1");
  if (text.startsWith("recovery stopped")) return `Stopped safely: ${text.split(": ").slice(1).join(": ")}`;
  if (text.includes("guide sections")) return `Retried with advice from the error guide: ${clean(text.split("guide sections ")[1] ?? "")}`;
  if (text.includes("monitor's finding")) return "Retried with the exact fix the Monitor found";
  return text.charAt(0).toUpperCase() + text.slice(1);
}
