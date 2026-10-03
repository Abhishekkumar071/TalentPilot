import { useState } from "react";
import { api } from "../../api.js";
import { Empty, ErrorBox, Loading, Modal, useAction, useData } from "../../components/ui.jsx";

export default function Knowledge() {
  const { data: docs, error, loading, reload } = useData(() => api.get("/api/hr/knowledge"), []);
  const [showAdd, setShowAdd] = useState(false);
  const [viewing, setViewing] = useState(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState(null);
  const action = useAction();

  const search = (e) => {
    e.preventDefault();
    action.run(async () => setHits(await api.get(`/api/hr/knowledge-search?q=${encodeURIComponent(q)}`)));
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Knowledge base</h1>
          <p>Company policies, salary bands, benefits and past JDs. Agents retrieve these (RAG) before writing JDs, replanning or drafting offers.</p>
        </div>
        <div className="row">
          <button className="btn" onClick={() => action.run(async () => { const r = await api.post("/api/hr/knowledge/reindex"); alert(`Embedded ${r.embedded} chunks`); await reload(); })}>Re-embed</button>
          <button className="btn btn-primary" onClick={() => setShowAdd(true)}>+ Add document</button>
        </div>
      </div>
      <ErrorBox error={error || action.error} />

      <div className="card">
        <h2>🔎 Test retrieval</h2>
        <form className="row" onSubmit={search} style={{ flexWrap: "nowrap" }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. salary band for senior backend engineer" />
          <button className="btn btn-primary" disabled={!q.trim() || action.busy}>Search</button>
        </form>
        {hits && (
          <div className="stack" style={{ marginTop: 12 }}>
            {hits.length === 0 && <p className="muted small">No matches.</p>}
            {hits.map((h, i) => (
              <div key={i} className="copybox" style={{ maxHeight: 160 }}>
                <b>{h.title}</b> <span className="faint">({h.category}, score {h.score})</span>
                {"\n"}{h.text}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        {loading && !docs ? <Loading /> : !docs?.length ? <Empty icon="📚" title="No documents" /> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Document</th><th>Category</th><th>Chunks</th><th>Embedded</th><th /></tr></thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td><a href="#" onClick={(e) => { e.preventDefault(); action.run(async () => setViewing(await api.get(`/api/hr/knowledge/${d.id}`))); }}><b>{d.title}</b></a><div className="faint">{d.preview.slice(0, 110)}...</div></td>
                    <td><span className="chip">{d.category}</span></td>
                    <td>{d.chunks}</td>
                    <td>{d.embedded ? <span className="badge ok">{d.embedded}</span> : <span className="badge" title="Add GEMINI_API_KEY for semantic search; BM25 keyword search works without it">keyword only</span>}</td>
                    <td><button className="btn btn-sm btn-danger" onClick={() => confirm(`Delete "${d.title}"?`) && action.run(async () => { await api.del(`/api/hr/knowledge/${d.id}`); await reload(); })}>Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {showAdd && <AddDoc onClose={() => setShowAdd(false)} onDone={reload} />}
      {viewing && (
        <Modal title={viewing.title} onClose={() => setViewing(null)}>
          <pre className="copybox" style={{ maxHeight: "65vh" }}>{viewing.content}</pre>
        </Modal>
      )}
    </>
  );
}

function AddDoc({ onClose, onDone }) {
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("policy");
  const [content, setContent] = useState("");
  const [file, setFile] = useState(null);
  const action = useAction();
  const submit = (e) => {
    e.preventDefault();
    action.run(async () => {
      const fd = new FormData();
      fd.append("title", title);
      fd.append("category", category);
      fd.append("content", content);
      if (file) fd.append("file", file);
      await api.upload("/api/hr/knowledge", fd);
      await onDone();
      onClose();
    });
  };
  return (
    <Modal title="Add company document" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="grid grid-2">
          <div className="field"><label>Title</label><input required value={title} onChange={(e) => setTitle(e.target.value)} /></div>
          <div className="field"><label>Category</label>
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {["policy", "salary", "benefits", "company", "engineering", "careers", "past_jd", "general"].map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
        </div>
        <div className="field"><label>Upload PDF / DOCX / TXT</label><input type="file" accept=".pdf,.docx,.txt" onChange={(e) => setFile(e.target.files?.[0] || null)} /></div>
        <div className="field"><label>...or paste text</label><textarea rows={8} value={content} onChange={(e) => setContent(e.target.value)} /></div>
        <ErrorBox error={action.error} />
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={action.busy || (!file && content.trim().length < 20)}>{action.busy ? "Indexing..." : "Add & index"}</button>
        </div>
      </form>
    </Modal>
  );
}
