"""
ODI Engine — Web API Server (Phase 16)

FastAPI-based REST API for the document intelligence pipeline.
Serves both the API endpoints and the web UI (static files).

Endpoints:
  POST /api/ingest          — Upload and ingest a PDF
  POST /api/ask             — Ask a question
  GET  /api/documents       — List ingested documents
  DELETE /api/documents/{id} — Delete a document
  GET  /api/history/{session} — Chat history
  GET  /api/health          — Health check
  GET  /                    — Web UI (static HTML)

Usage:
    python app.py
    # or
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Suppress noisy pdfminer warnings
logging.getLogger("pdfminer").setLevel(logging.ERROR)

log = logging.getLogger(__name__)

# ── App state ─────────────────────────────────────────────────────────────────

STORE_DIR = Path(".odi_store")
UPLOAD_DIR = STORE_DIR / "uploads"


class AppState:
    pipeline = None
    db = None


state = AppState()


def _init_app() -> None:
    """Initialize pipeline and database on startup."""
    STORE_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)

    from config import load_config
    from core.pipeline import DocumentPipeline
    from storage.database import Database

    cfg = load_config()
    
    state.pipeline = DocumentPipeline(cfg.pipeline_config())
    state.db = Database(cfg.db_path)
    log.info("ODI Engine initialized — store at %s", cfg.store_dir)


# ── FastAPI app ────────────────────────────────────────────────────────────────

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from pydantic import BaseModel

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _init_app()
        yield

    app = FastAPI(
        title="ODI Engine API",
        description="Offline Document Intelligence Engine — privacy-first, completely local",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from fastapi.staticfiles import StaticFiles
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    # ── Request / Response models ──────────────────────────────────────────────

    class AskRequest(BaseModel):
        question: str
        document_id: Optional[str] = None
        session_id: str = "default"

    class AskResponse(BaseModel):
        answer_id: str
        question: str
        answer_text: str
        route: str
        confidence: str
        confidence_score: float
        retrieval_score: float = 0.0
        evidence_score: float = 0.0
        answerability_score: float = 0.0
        citations: list[dict]
        elapsed_ms: float
        is_no_answer: bool
        debug_trace: Optional[dict] = None

    class DocumentInfo(BaseModel):
        document_id: str
        title: Optional[str]
        page_count: int
        chunk_count: int
        ingested_at: str

    # ── Endpoints ──────────────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "documents": len(state.db.list_documents()) if state.db else 0,
        }

    @app.post("/api/ingest")
    async def ingest(file: UploadFile = File(...)):
        """Upload and ingest a PDF document."""
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Only PDF files are accepted.")

        # Save upload
        upload_path = UPLOAD_DIR / f"{uuid.uuid4()}_{file.filename}"
        content = await file.read()
        upload_path.write_bytes(content)

        try:
            from core.document.models import ParsedDocument
            from storage.database import DocumentRecord, ChunkRecord

            # Check duplicate
            file_hash = _sha256_bytes(content)
            existing = state.db.get_document_by_hash(file_hash)
            if existing:
                return JSONResponse({
                    "status": "already_ingested",
                    "document_id": existing.document_id,
                    "message": f"Document already indexed as '{existing.title}'",
                })

            doc = state.pipeline.ingest(upload_path)

            # Persist to DB
            state.db.insert_document(DocumentRecord(
                document_id=doc.document_id,
                file_path=str(upload_path),
                file_hash=file_hash,
                title=doc.title or file.filename,
                page_count=doc.page_count,
                chunk_count=doc.chunk_count,
                embedding_model_id=state.pipeline._embedding_engine.model_id,
            ))
            chunks = state.pipeline._chunks.get(doc.document_id, [])
            state.db.insert_chunks([
                ChunkRecord(
                    chunk_id=c.chunk_id, document_id=c.document_id,
                    page_id=c.page_id, text=c.text, token_count=c.token_count,
                    chunk_index=c.chunk_index, strategy=c.strategy.value,
                    section_id=c.section_id, parent_chunk_id=c.parent_chunk_id,
                )
                for c in chunks
            ])

            return JSONResponse({
                "status": "ingested",
                "document_id": doc.document_id,
                "title": doc.title,
                "page_count": doc.page_count,
                "chunk_count": doc.chunk_count,
            })
        except Exception as exc:
            log.exception("Ingestion failed: %s", exc)
            upload_path.unlink(missing_ok=True)
            raise HTTPException(500, f"Ingestion failed: {exc}")

    @app.post("/api/ask", response_model=AskResponse)
    async def ask(req: AskRequest):
        """Answer a natural-language question."""
        if not req.question.strip():
            raise HTTPException(400, "Question must not be empty.")

        if not state.pipeline.documents:
            # Try reloading from DB
            docs = state.db.list_documents()
            for d in docs:
                p = Path(d.file_path)
                if p.exists():
                    try:
                        state.pipeline.ingest(p)
                    except Exception:
                        pass

        if not state.pipeline.documents:
            raise HTTPException(400, "No documents ingested yet. Please upload a PDF first.")

        try:
            result = state.pipeline.ask(req.question, document_id=req.document_id)

            # Persist to DB
            from storage.database import MessageRecord
            state.db.insert_message(MessageRecord(
                message_id=str(uuid.uuid4()), session_id=req.session_id,
                role="user", content=req.question,
            ))
            state.db.insert_message(MessageRecord(
                message_id=str(uuid.uuid4()), session_id=req.session_id,
                role="assistant", content=result.answer.plain_text(),
                route=result.route, confidence=result.confidence,
            ))
            
            import dataclasses

            return AskResponse(
                answer_id=result.answer.answer_id,
                question=req.question,
                answer_text=result.answer.plain_text(),
                route=result.route,
                confidence=result.confidence,
                confidence_score=result.answer.confidence_score,
                retrieval_score=result.answer.retrieval_score,
                evidence_score=result.answer.evidence_score,
                answerability_score=result.answer.answerability_score,
                citations=[c.to_dict() for c in result.citations.values()],
                elapsed_ms=result.elapsed_ms,
                is_no_answer=result.answer.is_no_answer,
                debug_trace=dataclasses.asdict(result.debug_trace) if result.debug_trace else None,
            )
        except Exception as exc:
            log.exception("Ask failed: %s", exc)
            raise HTTPException(500, f"Query failed: {exc}")

    @app.get("/api/documents", response_model=list[DocumentInfo])
    async def list_documents():
        docs = state.db.list_documents()
        return [DocumentInfo(
            document_id=d.document_id,
            title=d.title,
            page_count=d.page_count,
            chunk_count=d.chunk_count,
            ingested_at=d.ingested_at,
        ) for d in docs]

    @app.delete("/api/documents/{document_id}")
    async def delete_document(document_id: str):
        doc = state.db.get_document(document_id)
        if not doc:
            raise HTTPException(404, "Document not found.")
        state.db.delete_document(document_id)
        return {"status": "deleted", "document_id": document_id}

    @app.get("/api/history/{session_id}")
    async def get_history(session_id: str, limit: int = 50):
        msgs = state.db.get_messages(session_id, limit=limit)
        return [
            {
                "role": m.role, "content": m.content,
                "created_at": m.created_at, "route": m.route,
                "confidence": m.confidence,
            }
            for m in msgs
        ]

    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Serve the web UI."""
        ui_path = Path(__file__).parent / "web" / "index.html"
        if ui_path.exists():
            return FileResponse(str(ui_path))
        return HTMLResponse(_EMBEDDED_UI)

    # ── Static file helper ─────────────────────────────────────────────────────

    def _sha256_bytes(data: bytes) -> str:
        import hashlib
        return hashlib.sha256(data).hexdigest()

