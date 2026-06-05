// iASK 2.0 PoC 前台

const STORAGE_KEY = 'iask_session';

const login = document.getElementById('login');
const chat = document.getElementById('chat');
const loginForm = document.getElementById('login-form');
const nameInput = document.getElementById('name-input');
const userLabel = document.getElementById('user-label');
const messages = document.getElementById('messages');
const askForm = document.getElementById('ask-form');
const qInput = document.getElementById('question-input');
const sendBtn = document.getElementById('send-btn');
const logoutBtn = document.getElementById('logout-btn');

let session = null;

function loadSession() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  try { session = JSON.parse(raw); } catch (e) { session = null; }
}

function saveSession() { localStorage.setItem(STORAGE_KEY, JSON.stringify(session)); }
function clearSession() { localStorage.removeItem(STORAGE_KEY); session = null; }

function showChat() {
  login.classList.add('hidden');
  chat.classList.remove('hidden');
  userLabel.textContent = `· ${session.display_name}`;
  qInput.focus();
  loadHistory();
}

function showLogin() {
  chat.classList.add('hidden');
  login.classList.remove('hidden');
  nameInput.focus();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// 簡化 markdown：**bold**、`code`、[label](url)、FAQ chip、換行、條列
function mdToHtml(s) {
  let h = escapeHtml(s);
  // code（避免 link/bold 與 code 交錯，先處理）
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  // 外部連結 [label](url) → 先抽成 placeholder 保護起來。
  // SharePoint URL 路徑會含 [LCC004] 這種字串，若不保護，後面 FAQ-chip 規則會
  // 把 URL 裡的 [LCC004] 換成 <a> 標籤、插斷 href 屬性，導致整個連結壞掉、裸 URL 噴出。
  const links = [];
  h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (m, label, url) => {
    const i = links.length;
    links.push(`<a href="${url}" target="_blank" rel="noopener">${label}</a>`);
    return `@@LINK${i}@@`;
  });
  // FAQ chip [PMC005] [TMC013]（此時外部連結已是 placeholder，不會被誤傷）
  h = h.replace(/\[([A-Z]{2,4}\d{2,4})\]/g, '<a class="faq-chip" data-faq-id="$1" href="javascript:void(0)">[$1]</a>');
  // bold
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // ordered list: 行首數字+. 變 <li>
  h = h.replace(/(^|\n)\s*\d+\.\s+(.+)/g, '$1<li>$2</li>');
  // unordered list: 行首 - 或 * 變 <li>
  h = h.replace(/(^|\n)\s*[-*]\s+(.+)/g, '$1<li>$2</li>');
  // 把連續 <li> 包成 <ul>（粗略夠用）
  h = h.replace(/(?:<li>.*?<\/li>\s*){2,}/g, m => `<ul>${m}</ul>`);
  h = h.replace(/<\/li>\s*<li>/g, '</li><li>');
  // 換行
  h = h.replace(/\n/g, '<br>');
  // 還原外部連結
  h = h.replace(/@@LINK(\d+)@@/g, (m, i) => links[+i]);
  return h;
}

// ---------- FAQ modal ----------

const faqModal = document.getElementById('faq-modal');
const faqModalBody = document.getElementById('faq-modal-body');
const faqModalClose = faqModal.querySelector('.modal-close');

async function showFAQ(faqId) {
  faqModalBody.innerHTML = '<p class="thinking">載入 ' + escapeHtml(faqId) + ' …</p>';
  faqModal.classList.remove('hidden');
  try {
    const resp = await fetch(`/api/faq/${encodeURIComponent(faqId)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const d = await resp.json();
    const linksHtml = (d.links || []).map(l =>
      `<a href="${escapeHtml(l.url || '#')}" target="_blank" rel="noopener" class="faq-link-button">${escapeHtml(l.name || 'link')}</a>`
    ).join('');
    faqModalBody.innerHTML = `
      <div class="faq-modal-header">
        ${d.dept ? `<span class="dept-tag">${escapeHtml(d.dept)}</span>` : ''}
        <span class="faq-id-label">[${escapeHtml(d.id)}]</span>
      </div>
      <h2>${escapeHtml(d.question || faqId)}</h2>
      ${linksHtml ? `<div class="faq-links">${linksHtml}</div>` : ''}
      <div class="faq-body">${mdToHtml(d.body || '')}</div>
      <div class="faq-meta">來源檔案：<code>${escapeHtml(d.path)}</code></div>
    `;
  } catch (e) {
    faqModalBody.innerHTML = `<p class="thinking">載入失敗：${escapeHtml(e.message)}</p>`;
  }
}

faqModalClose.addEventListener('click', () => faqModal.classList.add('hidden'));
faqModal.addEventListener('click', (e) => { if (e.target === faqModal) faqModal.classList.add('hidden'); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') faqModal.classList.add('hidden'); });

// chip 點擊 — 用 event delegation（messages 區）
document.addEventListener('click', (e) => {
  const a = e.target.closest('a.faq-chip');
  if (!a) return;
  e.preventDefault();
  const id = a.dataset.faqId;
  if (id) showFAQ(id);
});

function addMessage(role, text, opts = {}) {
  const div = document.createElement('div');
  div.className = `message ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  if (opts.thinking) {
    bubble.innerHTML =
      '<span class="thinking-dots"><span></span><span></span><span></span></span>' +
      '<span class="thinking">思考中 <span class="elapsed-running">0.0</span> 秒</span>';
  } else {
    bubble.innerHTML = role === 'user' ? escapeHtml(text).replace(/\n/g, '<br>') : mdToHtml(text);
  }
  div.appendChild(bubble);
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

async function loadHistory() {
  messages.innerHTML = '';
  if (!session) return;
  try {
    const resp = await fetch(`/api/history?conversation_id=${encodeURIComponent(session.conversation_id)}`);
    if (!resp.ok) { clearSession(); showLogin(); return; }
    const data = await resp.json();
    for (const item of data.items) {
      addMessage('user', item.question);
      addMessage('bot', item.answer);
    }
  } catch (e) {
    console.error(e);
  }
}

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = nameInput.value.trim();
  if (!name) return;
  try {
    const resp = await fetch('/api/session', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({display_name: name}),
    });
    if (!resp.ok) throw new Error(`session failed: ${resp.status}`);
    session = await resp.json();
    saveSession();
    showChat();
  } catch (err) {
    alert('進入失敗：' + err.message);
  }
});

