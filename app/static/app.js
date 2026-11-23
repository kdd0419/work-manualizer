// 관리 화면 동작: 서버 API(/api/...)를 불러 화면을 그린다.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { hasKey: false, aiInit: false, recId: null, macro: null, recordings: [], tab: 'record' };
const MOCK_PREFIX = 'http://localhost:5000';

// ================================================================ 공통 도우미

// 서버 API 호출 (오류면 메시지와 함께 예외)
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: { 'Content-Type': 'application/json' } };
  if (opts.body) { init.method = opts.method || 'POST'; init.body = JSON.stringify(opts.body); }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tag = (text, cls = '') => `<span class="tag ${cls}">${esc(text)}</span>`;
const useAI = () => $('#use-ai').checked;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 표 행이 차례로 나타나는 효과
function animateRows(tbody) {
  $$('tr', tbody).forEach((tr, i) => { tr.classList.add('in'); tr.style.animationDelay = `${Math.min(i, 15) * 28}ms`; });
}

// 오른쪽 아래 알림
function toast(msg, kind = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `<span class="ti">${kind === 'ok' ? '✓' : kind === 'bad' ? '!' : 'i'}</span><span>${esc(msg)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 400); }, 3800);
}

// 버튼 누를 때 물결 효과
document.addEventListener('click', (e) => {
  const b = e.target.closest('button, a.button');
  if (!b || b.disabled) return;
  const r = b.getBoundingClientRect(), size = Math.max(r.width, r.height);
  const s = document.createElement('span');
  s.className = 'ripple';
  Object.assign(s.style, { width: `${size}px`, height: `${size}px`, left: `${e.clientX - r.left - size / 2}px`, top: `${e.clientY - r.top - size / 2}px` });
  b.appendChild(s);
  setTimeout(() => s.remove(), 650);
});

// 숫자가 0부터 올라가는 효과
function countUp(el, to, ms = 900) {
  const start = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - start) / ms), eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(to * eased);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// 성공 축하 폭죽 (외부 라이브러리 없이 canvas로)
function confetti() {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const cv = $('#confetti'), ctx = cv.getContext('2d');
  cv.width = innerWidth; cv.height = innerHeight;
  const colors = ['#4f46e5', '#7c3aed', '#06b6d4', '#22c55e', '#f59e0b', '#ec4899'];
  const parts = Array.from({ length: 140 }, () => ({
    x: innerWidth / 2 + (Math.random() - .5) * 200, y: innerHeight * .35,
    vx: (Math.random() - .5) * 14, vy: -Math.random() * 13 - 4, r: Math.random() * 6 + 4,
    c: colors[Math.floor(Math.random() * colors.length)], rot: Math.random() * 6, vr: (Math.random() - .5) * .3,
  }));
  let frame = 0;
  (function draw() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    parts.forEach((p) => {
      p.vy += .35; p.vx *= .99; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = p.c;
      ctx.globalAlpha = Math.max(0, 1 - frame / 140); ctx.fillRect(-p.r / 2, -p.r / 2, p.r, p.r * .6); ctx.restore();
    });
    if (++frame < 140) requestAnimationFrame(draw); else ctx.clearRect(0, 0, cv.width, cv.height);
  })();
}

// 오래 걸리는 작업 실행: 로그를 실시간으로 보여 주고, 끝나면 작업 정보를 돌려준다
async function runJob(body, prefix, buttons = [], label = '작업') {
  const box = $(`#${prefix}-job`), badge = $(`#${prefix}-badge`), msg = $(`#${prefix}-msg`), log = $(`#${prefix}-log`);
  box.hidden = false; box.classList.remove('finished'); log.textContent = ''; msg.textContent = '';
  badge.className = 'badge running'; badge.textContent = '진행 중';
  const main = buttons[0];
  buttons.forEach((b) => (b.disabled = true));
  if (main) { main.disabled = false; main.classList.add('loading'); }
  try {
    const { id } = await api('/api/jobs', { body });
    while (true) {
      await sleep(700);
      const job = await api(`/api/jobs/${id}`);
      log.textContent = job.log.join('\n');
      log.scrollTop = log.scrollHeight;
      if (job.status !== 'running') {
        badge.className = `badge ${job.status}`;
        badge.textContent = job.status === 'done' ? '완료' : '실패';
        toast(job.status === 'done' ? `${label} 완료` : `${label} 실패 — 로그를 확인하세요`, job.status === 'done' ? 'ok' : 'bad');
        return job;
      }
    }
  } catch (e) {
    badge.className = 'badge failed'; badge.textContent = '오류'; msg.textContent = e.message;
    toast(e.message, 'bad');
    return { status: 'failed', log: [], error: e.message };
  } finally {
    box.classList.add('finished');
    buttons.forEach((b) => { b.disabled = false; b.classList.remove('loading'); });
  }
}