except ImportError:
    # FastAPI not installed — provide a helpful error
    import sys
    class app:  # type: ignore
        pass
    print("[ERROR] FastAPI not installed. Run: pip install fastapi uvicorn[standard]", file=sys.stderr)


# ── Embedded minimal UI ────────────────────────────────────────────────────────

_EMBEDDED_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>ODI Engine — Offline Document Intelligence</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --surface2: #252836;
    --accent: #6c63ff; --accent2: #ff6584; --text: #e8eaf6;
    --text2: #9da5c7; --success: #43d9ad; --warn: #ffb347;
    --border: #2e3150; --radius: 12px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 32px;
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.25rem; font-weight: 700; }
  header span { color: var(--accent); font-size: 0.8rem; padding: 2px 10px; border: 1px solid var(--accent);
                border-radius: 20px; }
  .container { display: grid; grid-template-columns: 320px 1fr; gap: 0; height: calc(100vh - 57px); }
  .sidebar { background: var(--surface); border-right: 1px solid var(--border); display: flex;
             flex-direction: column; overflow: hidden; }
  .sidebar-header { padding: 20px; font-weight: 600; font-size: 0.85rem; text-transform: uppercase;
                    letter-spacing: 1px; color: var(--text2); border-bottom: 1px solid var(--border); }
  .upload-zone { margin: 16px; border: 2px dashed var(--border); border-radius: var(--radius);
                 padding: 24px; text-align: center; cursor: pointer; transition: all 0.2s; }
  .upload-zone:hover { border-color: var(--accent); background: rgba(108,99,255,0.05); }
  .upload-zone input { display: none; }
  .upload-icon { font-size: 2rem; margin-bottom: 8px; }
  .upload-zone p { color: var(--text2); font-size: 0.85rem; }
  .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600;
         font-size: 0.875rem; transition: all 0.2s; }
  .btn-primary { background: var(--accent); color: white; }
  .btn-primary:hover { background: #5a52e0; transform: translateY(-1px); }
  .btn-sm { padding: 6px 12px; font-size: 0.8rem; }
  .btn-danger { background: transparent; color: var(--accent2); border: 1px solid var(--accent2); }
  .doc-list { flex: 1; overflow-y: auto; padding: 8px 16px; }
  .doc-item { background: var(--surface2); border-radius: var(--radius); padding: 12px; margin-bottom: 8px;
              border: 1px solid var(--border); display: flex; align-items: center; gap: 10px; }
  .doc-icon { font-size: 1.5rem; }
  .doc-info { flex: 1; min-width: 0; }
  .doc-name { font-weight: 600; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .doc-meta { color: var(--text2); font-size: 0.75rem; margin-top: 2px; }
  .chat-area { display: flex; flex-direction: column; height: 100%; }
  .messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
  .msg { max-width: 80%; }
  .msg-user { align-self: flex-end; }
  .msg-assistant { align-self: flex-start; }
  .msg-bubble { padding: 14px 18px; border-radius: var(--radius); line-height: 1.6; font-size: 0.9rem; }
  .msg-user .msg-bubble { background: var(--accent); color: white; border-bottom-right-radius: 4px; }
  .msg-assistant .msg-bubble { background: var(--surface); border: 1px solid var(--border);
                                border-bottom-left-radius: 4px; }
  .msg-meta { font-size: 0.75rem; color: var(--text2); margin-top: 4px; padding: 0 4px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 0.7rem; font-weight: 600; margin-right: 4px; }
  .badge-high { background: rgba(67,217,173,0.15); color: var(--success); }
  .badge-medium { background: rgba(255,179,71,0.15); color: var(--warn); }
  .badge-low { background: rgba(255,101,132,0.15); color: var(--accent2); }
  .input-area { padding: 16px 24px; border-top: 1px solid var(--border); background: var(--surface); }
  .input-row { display: flex; gap: 10px; }
  .input-row input { flex: 1; background: var(--surface2); border: 1px solid var(--border); border-radius: 10px;
                     padding: 12px 16px; color: var(--text); font-size: 0.9rem; outline: none; transition: border 0.2s; }
  .input-row input:focus { border-color: var(--accent); }
  .empty-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
                 color: var(--text2); gap: 8px; }
  .empty-state .big-icon { font-size: 4rem; opacity: 0.3; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.3);
             border-top-color: white; border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .toast { position: fixed; bottom: 24px; right: 24px; background: var(--surface2); border: 1px solid var(--border);
           border-radius: var(--radius); padding: 12px 20px; font-size: 0.875rem; z-index: 100;
           animation: slideup 0.3s ease; }
  @keyframes slideup { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
  pre { white-space: pre-wrap; word-break: break-word; font-family: inherit; }
  .citation-bar { font-size: 0.75rem; color: var(--text2); margin-top: 8px; border-top: 1px solid var(--border); padding-top: 8px; }
</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body>
<header>
  <span style="font-size:1.4rem">📄</span>
  <h1>ODI Engine</h1>
  <span>Offline · Private · No Cloud</span>
  <div style="flex:1"></div>
  <span id="status-dot" style="width:10px;height:10px;border-radius:50%;background:#43d9ad;display:inline-block" title="Online"></span>
</header>
<div class="container">
  <aside class="sidebar">
    <div class="sidebar-header">Documents</div>
    <div class="upload-zone" onclick="document.getElementById('fileInput').click()" id="dropzone">
      <input type="file" id="fileInput" accept=".pdf" multiple>
      <div class="upload-icon">📤</div>
      <p>Drop PDFs here or click to upload</p>
    </div>
    <div class="doc-list" id="docList">
      <div style="text-align:center;color:var(--text2);padding:20px;font-size:0.85rem">Loading…</div>
    </div>
  </aside>
  <main class="chat-area">
    <div class="messages" id="messages">
      <div class="empty-state">
        <div class="big-icon">💬</div>
        <p style="font-weight:600">Ask anything about your documents</p>
        <p style="font-size:0.85rem">Upload a PDF first, then ask natural language questions</p>
      </div>
    </div>
    <div class="input-area">
      <div class="input-row">
        <input type="text" id="questionInput" placeholder="Ask a question about your documents…"
               onkeydown="if(event.key==='Enter')sendQuestion()">
        <button class="btn btn-primary" onclick="sendQuestion()" id="sendBtn">Send</button>
      </div>
    </div>
  </main>
</div>
<script>
const SESSION_ID = 'session_' + Date.now();
let uploading = false;

async function loadDocs() {
  const res = await fetch('/api/documents');
  const docs = await res.json();
  const el = document.getElementById('docList');
  if (!docs.length) {
    el.innerHTML = '<div style="text-align:center;color:var(--text2);padding:20px;font-size:0.85rem">No documents yet.<br>Upload a PDF to get started.</div>';
    return;
  }
  el.innerHTML = docs.map(d => `
    <div class="doc-item">
      <div class="doc-icon">📄</div>
      <div class="doc-info">
        <div class="doc-name" title="${d.title || d.document_id}">${d.title || 'Untitled'}</div>
        <div class="doc-meta">${d.page_count}p · ${d.chunk_count} chunks · ${d.ingested_at.slice(0,10)}</div>
      </div>
      <button class="btn btn-sm btn-danger" onclick="deleteDoc('${d.document_id}')">✕</button>
    </div>
  `).join('');
}

async function deleteDoc(id) {
  if (!confirm('Delete this document from the index?')) return;
  await fetch('/api/documents/' + id, { method: 'DELETE' });
  loadDocs();
  toast('Document removed from index.');
}

document.getElementById('fileInput').addEventListener('change', async (e) => {
  const files = Array.from(e.target.files);
  for (const f of files) {
    if (!f.name.endsWith('.pdf')) { toast('Only PDF files are accepted.'); continue; }
    await uploadFile(f);
  }
  e.target.value = '';
});

const dz = document.getElementById('dropzone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.style.borderColor='var(--accent)'; });
dz.addEventListener('dragleave', () => { dz.style.borderColor=''; });
dz.addEventListener('drop', async e => {
  e.preventDefault(); dz.style.borderColor='';
  const files = Array.from(e.dataTransfer.files).filter(f => f.name.endsWith('.pdf'));
  for (const f of files) await uploadFile(f);
});

async function uploadFile(file) {
  if (uploading) return;
  uploading = true;
  toast(`Uploading ${file.name}…`);
  const fd = new FormData(); fd.append('file', file);
  try {
    const res = await fetch('/api/ingest', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.status === 'ingested') toast(`✓ Indexed: ${data.title} (${data.page_count}p, ${data.chunk_count} chunks)`);
    else if (data.status === 'already_ingested') toast(`⚠ Already indexed: ${data.message}`);
    else toast(`Error: ${JSON.stringify(data)}`);
    loadDocs();
  } catch(err) { toast('Upload failed: ' + err); }
  finally { uploading = false; }
}

async function sendQuestion() {
  const input = document.getElementById('questionInput');
  const q = input.value.trim();
  if (!q) return;
  input.value = '';

  addMessage('user', q, null);
  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, session_id: SESSION_ID }),
    });
    const data = await res.json();
    if (!res.ok) { addMessage('assistant', '⚠ ' + (data.detail || 'Error'), null); return; }
    addMessage('assistant', data.answer_text, data);
  } catch(err) {
    addMessage('assistant', '⚠ Request failed: ' + err, null);
  } finally {
    btn.disabled = false; btn.innerHTML = 'Send';
  }
}