logoutBtn.addEventListener('click', () => {
  if (confirm('確定要登出？對話紀錄會保留在後台，本機只清掉 session ID。')) {
    clearSession();
    showLogin();
  }
});

// IME 狀態旗標（中文輸入法時按 Enter 是「確認候選字」，不該觸發送出）
let imeComposing = false;
qInput.addEventListener('compositionstart', () => { imeComposing = true; });
qInput.addEventListener('compositionend',   () => { imeComposing = false; });

qInput.addEventListener('keydown', (e) => {
  // 三重守門：自家旗標 + 標準 isComposing + keyCode 229（IME 中的 Enter）
  if (imeComposing || e.isComposing || e.keyCode === 229) return;
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    askForm.requestSubmit();
  }
});

// 解析單一 SSE 事件區塊（以空白行分隔）成 {event, data}
function parseSSE(raw) {
  let event = 'message';
  const dataLines = [];
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
  }
  if (!dataLines.length) return null;
  try { return {event, data: JSON.parse(dataLines.join('\n'))}; }
  catch (e) { return null; }
}

askForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = qInput.value.trim();
  if (!q) return;
  qInput.value = '';
  qInput.disabled = true;
  sendBtn.disabled = true;
  addMessage('user', q);
  const placeholder = addMessage('bot', '', {thinking: true});
  const bubble = placeholder.querySelector('.message-bubble');

  // 計時：每 100ms 更新一次秒數（思考階段，首個 delta 到達前）。
  // 每次重查 .elapsed-running，因為 meta 階段會抽換 bubble 內容。
  const t0 = performance.now();
  const timer = setInterval(() => {
    const span = bubble.querySelector('.elapsed-running');
    if (span) span.textContent = ((performance.now() - t0) / 1000).toFixed(1);
  }, 100);

  const finishWith = (html) => {
    clearInterval(timer);
    bubble.classList.remove('streaming');
    bubble.innerHTML = html;
    messages.scrollTop = messages.scrollHeight;
  };

  let acc = '';        // 已累積的答案文字
  let streaming = false;

  try {
    const resp = await fetch('/api/ask/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversation_id: session.conversation_id, question: q}),
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let doneData = null;
    let errMsg = null;

    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const ev = parseSSE(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
        if (!ev) continue;
        if (ev.event === 'delta') {
          if (!streaming) {           // 首個 delta：清掉「思考中」、進入逐字模式
            streaming = true;
            bubble.classList.add('streaming');
            bubble.innerHTML = '';
          }
          acc += ev.data.text || '';
          bubble.innerHTML =
            escapeHtml(acc).replace(/\n/g, '<br>') + '<span class="stream-cursor"></span>';
          messages.scrollTop = messages.scrollHeight;
        } else if (ev.event === 'meta') {
          // 檢索完成：把「思考中」換成進度 + 候選 FAQ chip，填補答案生成前的等待
          if (!streaming) {
            const cands = ev.data.candidates || [];
            const chips = cands.map(id =>
              `<a class="faq-chip" data-faq-id="${escapeHtml(id)}" href="javascript:void(0)">[${escapeHtml(id)}]</a>`
            ).join(' ');
            const head = cands.length
              ? `已找到 ${cands.length} 篇相關 FAQ，正在整理答案 <span class="elapsed-running">0.0</span> 秒`
              : `正在整理答案 <span class="elapsed-running">0.0</span> 秒`;
            bubble.innerHTML =
              '<span class="thinking-dots"><span></span><span></span><span></span></span>' +
              `<span class="thinking">${head}</span>` +
              (chips ? `<div class="retrieved-chips">${chips}</div>` : '');
          }
        } else if (ev.event === 'done') {
          doneData = ev.data;
        } else if (ev.event === 'error') {
          errMsg = ev.data.message || 'unknown error';
        }
      }
    }

    const elapsedSec = ((performance.now() - t0) / 1000).toFixed(1);
    if (errMsg) {
      finishWith(`<span class="thinking">出錯了：${escapeHtml(errMsg)}</span>`);
    } else {
      // done 事件帶回含來源連結的完整答案；萬一沒收到 done 就用已累積內容收尾
      const finalAnswer = doneData ? doneData.answer : acc;
      finishWith(mdToHtml(finalAnswer) + `<div class="elapsed">${elapsedSec} 秒</div>`);
    }
  } catch (err) {
    finishWith(`<span class="thinking">出錯了：${escapeHtml(err.message)}</span>`);
  } finally {
    qInput.disabled = false;
    sendBtn.disabled = false;
    qInput.focus();
  }
});

// boot
loadSession();
if (session && session.conversation_id) {
  showChat();
} else {
  showLogin();
}