// ================================================================ 사이드바 상태

async function refreshStatus() {
  try {
    const s = await api('/api/status');
    $('#mock-chip').className = `stat-line ${s.mock ? 'on' : 'off'}`;
    $('#mock-text').textContent = s.mock ? '가상 시스템 실행 중' : '가상 시스템 꺼짐';
    $('#mock-start').hidden = s.mock;
    $('#mock-open').hidden = !s.mock;
    const ai = $('#use-ai');
    if (!state.aiInit || state.hasKey !== s.has_key) {
      ai.disabled = !s.has_key;
      ai.checked = s.has_key;
      state.hasKey = s.has_key; state.aiInit = true;
    }
    $('#ai-chip').classList.toggle('disabled', !s.has_key);
    $('#ai-chip').title = s.has_key ? `모델: ${s.model}` : '.env에 LLM_API_KEY가 없어 규칙 기반으로만 동작합니다';
    $('#ai-text').textContent = s.has_key ? `AI 사용 · ${s.model}` : 'AI 키 없음 · 규칙 기반';
  } catch (e) {
    $('#mock-chip').className = 'stat-line off';
    $('#mock-text').textContent = '관리 서버 연결 끊김';
  }
}

$('#mock-start').onclick = async () => {
  $('#mock-text').textContent = '켜는 중…';
  try { await api('/api/mock/start', { body: {} }); toast('가상 시스템을 켰습니다', 'ok'); } catch (e) { toast(e.message, 'bad'); }
  refreshStatus();
};

// ================================================================ 탭

function movePill() {
  const b = $(`#tabs button[data-tab="${state.tab}"]`);
  if (b) $('#nav-pill').style.transform = `translateY(${b.offsetTop}px)`;
}

