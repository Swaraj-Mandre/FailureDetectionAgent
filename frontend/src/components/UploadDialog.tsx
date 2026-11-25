// Adding a shop: choose files, look at what was found, accept it. Nothing is added before the last step.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileSpreadsheet, Link2, Lock, Upload, X } from "lucide-react";
import { api, type UploadPreview } from "../lib/api";
import { useSettings } from "../lib/settings";
import { plural } from "../lib/words";

export const ACCEPT = ".xlsx,.xlsm,.xls,.csv,.tsv,.txt,.sql,.db,.sqlite,.sqlite3";

export function UploadDialog() {
  const { setup, upload, closeUpload, setUsers, setUserId } = useSettings();
  const navigate = useNavigate();
  const dialog = useRef<HTMLDialogElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState("");
  const [found, setFound] = useState<UploadPreview | null>(null);
  const [name, setName] = useState("");
  const turn = useRef(0);   // an answer for files that were replaced or cancelled meanwhile is ignored

  const read = async (files: File[]) => {
    if (!files.length) return;
    const mine = ++turn.current;
    setReading(true);
    setError("");
    setFound(null);
    try {
      const preview = await api.previewUpload(files);
      if (mine !== turn.current) { api.discardUpload(preview.token).catch(() => undefined); return; }
      setFound(preview);
      setName(preview.name);
    } catch (e) {
      if (mine === turn.current) setError((e as Error).message);
    } finally {
      if (mine === turn.current) setReading(false);
    }
  };

  useEffect(() => {
    if (!upload) return;
    setError("");
    setFound(null);
    setSaving(false);
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    read(upload.files);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [upload]);

  if (!upload) return null;

  const cancel = () => {
    turn.current++;
    if (found) api.discardUpload(found.token).catch(() => undefined);
    setReading(false);
    closeUpload();
  };

  const accept = async () => {
    if (!found) return;
    setSaving(true);
    setError("");
    try {
      const { id, users } = await api.acceptUpload(found.token, name);
      setUsers(users);
      setUserId(id);
      closeUpload();
      navigate("/");
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  return (
    <dialog ref={dialog} className="upload" aria-label="Add your shop data" onCancel={(e) => { e.preventDefault(); cancel(); }}>
      <div className="upload-head">
        <h2>{found ? "Is this your data?" : "Add your shop data"}</h2>
        <button className="icon-button" onClick={cancel} aria-label="Close"><X size={17} /></button>
      </div>

      <input ref={picker} type="file" multiple hidden accept={ACCEPT} aria-label="Files"
        onChange={(e) => { read([...(e.target.files ?? [])]); e.target.value = ""; }} />

      <div className="upload-body">
        {error && <p className="turn-error">{error}</p>}

        {!found && (
          <button className={`drop ${over ? "over" : ""}`} disabled={reading} onClick={() => picker.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); read([...e.dataTransfer.files]); }}>
            <Upload size={22} />
            <strong>{reading ? "Reading your file" : "Choose a file, or drop it here"}</strong>
            <small>Excel, CSV, a SQL backup or a SQLite database. Up to {setup?.max_files ?? 8} files, {setup?.max_file_mb ?? 10} MB each.</small>
          </button>
        )}

        {!found && !reading && (
          <p className="upload-note">
            Using Vyapar, Tally, Busy or another billing app? Open the sale report there and export it to Excel.
            Files that belong together, such as stock and bills, can be added in one go.
            {setup?.public && <> This is an online demo: please use sample data or a copy without private details.
              Only you can see it. It is deleted after {setup.keep_hours} hours without use, or at once with Delete my data.</>}
          </p>
        )}

        {found && (
          <>
            <label className="upload-name">
              <span className="rail-label">Name this store</span>
              <input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
            </label>

            {found.tables.map((t) => (
              <section key={t.name} className="found">
                <p className="found-head">
                  <FileSpreadsheet size={16} /> <strong>{t.source}</strong>
                  <span>{plural(t.rows, "row")}, {plural(t.columns.length, "column")}</span>
                </p>
                <div className="preview">
                  <table>
                    <thead>
                      <tr>{t.columns.map((c) => (
                        <th key={c.name}>{c.name}<small>{c.personal ? <><Lock size={10} /> private</> : c.type}</small></th>
                      ))}</tr>
                    </thead>
                    <tbody>
                      {t.sample.map((row, i) => (
                        <tr key={i}>{row.map((v, j) => <td key={j} className={v === "(hidden)" ? "hidden-cell" : ""}>{v ?? ""}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {t.joins.map((j) => <p key={j} className="found-join"><Link2 size={13} /> {j}</p>)}
                <ul className="found-notes">{t.notes.filter((n) => !n.startsWith("Linked to")).map((n) => <li key={n}>{n}</li>)}</ul>
              </section>
            ))}
          </>
        )}
      </div>

      {found && (
        <div className="upload-foot">
          <p>{setup?.public
            ? `Only you can see this data here. It is deleted after ${setup.keep_hours} hours without use, or when you press Delete my data.`
            : "Your file stays on this computer."} Private columns are shown only after you approve.</p>
          <button className="button button-quiet" onClick={() => picker.current?.click()} disabled={saving}>Choose another file</button>
          <button className="button button-brand" onClick={accept} disabled={saving || !name.trim()}>
            {saving ? "Adding" : "Use this data"}
          </button>
        </div>
      )}
    </dialog>
  );
}