function addMessage(role, text, meta) {
  const msgs = document.getElementById('messages');
  // Remove empty state
  const empty = msgs.querySelector('.empty-state');
  if (empty) empty.remove();

  const div = document.createElement('div');
  div.className = 'msg msg-' + role;

  let bubble = `<div class="msg-bubble"><pre>${escHtml(text)}</pre>`;
  if (meta && meta.citations && meta.citations.length > 0) {
    const pages = [...new Set(meta.citations.map(c => c.page_number))].sort((a,b)=>a-b);
    bubble += `<div class="citation-bar">📖 Sources: ${pages.map(p=>'p.'+p).join(', ')}</div>`;
  }
  bubble += '</div>';

  let metaHtml = '';
  if (meta) {
    const conf = (meta.confidence||'').toLowerCase();
    const badgeClass = conf === 'high' ? 'badge-high' : conf === 'medium' ? 'badge-medium' : 'badge-low';
    metaHtml = `<div class="msg-meta">
      <span class="badge ${badgeClass}">${meta.confidence||''}</span>
      <span>${meta.route||''}</span> · ${meta.elapsed_ms?.toFixed(0)||'?'}ms
    </div>`;
  }

  div.innerHTML = bubble + metaHtml;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function toast(msg) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// Init
loadDocs();
setInterval(loadDocs, 30000);
</script>
</body>
</html>"""


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )
