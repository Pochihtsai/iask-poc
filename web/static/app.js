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

// 依「行首縮排深度」把條列建成巢狀 <ul>/<ol>。
// 舊版用單一 regex 把每個 <li> 攤平塞進同一層 <ul>，縮排資訊整個丟掉，
// 導致次層 bullet 與首層擠在同一排。改用縮排堆疊：縮排變深→開新一層子清單，
// 變淺→收掉子清單。子清單的不同 bullet 符號交給 CSS（ul ul / ul ul ul）處理。
function renderNestedLists(text) {
  const lines = text.split('\n');
  const out = [];
  const stack = []; // 每層 { indent, type:'ul'|'ol', liOpen }
  const closeTop = () => {
    const top = stack.pop();
    if (top.liOpen) out.push('</li>');
    out.push(top.type === 'ol' ? '</ol>' : '</ul>');
  };
  const closeTo = (indent) => { while (stack.length && stack[stack.length - 1].indent > indent) closeTop(); };
  const closeAll = () => { while (stack.length) closeTop(); };
  let pendingBlanks = 0;
  const flushBlanks = () => { while (pendingBlanks > 0) { out.push(''); pendingBlanks--; } };
  for (const line of lines) {
    const m = line.match(/^(\s*)(\d+\.|[-*])\s+(.*)$/);
    if (!m) {
      // 清單中間的空行：先緩存、不要關閉清單，否則 <ol> 編號會重新從 1 開始（變成整串都是 1.）。
      if (line.trim() === '' && stack.length) { pendingBlanks++; continue; }
      // 有縮排、非清單標記的文字：視為上一個 <li> 的接續內容，掛在它後面，別關掉清單。
      if (stack.length && /^\s+\S/.test(line) && stack[stack.length - 1].liOpen) {
        flushBlanks(); out.push('<br>' + line.trim()); continue;
      }
      // 真正的非清單內容 → 收掉清單，並補回先前緩存的空行。
      closeAll(); flushBlanks(); out.push(line); continue;
    }
    pendingBlanks = 0; // 是清單項目 → 同一份清單延續，丟掉中間緩存的空行
    const indent = m[1].length;
    const type = /\d+\./.test(m[2]) ? 'ol' : 'ul';
    closeTo(indent);
    let top = stack[stack.length - 1];
    if (top && top.indent === indent) {
      // 同層的下一個項目：先收掉上一個 <li>
      if (top.liOpen) { out.push('</li>'); top.liOpen = false; }
      if (top.type !== type) { // 同縮排但清單型別不同 → 收掉重開
        out.push(top.type === 'ol' ? '</ol>' : '</ul>');
        stack.pop();
        out.push(type === 'ol' ? '<ol>' : '<ul>');
        stack.push(top = { indent, type, liOpen: false });
      }
    } else {
      // 縮排變深（或第一層）→ 在目前仍開著的 <li> 內開一層子清單
      out.push(type === 'ol' ? '<ol>' : '<ul>');
      stack.push(top = { indent, type, liOpen: false });
    }
    out.push('<li>' + m[3]);
    top.liOpen = true;
  }
  closeAll();
  flushBlanks();
  return out.join('\n');
}

// 簡化 markdown：**bold**、`code`、[label](url)、FAQ chip、換行、條列
function mdToHtml(s) {
  let h = escapeHtml(s);
  // code（避免 link/bold 與 code 交錯，先處理）
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  // 外部連結 [label](url) → 先抽成 placeholder 保護起來。
  // SharePoint URL 路徑會含 [LCC004] 這種字串，若不保護，後面 FAQ-chip 規則會
  // 把 URL 裡的 [LCC004] 換成 <a> 標籤、插斷 href 屬性，導致整個連結壞掉、裸 URL 噴出。
  // 核心問題：label 常以 [LCC002] 開頭、URL 路徑含未編碼的 [ ] 與 ( )（如 (海外公司適用)），
  // 舊的 [^\]]+ / [^\s)]+ 規則會在第一個 ] 或 ) 提早截斷，導致整段連結 match 失敗而噴出裸網址。
  // 改用允許一層巢狀 [] 的 label 與一層巢狀 () 的 URL 的平衡式規則（已用 295 筆實際連結驗證全數通過）。
  const links = [];
  h = h.replace(/\[((?:[^\[\]]|\[[^\]]*\])*)\]\((https?:\/\/(?:[^()\s]|\([^()]*\))*)\)/g, (m, label, url) => {
    const i = links.length;
    links.push(`<a href="${url}" target="_blank" rel="noopener">${label}</a>`);
    return `@@LINK${i}@@`;
  });
  // FAQ chip [PMC005] [TMC013]（此時外部連結已是 placeholder，不會被誤傷）
  h = h.replace(/\[([A-Z]{2,4}\d{2,4})\]/g, '<a class="faq-chip" data-faq-id="$1" href="javascript:void(0)">[$1]</a>');
  // bold
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // 條列：依縮排建立巢狀 <ul>/<ol>（次層自動縮排，符號由 CSS 區分）
  h = renderNestedLists(h);
  // 換行
  h = h.replace(/\n/g, '<br>');
  // 清掉黏在 list 區塊標籤旁的多餘 <br>，免得清單內外被塞空行
  h = h.replace(/<br>\s*(<\/?(?:ul|ol|li)>)/g, '$1');
  h = h.replace(/(<\/?(?:ul|ol|li)>)\s*<br>/g, '$1');
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
