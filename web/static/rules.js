// iASK 2.0 PoC 後台 · 相關連結規則

const tbody = document.querySelector('#rules-table tbody');
const modal = document.getElementById('rule-modal');
const modalTitle = document.getElementById('rule-modal-title');
const form = document.getElementById('rule-form');
const addBtn = document.getElementById('add-rule-btn');
const cancelBtn = document.getElementById('r-cancel-btn');
const closeBtn = modal.querySelector('.modal-close');

const fields = {
  enabled: document.getElementById('r-enabled'),
  keyword: document.getElementById('r-keyword'),
  match_field: document.getElementById('r-match-field'),
  link_name: document.getElementById('r-link-name'),
  link_url: document.getElementById('r-link-url'),
  note: document.getElementById('r-note'),
};

let editingId = null;
let cachedRules = [];

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

const matchFieldLabel = {
  question: '只看問題',
  answer: '只看答案',
  either: '兩者任一',
};

async function loadRules() {
  const resp = await fetch('/admin/api/rules');
  if (!resp.ok) {
    tbody.innerHTML = `<tr><td colspan="7">載入失敗：${resp.status}</td></tr>`;
    return;
  }
  const data = await resp.json();
  cachedRules = data.items;
  tbody.innerHTML = '';
  if (!cachedRules.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:#9ca3af; padding:2rem">目前沒有規則。點右上「+ 新增規則」開始</td></tr>';
    return;
  }
  for (const r of cachedRules) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${r.enabled ? '<span style="color:#16a34a; font-weight:600;">✓</span>' : '<span style="color:#9ca3af">×</span>'}</td>
      <td><span class="tag">${escapeHtml(r.keyword)}</span></td>
      <td>${escapeHtml(matchFieldLabel[r.match_field] || r.match_field)}</td>
      <td>${escapeHtml(r.link_name)}</td>
      <td><a href="${escapeHtml(r.link_url)}" target="_blank" rel="noopener" title="${escapeHtml(r.link_url)}">${escapeHtml(truncate(r.link_url, 50))}</a></td>
      <td title="${escapeHtml(r.note || '')}">${escapeHtml(truncate(r.note || '', 30))}</td>
      <td>
        <button class="text-btn" data-action="edit" data-id="${r.id}">編輯</button>
        <button class="text-btn" data-action="delete" data-id="${r.id}" style="color:#dc2626">刪除</button>
      </td>
    `;
    tbody.appendChild(tr);
  }
}

function openModalForCreate() {
  editingId = null;
  modalTitle.textContent = '新增規則';
  fields.enabled.checked = true;
  fields.keyword.value = '';
  fields.match_field.value = 'either';
  fields.link_name.value = '';
  fields.link_url.value = '';
  fields.note.value = '';
  modal.classList.remove('hidden');
  setTimeout(() => fields.keyword.focus(), 50);
}

function openModalForEdit(rule) {
  editingId = rule.id;
  modalTitle.textContent = `編輯規則 #${rule.id}`;
  fields.enabled.checked = !!rule.enabled;
  fields.keyword.value = rule.keyword;
  fields.match_field.value = rule.match_field || 'either';
  fields.link_name.value = rule.link_name;
  fields.link_url.value = rule.link_url;
  fields.note.value = rule.note || '';
  modal.classList.remove('hidden');
  setTimeout(() => fields.keyword.focus(), 50);
}

function closeModal() {
  modal.classList.add('hidden');
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    enabled: fields.enabled.checked,
    keyword: fields.keyword.value.trim(),
    match_field: fields.match_field.value,
    link_name: fields.link_name.value.trim(),
    link_url: fields.link_url.value.trim(),
    note: fields.note.value.trim() || null,
  };
  if (payload.keyword.length < 2) {
    alert('關鍵字至少 2 字'); return;
  }
  try {
    const url = editingId ? `/admin/api/rules/${editingId}` : '/admin/api/rules';
    const method = editingId ? 'PUT' : 'POST';
    const resp = await fetch(url, {
      method,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    closeModal();
    loadRules();
  } catch (err) {
    alert('儲存失敗：' + err.message);
  }
});

addBtn.addEventListener('click', openModalForCreate);
cancelBtn.addEventListener('click', closeModal);
closeBtn.addEventListener('click', closeModal);
modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

tbody.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const id = parseInt(btn.dataset.id, 10);
  if (btn.dataset.action === 'edit') {
    const rule = cachedRules.find(r => r.id === id);
    if (rule) openModalForEdit(rule);
  } else if (btn.dataset.action === 'delete') {
    if (!confirm('確定刪除這條規則？')) return;
    try {
      const resp = await fetch(`/admin/api/rules/${id}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      loadRules();
    } catch (err) {
      alert('刪除失敗：' + err.message);
    }
  }
});

loadRules();
