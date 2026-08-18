// ODI Engine - Premium Web App Logic
const SESSION_ID = 'session_' + Math.random().toString(36).substring(2, 15);
let isUploading = false;

// Initialize Markdown parser options
marked.setOptions({
    highlight: function(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    },
    breaks: true
});

// DOM Elements
const docList = document.getElementById('docList');
const docCount = document.getElementById('docCount');
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const messagesContainer = document.getElementById('messages');
const messagesWrapper = document.getElementById('messagesWrapper');
const questionInput = document.getElementById('questionInput');
const sendBtn = document.getElementById('sendBtn');
const uploadProgress = document.getElementById('uploadProgress').firstElementChild;
const toastContainer = document.getElementById('toastContainer');

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    loadDocuments();
    setInterval(loadDocuments, 30000); // Poll every 30s
});

// --- API Interactions ---
async function loadDocuments() {
    try {
        const res = await fetch('/api/documents');
        if (!res.ok) throw new Error('Failed to fetch documents');
        const docs = await res.json();
        
        docCount.textContent = docs.length;
        
        if (docs.length === 0) {
            docList.innerHTML = `
                <div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">
                    No documents indexed yet.
                </div>
            `;
            return;
        }

        docList.innerHTML = docs.map(d => `
            <div class="doc-item">
                <div class="doc-icon">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                </div>
                <div class="doc-info">
                    <div class="doc-name" title="${d.title || d.document_id}">${d.title || 'Untitled Document'}</div>
                    <div class="doc-meta">${d.page_count} Pages · ${d.chunk_count} Chunks</div>
                </div>
                <button class="del-btn" onclick="deleteDocument('${d.document_id}', event)" title="Remove from Index">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                </button>
            </div>
        `).join('');
    } catch (err) {
        console.error('Document load error:', err);
    }
}