function showTab(name) {
  state.tab = name;
  $$('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  movePill();
  $$('.tab').forEach((t) => {
    const on = t.id === `tab-${name}`;
    t.hidden = !on;
    if (on) {
      t.classList.remove('enter'); void t.offsetWidth; t.classList.add('enter');   // 애니메이션 다시 재생
      $('#page-step').textContent = t.dataset.step;
      $('#page-title').textContent = t.dataset.title;
      $('#page-lead').textContent = t.dataset.lead;
    }
  });
  $('#rec-toolbar').hidden = !['analyze', 'manual'].includes(name);
  if (name === 'record') loadRecordings();
  if (name === 'analyze' || name === 'manual') loadRecordings().then(loadDetail);
  if (name === 'run') loadMacros();
  if (name === 'eval') loadResults();
  try { localStorage.setItem('tab', name); } catch (e) {}
}
$$('#tabs button').forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
addEventListener('resize', movePill);

// ================================================================ ① 녹화

// 녹화 진행 상태 막대 (해석 → 매크로 → 매뉴얼)
function pipeline(r) {
  const done = [r.interpreted, r.macro, r.manual];
  const n = done.filter(Boolean).length;
  return `<div class="pipeline" title="해석 · 매크로 · 매뉴얼">${done.map((d) => `<span class="pdot ${d ? 'done' : ''}"></span>`).join('')}
    <small>${['녹화됨', '해석 완료', '매크로 준비', '매뉴얼 완료'][n]}</small></div>`;
}

async function loadRecordings() {
  state.recordings = await api('/api/recordings');
  const hideEval = $('#hide-eval').checked;
  const visible = state.recordings.filter((r) => !hideEval || !r.is_eval);
  const tbody = $('#rec-table tbody');
  tbody.innerHTML = state.recordings.map((r) => `
    <tr>
      <td class="rec-name"><b>${esc(r.name)}</b><span>${esc(r.id)}</span></td>
      <td class="num">${r.events}</td>
      <td>${esc(r.started_at.replace('T', ' '))}</td>
      <td>${pipeline(r)}</td>
      <td><button class="sm" data-open="${esc(r.id)}">열기 →</button></td>
    </tr>`).join('');
  animateRows(tbody);
  $('#rec-empty').hidden = state.recordings.length > 0;
  $$('[data-open]', tbody).forEach((b) => (b.onclick = () => {
    state.recId = b.dataset.open;
    if (state.recordings.find((r) => r.id === state.recId)?.is_eval) $('#hide-eval').checked = false;
    showTab('analyze');
  }));

  const sel = $('#rec-select');
  if (!visible.find((r) => r.id === state.recId)) state.recId = visible[0]?.id || null;
  sel.innerHTML = visible.map((r) =>
    `<option value="${esc(r.id)}" ${r.id === state.recId ? 'selected' : ''}>${esc(r.name)} · ${esc(r.started_at.replace('T', ' '))}</option>`
  ).join('') || '<option value="">녹화가 없습니다</option>';
}

$('#rec-refresh').onclick = loadRecordings;
$('#hide-eval').onchange = () => loadRecordings().then(loadDetail);
$('#rec-select').onchange = (e) => { state.recId = e.target.value; loadDetail(); };

// 녹화할 시스템 선택 → 시작 주소 채우기, 가상 시스템이 아니면 경고
function checkUrl() { $('#url-warn').hidden = $('#rec-url').value.trim().startsWith(MOCK_PREFIX); }
$('#rec-preset').onchange = (e) => {
  if (e.target.value === 'custom') { $('#rec-url').value = ''; $('#rec-url').focus(); }
  else $('#rec-url').value = e.target.value;
  checkUrl();
};
$('#rec-url').oninput = () => {
  const v = $('#rec-url').value.trim();
  const match = $$('#rec-preset option').find((o) => o.value === v);
  $('#rec-preset').value = match ? v : 'custom';
  checkUrl();
};

$('#rec-start').onclick = async () => {
  const name = $('#rec-name').value.trim();
  if (!name) { toast('녹화 이름을 입력하세요', 'bad'); $('#rec-name').focus(); return; }
  toast('잠시 후 녹화용 창이 열립니다. 시연 후 그 창을 닫으세요', 'info');
  $('#rec-msg').textContent = '녹화 중… 시연을 마치면 녹화용 브라우저 창을 닫으세요.';
  const job = await runJob({ kind: 'record', name, url: $('#rec-url').value }, 'rec', [$('#rec-start')], '녹화');
  $('#rec-msg').textContent = job.status === 'done' ? '저장되었습니다. 목록에서 "열기 →"를 눌러 해석하세요.' : '녹화에 실패했습니다. 로그를 확인하세요.';
  loadRecordings();
};

// ================================================================ ② 해석 / ③ 매뉴얼

async function loadDetail() {
  const has = !!state.recId;
  ['#btn-interpret', '#btn-macro', '#btn-questions', '#btn-manual'].forEach((s) => ($(s).disabled = !has));
  if (!has) {
    ['#steps-area', '#vars-area', '#qa-area', '#manual-view'].forEach((s) => ($(s).hidden = true));
    $('#ana-hint').textContent = '녹화가 없습니다. 먼저 녹화하세요.';
    return;
  }
  const d = await api(`/api/recordings/${encodeURIComponent(state.recId)}`);
  $('#btn-macro').disabled = !d.steps;
  $('#btn-questions').disabled = !d.steps;
  $('#btn-manual').disabled = !d.steps;
  $('#ana-hint').textContent = d.steps ? '' : `이벤트 ${d.events}개가 녹화되어 있습니다. "① 단계 해석"을 누르세요.`;
  $('#man-hint').textContent = d.steps ? '' : '먼저 해석 탭에서 단계 해석을 하세요.';
  renderSteps(d);
  renderVars(d.macro_data);
  renderQuestions(d.questions);
  loadManual();
}

function renderSteps(d) {
  $('#steps-area').hidden = !d.steps;
  if (!d.steps) return;
  const steps = d.steps.steps;
  $('#steps-title').textContent = `단계 ${steps.length}개`;
  $('#steps-model').textContent = d.steps.model;
  const tbody = $('#steps-table tbody');
  tbody.innerHTML = steps.map((s) => `
    <tr data-step="${s.step}">
      <td>${s.step}</td><td>${tag(s.action, 'blue')}</td><td><b>${esc(s.target)}</b></td>
      <td>${esc(s.value || s.download_filename || '')}</td><td>${esc(s.description)}</td>
      <td>${s.changes_data ? tag('데이터 변경', 'warn') : ''}</td>
    </tr>`).join('');
  animateRows(tbody);
  const img = $('#shot-img');
  const pick = (n) => {
    const s = steps.find((x) => x.step === n);
    $$('tr', tbody).forEach((tr) => tr.classList.toggle('selected', +tr.dataset.step === n));
    $('#shot-caption').textContent = `${s.step}. ${s.description}`;
    const src = s.screenshot ? `/files/recordings/${encodeURIComponent(d.id)}/${s.screenshot}` : '';
    img.classList.add('fade');
    setTimeout(() => { img.src = src; img.onload = () => img.classList.remove('fade'); }, 150);   // 부드럽게 바꾸기
  };
  $$('tr', tbody).forEach((tr) => (tr.onclick = () => pick(+tr.dataset.step)));
  if (steps.length) pick(steps[0].step);
}

function renderVars(m) {
  $('#vars-area').hidden = !m;
  if (!m) return;
  $('#vars-table tbody').innerHTML = m.variables.map((v) => `
    <tr><td><b>${esc(v.name)}</b></td><td>${esc(v.description)}</td><td>${esc(v.recorded_value)}</td>
    <td>${tag(v.default_rule, 'blue')}</td><td>${esc(ruleExample(v))}</td><td>${v.steps.join(', ')}</td></tr>`).join('')
    || '<tr><td colspan="6" class="empty">변수 없음</td></tr>';
  $('#fixed-table tbody').innerHTML = m.fixed.map((f) =>
    `<tr><td>${f.step}</td><td>${esc(f.value)}</td><td>${esc(f.reason)}</td></tr>`).join('')
    || '<tr><td colspan="3" class="empty">고정값 없음</td></tr>';
  animateRows($('#vars-table tbody'));
}

// 기본값 규칙 예시 (화면 표시용, 실제 계산은 실행 시 서버에서)
function ruleExample(v) {
  const fmt = (d) => d.toISOString().slice(0, 10);
  const t = new Date(); t.setMinutes(t.getMinutes() - t.getTimezoneOffset());
  const m = /^today([+-]\d+)?$/.exec(v.default_rule);
  if (m) { t.setDate(t.getDate() + (+m[1] || 0)); return fmt(t); }
  if (v.default_rule === 'month_start') { t.setDate(1); return fmt(t); }
  if (v.default_rule === 'prev_month_end') { t.setDate(0); return fmt(t); }
  if (v.default_rule === 'prev_month_start') { t.setDate(0); t.setDate(1); return fmt(t); }
  if (v.default_rule === 'recorded') return v.recorded_value;
  return '(실행할 때 입력)';
}

$('#btn-interpret').onclick = async () => {
  const job = await runJob({ kind: 'interpret', rid: state.recId, use_ai: useAI() }, 'ana', [$('#btn-interpret'), $('#btn-macro')], '단계 해석');
  if (job.status === 'done') loadDetail();
};
$('#btn-macro').onclick = async () => {
  const job = await runJob({ kind: 'macro', rid: state.recId, use_ai: useAI() }, 'ana', [$('#btn-macro'), $('#btn-interpret')], '매크로 생성');
  if (job.status === 'done') { await loadDetail(); $('#vars-area').scrollIntoView({ behavior: 'smooth' }); }
};

function renderQuestions(qs) {
  $('#qa-area').hidden = !qs.length;
  $('#qa-form').innerHTML = qs.map((q) => `
    <div class="qa">
      <div class="q">${tag(q.kind, q.kind === '예외 상황' ? 'warn' : 'blue')} ${esc(q.question)}
        ${q.why ? `<div class="why">${esc(q.why)}</div>` : ''}</div>
      <textarea name="${esc(q.id)}" placeholder="답변">${esc(q.answer)}</textarea>
    </div>`).join('');
  $('#qa-saved').textContent = '';
}

async function loadManual() {
  const m = await api(`/api/manuals/${encodeURIComponent(state.recId)}`);
  $('#manual-view').hidden = !m.exists;
  $('#manual-download').hidden = !m.exists;
  if (m.exists) {
    $('#manual-view').innerHTML = m.html;
    $('#manual-download').href = `/api/manuals/${encodeURIComponent(state.recId)}/download`;
  }
}

async function saveAnswers(quiet) {
  const answers = {};
  $$('#qa-form textarea').forEach((t) => (answers[t.name] = t.value));
  await api(`/api/recordings/${encodeURIComponent(state.recId)}/answers`, { body: { answers } });
  $('#qa-saved').textContent = '저장했습니다. "② 매뉴얼 생성"을 누르면 답변이 반영됩니다.';
  if (!quiet) toast('답변을 저장했습니다', 'ok');
}

$('#btn-save-answers').onclick = () => saveAnswers().catch((e) => toast(e.message, 'bad'));
$('#btn-questions').onclick = async () => {
  const job = await runJob({ kind: 'questions', rid: state.recId, use_ai: useAI() }, 'man', [$('#btn-questions'), $('#btn-manual')], '질문 생성');
  if (job.status === 'done') loadDetail();
};
$('#btn-manual').onclick = async () => {
  if ($$('#qa-form textarea').length) await saveAnswers(true).catch(() => {});   // 적어 둔 답변은 먼저 저장
  const job = await runJob({ kind: 'manual', rid: state.recId, use_ai: useAI() }, 'man', [$('#btn-manual'), $('#btn-questions')], '매뉴얼 생성');
  if (job.status === 'done') { await loadManual(); $('#manual-view').scrollIntoView({ behavior: 'smooth' }); }
};

// ================================================================ ④ 매크로 실행

async function loadMacros() {
  const list = await api('/api/macros');
  const hide = $('#hide-eval-macro').checked;
  const visible = list.filter((m) => !hide || !m.is_eval);
  const sel = $('#macro-select');
  const prev = sel.value;
  sel.innerHTML = visible.map((m) =>
    `<option value="${esc(m.id)}">${esc(m.name || m.id)} · 단계 ${m.steps} · 변수 ${m.variables}</option>`).join('');
  if (visible.find((m) => m.id === prev)) sel.value = prev;
  $('#macro-empty').hidden = visible.length > 0;
  $('#macro-area').hidden = !visible.length;
  if (visible.length) loadMacro();
}

async function loadMacro() {
  const m = state.macro = await api(`/api/macros/${encodeURIComponent($('#macro-select').value)}`);
  $('#var-form').innerHTML = m.variables.map((v) => `
    <label>${esc(v.name)} <small>${esc(v.description || '')} · ${esc(v.default_rule)}</small>
      <input data-var="${esc(v.name)}" type="${v.secret ? 'password' : 'text'}"
        value="${v.secret ? '' : esc(v.default ?? '')}" placeholder="${v.secret ? '실행할 때마다 입력' : ''}">
    </label>`).join('') || '<p class="hint">변수가 없는 매크로입니다.</p>';
  $$('#var-form input').forEach((i) => (i.oninput = renderMacroSteps));
  const changes = m.steps.some((s) => s.changes_data);
  $('#opt-allow').disabled = !changes;
  $('#change-warn').hidden = !changes || $('#opt-allow').checked;
  $('#run-result').hidden = true;
  $('#run-job').hidden = true;
  renderMacroSteps(true);
}

function varValues() {
  const values = {};
  $$('#var-form input').forEach((i) => (values[i.dataset.var] = i.value));
  return values;
}

function renderMacroSteps(animate) {
  const values = varValues();
  const secret = new Set(state.macro.variables.filter((v) => v.secret).map((v) => v.name));
  const fill = (s) => (s || '').replace(/\{\{(.+?)\}\}/g, (_, n) => (secret.has(n) ? '***' : values[n] ?? ''));
  const tbody = $('#macro-steps tbody');
  tbody.innerHTML = state.macro.steps.map((s) => `
    <tr><td>${s.step}</td><td>${tag(s.action, 'blue')}</td><td>${esc(s.description)}</td>
    <td><b>${['입력', '선택'].includes(s.action) ? esc(fill(s.value)) : ''}</b></td>
    <td>${s.changes_data ? tag('데이터 변경', 'warn') : ''}</td></tr>`).join('');
  if (animate === true) animateRows(tbody);
}

$('#macro-select').onchange = loadMacro;
$('#hide-eval-macro').onchange = loadMacros;
$('#opt-allow').onchange = (e) => ($('#change-warn').hidden = e.target.checked || !state.macro.steps.some((s) => s.changes_data));

$('#btn-run').onclick = async () => {
  const values = varValues();
  const missing = Object.entries(values).filter(([, v]) => !v.trim()).map(([k]) => k);
  if (missing.length) { toast(`값을 입력하세요: ${missing.join(', ')}`, 'bad'); return; }
  $('#run-result').hidden = true;
  $('#run-msg').textContent = $('#opt-headless').checked ? '' : '자동 실행용 브라우저 창이 열립니다. 건드리지 말고 지켜보세요.';
  const job = await runJob({
    kind: 'run', mid: $('#macro-select').value, values,
    checks: $('#opt-checks').checked, allow_changes: $('#opt-allow').checked, headless: $('#opt-headless').checked,
  }, 'run', [$('#btn-run')], '매크로 실행');
  $('#run-msg').textContent = '';
  if (job.result) renderRunResult(job.result);
};

function renderRunResult(r) {
  const runName = r.run_dir.split(/[\\/]/).pop();
  const fail = r.steps.find((s) => s.screenshot);
  const box = $('#run-result');
  box.hidden = false;
  box.innerHTML = `
    ${r.success
      ? `<div class="banner ok">✅ 성공 — 총 ${r.total}단계 중 ${r.succeeded}단계 완료 (${esc(r.version || '')})</div>`
      : `<div class="banner bad">⛔ ${r.stopped_at}단계에서 멈췄습니다<small>${esc(r.stop_message || '')}</small></div>`}
    ${r.downloads.length ? `<p><b>다운로드한 파일:</b> ${r.downloads.map((f) =>
      `<a href="/files/runs/${encodeURIComponent(runName)}/${encodeURIComponent(f)}">${esc(f)}</a>`).join(', ')}</p>` : ''}
    ${r.messages.length ? `<p><b>화면 메시지:</b> ${r.messages.map(esc).join(' / ')}</p>` : ''}
    <div class="table-wrap"><table><thead><tr><th>#</th><th>결과</th><th>동작</th><th>대상</th><th>메시지</th></tr></thead><tbody>
    ${r.steps.map((s) => `<tr><td>${s.step}</td><td>${tag(s.status, s.status === '실패' ? 'bad' : 'ok')}</td>
      <td>${esc(s.action)}</td><td>${esc(s.target)}</td><td>${esc(s.message)}</td></tr>`).join('')}
    </tbody></table></div>
    ${fail ? `<p><b>멈춘 화면</b></p><img class="fail-shot" src="/files/runs/${encodeURIComponent(runName)}/${fail.screenshot}">` : ''}`;
  animateRows($('tbody', box));
  box.scrollIntoView({ behavior: 'smooth' });
  if (r.success) confetti();
}

// ================================================================ ⑤ 평가 결과

async function loadResults() {
  const items = await api('/api/results');
  const latest = {};
  items.forEach((it) => { const v = it.name.split('_').pop(); if (!latest[v]) latest[v] = it; });
  const label = { v1: 'v1 · 그대로 재생', v3: 'v3 · 변수 매크로', v4: 'v4 · + 실패 감지' };
  const vs = ['v1', 'v3', 'v4'].filter((v) => latest[v]);
  $('#eval-summary').innerHTML = vs.map((v) => {
    const it = latest[v], pct = it.total ? Math.round((it.ok / it.total) * 100) : 0;
    return `<div class="card stat"><div class="ver">${label[v]}</div>
      <div class="rate"><span class="cnt" data-to="${it.ok}">0</span>/${it.total}</div>
      <div class="hint">성공률 <span class="cnt" data-to="${pct}">0</span>% · ${esc(it.name.split('_')[0])}</div>
      <div class="bar"><span data-w="${pct}"></span></div></div>`;
  }).join('') || '<div class="card"><p class="empty">평가 결과가 없습니다. 아래 "평가 실행"을 누르세요.</p></div>';
  requestAnimationFrame(() => {
    $$('#eval-summary .cnt').forEach((el) => countUp(el, +el.dataset.to));
    $$('#eval-summary .bar span').forEach((el) => (el.style.width = `${el.dataset.w}%`));
  });
  $('#eval-reports').innerHTML = items.map((it) =>
    `<details class="report"><summary>${esc(it.name)} — ${it.ok ?? '?'}/${it.total ?? '?'}</summary><div class="table-wrap">${it.html}</div></details>`).join('');
}

$('#btn-eval').onclick = async () => {
  if (!confirm('평가를 실행하면 가상 업무 시스템 데이터가 초기화되고, 약 5분 동안 브라우저 자동 실행이 진행됩니다. 계속할까요?')) return;
  const job = await runJob({ kind: 'eval', use_ai: useAI() }, 'eval', [$('#btn-eval')], '평가');
  $('#eval-msg').textContent = job.status === 'done' ? '평가가 끝났습니다.' : '평가 중 오류가 났습니다. 로그를 확인하세요.';
  loadResults();
};

// ================================================================ 시작

refreshStatus();
setInterval(refreshStatus, 5000);
let firstTab = 'record';
try { firstTab = localStorage.getItem('tab') || 'record'; } catch (e) {}
showTab(firstTab);
checkUrl();
