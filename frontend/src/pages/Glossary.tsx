// What each store's own words mean. QueryMend uses these in every conversation, even after a restart.
import { useEffect, useState, type FormEvent } from "react";
import { api, type Term } from "../lib/api";
import { useSettings } from "../lib/settings";

export function Glossary() {
  const { setup, userId } = useSettings();
  const [terms, setTerms] = useState<Term[]>([]);
  const [term, setTerm] = useState("");
  const [meaning, setMeaning] = useState("");
  const [error, setError] = useState("");
  const store = setup?.users.find((u) => u.id === userId)?.name ?? "";

  useEffect(() => { api.glossary(userId).then(setTerms).catch((e) => setError(e.message)); }, [userId]);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      setTerms(await api.addTerm(userId, { term, meaning }));
      setTerm("");
      setMeaning("");
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="page narrow">
      <header className="page-head">
        <div>
          <h1>Your words</h1>
          <p className="page-sub">What "{store}" means by its own business words. QueryMend follows these in every conversation, even after a restart. Each store keeps its own list.</p>
        </div>
      </header>

      <section className="panel flush">
        <dl className="terms">
          {terms.map((t) => (
            <div key={t.term}><dt>{t.term}</dt><dd>{t.meaning}</dd></div>
          ))}
        </dl>
        {terms.length === 0 && <p className="empty">No words saved for this store yet.</p>}
      </section>

      <form className="panel term-form" onSubmit={add}>
        <h2>Teach QueryMend a word</h2>
        <div className="fields">
          <label>Word<input value={term} maxLength={60} onChange={(e) => setTerm(e.target.value)} placeholder="big order" /></label>
          <label>What it means<input value={meaning} maxLength={300} onChange={(e) => setMeaning(e.target.value)} placeholder="an order with a total above Rs 8,000" /></label>
        </div>
        {error && <p className="turn-error">{error}</p>}
        <button className="button button-brand" disabled={!term.trim() || !meaning.trim()}>Save the word</button>
        <p className="panel-note">You can also teach a word while asking: "By premium product I mean a price above Rs 1500."</p>
      </form>
    </div>
  );
}
