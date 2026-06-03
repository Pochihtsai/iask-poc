// iASK 2.0 PoC 後台

const tbody = document.querySelector('#queries-table tbody');
const filterUser = document.getElementById('filter-user');
const filterSince = document.getElementById('filter-since');
const refreshBtn = document.getElementById('refresh-btn');
const modal = document.getElementById('detail-modal');
const modalBody = document.getElementById('detail-body');
const modalClose = document.querySelector('.modal-close');

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

function tagList(arr) {
  return (arr || []).map(c => `<span class="tag">${escapeHtml(c)}</span>`).join('');
}

async function loadQueries() {
  const params = new URLSearchParams();
  if (filterUser.value.trim()) params.set('user', filterUser.value.trim());
  if (filterSince.value) params.set('since', filterSince.value);
  const resp = await fetch('/admin/api/queries?' + params);
  if (!resp.ok) {
    tbody.innerHTML = `<tr><td colspan="7">載入失敗：${resp.status}</td></tr>`;
    return;
  }
  const data = await resp.json();
  tbody.innerHTML = '';
  if (data.items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:#9ca3af; padding:2rem">目前沒有紀錄</td></tr>';
    return;
  }
  for (const r of data.items) {
    const tr = document.createElement('tr');
    tr.dataset.id = r.id;
    tr.innerHTML = `
      <td>${r.id}</td>
      <td>${escapeHtml(r.created_at)}</td>
      <td>${escapeHtml(r.display_name)}</td>
      <td class="question" title="${escapeHtml(r.question)}">${escapeHtml(truncate(r.question, 80))}</td>
      <td><div class="tag-list">${tagList(r.candidates)}</div></td>
      <td>$${(r.cost_usd || 0).toFixed(4)}</td>
      <td>${((r.latency_ms || 0)/1000).toFixed(1)}s</td>
    `;
    tr.addEventListener('click', () => showDetail(r.id));
    tbody.appendChild(tr);
  }
}

async function showDetail(qid) {
  const resp = await fetch(`/admin/api/queries/${qid}`);
  if (!resp.ok) { alert('載入失敗：' + resp.status); return; }
  const d = await resp.json();
  let cands = [], signals = [];
  try { cands = JSON.parse(d.candidates || '[]'); } catch (e) {}
  try { signals = JSON.parse(d.signal_terms || '[]'); } catch (e) {}

  modalBody.innerHTML = `
    <h2 style="margin-bottom:1rem; color:#1e3a8a;">Q${d.id} · ${escapeHtml(d.display_name)} · ${escapeHtml(d.created_at)}</h2>

    <div class="detail-section">
      <h3>使用者問題</h3>
      <pre>${escapeHtml(d.question)}</pre>
      ${d.rewritten_question && d.rewritten_question !== d.question
        ? `<p style="margin-top:0.5rem"><strong>Rewriter 改寫成 standalone：</strong></p><pre>${escapeHtml(d.rewritten_question)}</pre>`
        : `<p style="margin-top:0.5rem; color:#6b7280; font-size:0.85rem">${d.rewritten_question ? '（rewriter 判定已自包含、原樣回傳）' : '（單題、無 rewriter）'}</p>`
      }
    </div>

    <div class="detail-section">
      <h3>系統答案</h3>
      <pre>${escapeHtml(d.answer)}</pre>
    </div>

    <div class="detail-section">
      <h3>Ladder Debug</h3>
      <p><strong>Signal terms：</strong>${tagList(signals)}</p>
      <p><strong>讀過的 FAQ：</strong>${tagList(cands)}</p>
      <p><strong>Selector reasoning：</strong>${escapeHtml(d.reasoning || '(無)')}</p>
    </div>

    <div class="detail-section">
      <h3>Metrics</h3>
      <pre>model:        ${escapeHtml(d.model)}
fresh in:     ${d.tokens_in}
cached in:    ${d.tokens_cached}
output:       ${d.tokens_out}
cost:         $${(d.cost_usd || 0).toFixed(4)}
latency:      ${d.latency_ms}ms</pre>
    </div>
  `;
  modal.classList.remove('hidden');
}

modalClose.addEventListener('click', () => modal.classList.add('hidden'));
modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.add('hidden'); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') modal.classList.add('hidden'); });
refreshBtn.addEventListener('click', loadQueries);
filterUser.addEventListener('change', loadQueries);
filterSince.addEventListener('change', loadQueries);

// boot
loadQueries();