async function deleteDocument(id, event) {
    event.stopPropagation();
    if (!confirm('Are you sure you want to remove this document from the local index?')) return;
    
    try {
        const res = await fetch(`/api/documents/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Deletion failed');
        showToast('Document removed successfully', 'success');
        loadDocuments();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// --- Drag & Drop Uploads ---
fileInput.addEventListener('change', (e) => {
    handleFiles(Array.from(e.target.files));
    e.target.value = ''; // reset
});

dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('drag-active');
});

dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-active'));
dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag-active');
    handleFiles(Array.from(e.dataTransfer.files));
});

async function handleFiles(files) {
    const pdfs = files.filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfs.length === 0) {
        showToast('Only PDF files are supported.', 'error');
        return;
    }

    if (isUploading) {
        showToast('An upload is already in progress.', 'warning');
        return;
    }

    isUploading = true;
    dropzone.classList.add('uploading');

    for (const file of pdfs) {
        showToast(`Ingesting ${file.name}...`, 'info');
        const formData = new FormData();
        formData.append('file', file);
        
        try {
            // Simulated progress for UI flair
            uploadProgress.style.width = '30%';
            
            const res = await fetch('/api/ingest', {
                method: 'POST',
                body: formData
            });
            
            uploadProgress.style.width = '80%';
            const data = await res.json();
            
            if (!res.ok) throw new Error(data.detail || 'Upload failed');
            
            if (data.status === 'already_ingested') {
                showToast(`Already indexed: ${file.name}`, 'info');
            } else {
                showToast(`Successfully indexed ${data.page_count} pages!`, 'success');
            }
        } catch (err) {
            showToast(`Error uploading ${file.name}: ${err.message}`, 'error');
        } finally {
            uploadProgress.style.width = '100%';
            setTimeout(() => {
                uploadProgress.style.width = '0%';
            }, 500);
            await loadDocuments();
        }
    }
    
    dropzone.classList.remove('uploading');
    isUploading = false;
}

// --- Chat Interface ---
function setInput(text) {
    questionInput.value = text;
    questionInput.style.height = questionInput.scrollHeight + 'px';
    questionInput.focus();
}

async function sendQuestion() {
    const q = questionInput.value.trim();
    if (!q) return;

    // Reset input
    questionInput.value = '';
    questionInput.style.height = 'auto';
    sendBtn.disabled = true;

    // Remove empty state if present
    const emptyState = messagesContainer.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    // Add User Message
    appendMessage('user', q);
    
    // Add Typing Indicator
    const typingId = appendTypingIndicator();
    scrollToBottom();

    try {
        const res = await fetch('/api/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: q, session_id: SESSION_ID })
        });
        
        const data = await res.json();
        removeElement(typingId);
        
        if (!res.ok) {
            appendMessage('ai', `**Error:** ${data.detail || 'Failed to get answer'}`);
            return;
        }

        appendMessage('ai', data.answer_text, data);
    } catch (err) {
        removeElement(typingId);
        appendMessage('ai', `**Network Error:** ${err.message}`);
    } finally {
        sendBtn.disabled = false;
        scrollToBottom();
    }
}

function appendMessage(role, text, meta = null) {
    const div = document.createElement('div');
    div.className = 'message';
    
    const isUser = role === 'user';
    const avatar = isUser 
        ? `<div class="avatar user"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg></div>`
        : `<div class="avatar ai"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"></path></svg></div>`;
    
    let contentHtml = `<div class="msg-bubble">${marked.parse(text)}</div>`;
    
    // Add metadata/citations for AI responses
    if (!isUser && meta) {
        let metaHtml = `<div class="msg-meta">`;
        
        if (meta.confidence) {
            const confClass = meta.confidence === 'HIGH' ? 'conf-high' 
                            : meta.confidence === 'MEDIUM' ? 'conf-medium' 
                            : 'conf-low';
            const tooltip = `Retrieval: ${(meta.retrieval_score*100).toFixed(0)}%&#10;Evidence: ${(meta.evidence_score*100).toFixed(0)}%&#10;Answerability: ${(meta.answerability_score*100).toFixed(0)}%`;
            metaHtml += `<span class="confidence-badge ${confClass}" title="${tooltip}">${meta.confidence} Confidence</span>`;
        }
        
        if (meta.route) {
            metaHtml += `<span>Route: ${meta.route}</span>`;
        }
        
        if (meta.elapsed_ms) {
            metaHtml += `<span>${Math.round(meta.elapsed_ms)}ms</span>`;
        }
        
        metaHtml += `</div>`;
        
        if (meta.citations && meta.citations.length > 0) {
            const uniquePages = [...new Set(meta.citations.map(c => c.page_number))].sort((a,b)=>a-b);
            metaHtml += `
                <div class="citations">
                    ${uniquePages.map(p => `
                        <span class="citation-tag" title="Source evidence found on this page">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
                            Page ${p}
                        </span>
                    `).join('')}
                </div>
            `;
        }
        
        contentHtml += metaHtml;

        const debugToggle = document.getElementById('debugToggle');
        if (debugToggle && debugToggle.checked && meta.debug_trace) {
            contentHtml += `
                <details class="debug-trace-panel" style="margin-top: 15px; font-size: 0.8rem; background: var(--bg-primary); padding: 10px; border-radius: 8px; border: 1px solid var(--border);">
                    <summary style="cursor: pointer; font-weight: 600; color: var(--text-muted); margin-bottom: 5px;">🔧 View Debug Trace</summary>
                    <pre style="white-space: pre-wrap; word-wrap: break-word; color: var(--text); overflow-x: auto; max-height: 400px; overflow-y: auto;">${JSON.stringify(meta.debug_trace, null, 2)}</pre>
                </details>
            `;
        }
    }

    div.innerHTML = `
        ${avatar}
        <div class="msg-content">
            ${contentHtml}
        </div>
    `;
    
    messagesContainer.appendChild(div);
}

function appendTypingIndicator() {
    const id = 'typing_' + Date.now();
    const div = document.createElement('div');
    div.className = 'message';
    div.id = id;
    div.innerHTML = `
        <div class="avatar ai"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"></path></svg></div>
        <div class="msg-content">
            <div class="msg-bubble">
                <div class="typing-indicator">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>
            </div>
        </div>
    `;
    messagesContainer.appendChild(div);
    return id;
}

function removeElement(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function scrollToBottom() {
    messagesWrapper.scrollTop = messagesWrapper.scrollHeight;
}

function clearSession() {
    messagesContainer.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            </div>
            <h3>Session Cleared</h3>
            <p>Ready for a new line of questioning.</p>
        </div>
    `;
}

// --- UI Utils ---
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = 'toast';
    
    const icon = type === 'success' ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--success)" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>'
               : type === 'error' ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>'
               : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';

    toast.innerHTML = `${icon} <span>${message}</span>`;
    toastContainer.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
