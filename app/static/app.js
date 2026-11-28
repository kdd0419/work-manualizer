// 관리 화면 동작: 서버 API(/api/...)를 불러 화면을 그린다.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { hasKey: false, aiInit: false, recId: null, macro: null, recordings: [], tab: 'record', excel: true, xlFiles: [] };
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

// 오른쪽 아래 알림. action({label, onClick})을 주면 버튼이 붙고 조금 더 오래 보인다
function toast(msg, kind = 'info', action = null) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `<span class="ti">${kind === 'ok' ? '✓' : kind === 'bad' ? '!' : 'i'}</span><span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement('button');
    b.className = 'toast-act';
    b.textContent = action.label;
    b.onclick = () => { action.onClick(); el.remove(); };
    el.appendChild(b);
  }
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 400); }, action ? 7000 : 3800);
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
    const ai = $('#use-ai');
    if (!state.aiInit || state.hasKey !== s.has_key) {
      ai.disabled = !s.has_key;
      ai.checked = s.has_key;
      state.hasKey = s.has_key; state.aiInit = true;
    }
    $('#ai-chip').classList.toggle('disabled', !s.has_key);
    $('#ai-chip').title = s.has_key ? `${s.provider} · 모델: ${s.model || '서버 기본'}${s.external ? ' (외부 서비스 — 내부 정보는 사내 AI로)' : ''}`
      : '.env에 AI 설정이 없어 규칙 기반으로만 동작합니다';
    $('#ai-text').textContent = s.has_key ? `${s.external ? 'AI' : '사내 AI'} · ${s.model || '기본 모델'}` : 'AI 키 없음 · 규칙 기반';
    $('#ai-chip').dataset.tip = $('#ai-text').textContent;
    state.aiExternal = s.external;
    checkUrl(); checkXlWarn();
    state.excel = s.excel;
    $('#xl-noexcel').hidden = s.excel;
  } catch (e) {
    $('#ai-text').textContent = '관리 서버 연결 끊김';
  }
}

// ================================================================ 사이드바: 업무 사이트 바로가기
// 녹화 탭 ☆ 즐겨찾기와 같은 목록. 이 PC에서 접속되는지(사내망·VPN) 점으로 보여 주고, 열기·바로 녹화
state.siteState = {};
const isMockUrl = (u) => u.startsWith(MOCK_PREFIX);

function renderSites() {
  const real = state.favorites.map((f, i) => ({ ...f, i })).filter((f) => !isMockUrl(f.url));
  const mock = state.favorites.map((f, i) => ({ ...f, i })).filter((f) => isMockUrl(f.url));
  const row = (f) => {
    const st = state.siteState[f.url] || { state: 'checking', message: '접속 확인 중…' };
    return `<li class="site ${st.state}" title="${esc(f.url)}\n${esc(st.message)}">
      <span class="dot" aria-label="${esc(st.message)}"></span>
      <a href="${esc(f.url)}" target="_blank" rel="noopener" data-open="${f.i}">${esc(f.name)}</a>
      <button type="button" data-rec="${f.i}" title="이 사이트 녹화하기" aria-label="${esc(f.name)} 녹화">🎬</button>
      <button type="button" data-del="${f.i}" title="목록에서 삭제" aria-label="${esc(f.name)} 삭제">✕</button></li>`;
  };
  $('#sites-list').innerHTML = (real.map(row).join('')
      || '<li class="sites-empty">＋를 눌러 자주 쓰는 업무 사이트(사내 주소 포함)를 등록하세요</li>')
    + (mock.length ? `<li class="sites-group">🧪 시험용 가상 사이트</li>${mock.map(row).join('')}` : '');
  $$('#sites-list [data-open]').forEach((a) => (a.onclick = async (e) => {
    const f = state.favorites[+a.dataset.open];
    if (!isMockUrl(f.url) || state.siteState[f.url]?.state === 'ok') return;   // 실제 사이트는 그냥 새 탭
    e.preventDefault();   // 가상 사이트가 꺼져 있으면 켠 뒤 열기
    const w = window.open('about:blank', '_blank');
    try { w.document.write('<p style="font:16px sans-serif;padding:40px;color:#555">시험용 가상 시스템을 켜는 중입니다… (몇 초)</p>'); } catch (err) {}
    try { await api('/api/mock/start', { body: {} }); if (w) w.location = f.url; checkSites(); }
    catch (err) { w?.close(); toast(err.message, 'bad'); }
  }));
  $$('#sites-list [data-rec]').forEach((b) => (b.onclick = () => {
    const f = state.favorites[+b.dataset.rec];
    showTab('record'); setRecMode('site');
    $('#rec-url').value = f.url; onUrlChange();
    $('#rec-name').focus();
    toast(`녹화할 사이트: ${f.name} — 녹화 이름을 넣고 시작하세요`, 'info');
  }));
  $$('#sites-list [data-del]').forEach((b) => (b.onclick = () => deleteFavorite(state.favorites[+b.dataset.del])));
}

async function checkSites() {
  $('#sites-check').classList.add('spin');
  try {
    (await api('/api/favorites/check', { body: {} })).forEach((r) => (state.siteState[r.url] = r));
  } catch (e) { /* 확인 실패는 조용히 */ }
  $('#sites-check').classList.remove('spin');
  renderSites();
}

$('#sites-check').onclick = checkSites;
$('#sites-title').onclick = () => { if ($('.app').classList.contains('collapsed')) setSideCollapsed(false); };
$('#sites-add').onclick = () => { $('#sites-form').hidden = !$('#sites-form').hidden; if (!$('#sites-form').hidden) $('#site-name').focus(); };
$('#site-cancel').onclick = () => ($('#sites-form').hidden = true);
$('#sites-form').onsubmit = async (e) => {
  e.preventDefault();
  let url = $('#site-url').value.trim();
  if (url && !/^https?:\/\//i.test(url)) url = `http://${url}`;   // intra.회사.com 처럼 입력해도 되게
  try {
    state.favorites = await api('/api/favorites', { body: { url, name: $('#site-name').value.trim() } });
    $('#sites-form').hidden = true; $('#site-name').value = ''; $('#site-url').value = '';
    toast('업무 사이트를 등록했습니다 (녹화 탭 즐겨찾기에도 나옵니다)', 'ok');
    renderFavorites(); checkSites();
  } catch (err) { toast(err.message, 'bad'); }
};

// ================================================================ 탭

function movePill() {
  const b = $(`#tabs button[data-tab="${state.tab}"]`);
  if (b) $('#nav-pill').style.transform = `translateY(${b.offsetTop}px)`;
}

function showTab(name) {
  state.tab = name;
  document.body.dataset.tab = name;
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
  if (name === 'guide') loadGuide();
  if (name === 'record') loadRecordings();
  if (name === 'analyze' || name === 'manual') loadRecordings().then(loadDetail);
  if (name === 'run') loadMacros();
  try { localStorage.setItem('tab', name); } catch (e) {}
}
$$('#tabs button').forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
addEventListener('resize', movePill);

// ---------------- 사이드바 접기/펴기 (« 버튼, Ctrl+B). 상태는 기억해 둔다
function setSideCollapsed(on) {
  $('.app').classList.toggle('collapsed', on);
  const t = $('#side-toggle');
  t.textContent = on ? '»' : '«';
  t.title = on ? '메뉴 펼치기 (Ctrl+B)' : '메뉴 접기 (Ctrl+B)';
  t.setAttribute('aria-label', on ? '메뉴 펼치기' : '메뉴 접기');
  t.setAttribute('aria-expanded', String(!on));
  try { localStorage.setItem('sideCollapsed', on ? '1' : '0'); } catch (e) {}
  setTimeout(movePill, 340);   // 폭이 바뀐 뒤 선택 표시 위치 다시 맞춤
}
$('#side-toggle').onclick = () => setSideCollapsed(!$('.app').classList.contains('collapsed'));
addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b' && !e.target.closest('input, textarea, [contenteditable]')) {
    e.preventDefault();
    setSideCollapsed(!$('.app').classList.contains('collapsed'));
  }
});
try { if (localStorage.getItem('sideCollapsed') === '1') setSideCollapsed(true); } catch (e) {}

// ================================================================ 시작 화면: 사용 안내

const SECTION_ICON = { '필요한 환경': '🧰', '설치': '⬇️', '실행': '▶️', '시연': '🎯', 'AI': '🤖', '결과물': '📁', '문제': '🛟' };
let guideLoaded = false;

async function loadGuide() {
  try { $('#guide-hide').checked = localStorage.getItem('skipGuide') === '1'; } catch (e) {}
  if (guideLoaded) return;
  try {
    const g = await api('/api/guide');
    $('#guide-intro').innerHTML = g.intro;
    $('#guide-sections').innerHTML = g.sections.map((s, i) => {
      const icon = Object.entries(SECTION_ICON).find(([k]) => s.title.includes(k))?.[1] || '📌';
      return `<details class="guide-sec"><summary><span>${icon}</span>${esc(s.title)}</summary>
        <div class="guide-body table-wrap">${s.html}</div></details>`;
    }).join('');
    guideLoaded = true;
  } catch (e) { $('#guide-sections').innerHTML = `<p class="warn">사용 안내를 불러오지 못했습니다: ${esc(e.message)}</p>`; }
}

function leaveGuide(to = 'record') {
  try { localStorage.setItem('skipGuide', $('#guide-hide').checked ? '1' : '0'); } catch (e) {}
  showTab(to);
}
$('#guide-skip').onclick = () => leaveGuide('record');
$('#guide-start').onclick = () => leaveGuide('record');
$('#guide-read').onclick = () => $('#guide-how').scrollIntoView({ behavior: 'smooth', block: 'start' });

// 시연 영상: 카드를 누르면 큰 화면으로 재생
const VIDEO_SRC = { site: '/static/videos/site_demo.webm', excel: '/static/videos/excel_demo.webm' };
function openVideo(kind, title) {
  const v = $('#vplayer');
  $('#vtitle').textContent = title;
  v.src = VIDEO_SRC[kind];
  $('#vmodal').hidden = false;
  document.body.style.overflow = 'hidden';
  v.play().catch(() => {});
  $('#vclose').focus();
}
function closeVideo() {
  const v = $('#vplayer');
  v.pause(); v.removeAttribute('src'); v.load();
  $('#vmodal').hidden = true;
  document.body.style.overflow = '';
}
$$('.vcard[data-video]').forEach((c) => (c.onclick = () => openVideo(c.dataset.video, c.dataset.title)));
$('#vclose').onclick = closeVideo;
$('#vmodal').onclick = (e) => { if (e.target.id === 'vmodal') closeVideo(); };
addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#vmodal').hidden) closeVideo(); });
// 영상 길이 표시 (메타데이터만 읽음)
Object.entries(VIDEO_SRC).forEach(([k, src]) => {
  const probe = document.createElement('video');
  probe.preload = 'metadata';
  probe.onloadedmetadata = () => {
    const d = probe.duration;
    if (Number.isFinite(d)) $(`[data-len="${k}"]`).textContent = `${Math.floor(d / 60)}:${String(Math.round(d % 60)).padStart(2, '0')}`;
  };
  probe.src = src;
});
$('#guide-hide').onchange = (e) => { try { localStorage.setItem('skipGuide', e.target.checked ? '1' : '0'); } catch (err) {} };
$$('#tab-guide [data-go]').forEach((b) => {
  b.onclick = () => leaveGuide(b.dataset.go);
  if (b.tagName !== 'BUTTON') {   // 카드도 키보드로 누를 수 있게
    b.tabIndex = 0; b.setAttribute('role', 'button');
    b.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); leaveGuide(b.dataset.go); } };
  }
});

// 시작 화면 꾸밈: 아래로 내리면 섹션이 차례로 나타남 + 마우스를 따라오는 은은한 조명
(() => {
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!calm && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((en) => {
      if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
    }), { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    $$('#tab-guide .g-sec').forEach((s) => { s.classList.add('reveal'); io.observe(s); });
  }
  if (calm) return;
  let raf = 0;
  const follow = (el) => el.addEventListener('pointermove', (e) => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const r = el.getBoundingClientRect();
      el.style.setProperty('--mx', `${e.clientX - r.left}px`);
      el.style.setProperty('--my', `${e.clientY - r.top}px`);
    });
  });
  follow($('#hero'));

  // 3D: 첫 화면 그림은 마우스 방향으로 기울고, 벗어나면 원래 비스듬한 자세로
  const hero = $('#hero'), stage = $('#hero-stage');
  hero.addEventListener('pointermove', (e) => {
    const r = hero.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    stage.classList.add('live');
    stage.style.setProperty('--ry', `${(-16 + x * 22).toFixed(1)}deg`);
    stage.style.setProperty('--rx', `${(8 - y * 14).toFixed(1)}deg`);
  });
  hero.addEventListener('pointerleave', () => {
    stage.classList.remove('live');
    stage.style.removeProperty('--ry'); stage.style.removeProperty('--rx');
  });

  // 3D: 카드는 마우스 쪽으로 최대 8도 기울고, 빛이 마우스 위치에 비침
  $$('#tab-guide .steps4 li, #tab-guide .ai4-card, #tab-guide .vcard').forEach((c) => {
    c.classList.add('tilt');
    const g = document.createElement('span'); g.className = 'glare'; c.appendChild(g);
    c.addEventListener('pointermove', (e) => {
      const r = c.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      c.style.setProperty('--ry', `${((x - 0.5) * 16).toFixed(1)}deg`);
      c.style.setProperty('--rx', `${((0.5 - y) * 12).toFixed(1)}deg`);
      c.style.setProperty('--mx', `${(x * 100).toFixed(0)}%`);
      c.style.setProperty('--my', `${(y * 100).toFixed(0)}%`);
    });
    c.addEventListener('pointerleave', () => ['--rx', '--ry'].forEach((k) => c.style.removeProperty(k)));
  });
})();

// ================================================================ ① 녹화

// 녹화 진행 상태 막대 (해석 → 매크로 → 매뉴얼)
function pipeline(r) {
  const done = [r.interpreted, r.macro, r.manual];
  const n = done.filter(Boolean).length;
  return `<div class="pipeline" title="해석 · 매크로 · 매뉴얼">${done.map((d) => `<span class="pdot ${d ? 'done' : ''}"></span>`).join('')}
    <small>${['녹화됨', '해석 완료', '매크로 준비', '매뉴얼 완료'][n]}</small></div>`;
}

async function loadRecordings() {
  // 평가 스크립트가 만든 eval_* 녹화는 목록에서 뺀다 (명령으로 평가를 돌렸을 때만 생김)
  state.recordings = (await api('/api/recordings')).filter((r) => !r.is_eval);
  const visible = state.recordings;
  const tbody = $('#rec-table tbody');
  tbody.innerHTML = state.recordings.map((r) => `
    <tr>
      <td class="rec-name"><b><span class="kind" title="${r.kind === 'excel' ? '엑셀 녹화' : '웹 녹화'}">${r.kind === 'excel' ? '📊' : '🌐'}</span>${esc(r.name)}</b><span>${esc(r.kind === 'excel' ? r.source : r.id)}</span></td>
      <td class="num">${r.events}</td>
      <td>${esc(r.started_at.replace('T', ' '))}</td>
      <td>${pipeline(r)}</td>
      <td><button class="sm" data-open="${esc(r.id)}">열기 →</button></td>
    </tr>`).join('');
  animateRows(tbody);
  $('#rec-empty').hidden = state.recordings.length > 0;
  $$('[data-open]', tbody).forEach((b) => (b.onclick = () => {
    state.recId = b.dataset.open;
    showTab('analyze');
  }));

  const sel = $('#rec-select');
  if (!visible.find((r) => r.id === state.recId)) state.recId = visible[0]?.id || null;
  sel.innerHTML = visible.map((r) =>
    `<option value="${esc(r.id)}" ${r.id === state.recId ? 'selected' : ''}>${r.kind === 'excel' ? '📊 ' : ''}${esc(r.name)} · ${esc(r.started_at.replace('T', ' '))}</option>`
  ).join('') || '<option value="">녹화가 없습니다</option>';
}

$('#rec-refresh').onclick = loadRecordings;
$('#rec-select').onchange = (e) => { state.recId = e.target.value; loadDetail(); };

// ---------------- 녹화 대상: [🌐 사이트] / [📊 Excel] 전환
state.recMode = 'site';
state.favorites = [];
const isExcelRec = () => state.recMode === 'excel';
// 경고는 '실제 업무 대상 + AI 켬 + 외부 AI(Gemini)'일 때만 (사내 AI로 바꿨거나 AI를 끄면 안 보임)
const externalAIOn = () => state.aiExternal !== false && useAI();
function checkUrl() {
  $('#url-warn').hidden = isExcelRec() || $('#rec-url').value.trim().startsWith(MOCK_PREFIX) || !externalAIOn();
}

function setRecMode(mode) {
  state.recMode = mode;
  const xl = mode === 'excel';
  $('#rec-mode').dataset.mode = mode;
  $$('#rec-mode button').forEach((b) => { const on = b.dataset.mode === mode; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
  $('#site-panel').hidden = xl;
  $('#xl-row').hidden = !xl;
  $('#xl-info').hidden = !xl;
  $('#rec-start-hint').textContent = xl ? '원본 파일의 복사본이 Excel로 열립니다' : '녹화용 Chromium 창이 따로 열립니다';
  if (xl) loadExcelFiles().then(() => { fillFileSelect($('#xl-file')); checkXlWarn(); });
  else loadFavorites();
  checkXlWarn();
  checkUrl();
  try { localStorage.setItem('recMode', mode); } catch (e) {}
}
$$('#rec-mode button').forEach((b) => (b.onclick = () => setRecMode(b.dataset.mode)));

// ---------------- 사이트 즐겨찾기
async function loadFavorites() {
  try { state.favorites = await api('/api/favorites'); } catch (e) { state.favorites = []; }
  renderFavorites();
}

function renderFavorites() {
  const cur = $('#rec-url').value.trim();
  $('#fav-list').innerHTML = state.favorites.map((f, i) => `
    <button type="button" class="fav ${f.url === cur ? 'on' : ''}" data-i="${i}" title="${esc(f.url)}">
      ${esc(f.name)}<small>${esc(f.url)}</small>
      <span class="x" role="button" data-del="${i}" title="즐겨찾기에서 삭제" aria-label="${esc(f.name)} 삭제">✕</span>
    </button>`).join('') || '<p class="fav-empty">즐겨찾기가 없습니다. 아래에 주소를 입력하고 ☆ 즐겨찾기를 누르세요.</p>';
  $$('#fav-list .fav').forEach((b) => (b.onclick = (e) => {
    if (e.target.closest('[data-del]')) return;
    $('#rec-url').value = state.favorites[+b.dataset.i].url;
    onUrlChange();
  }));
  $$('#fav-list [data-del]').forEach((x) => (x.onclick = (e) => { e.stopPropagation(); deleteFavorite(state.favorites[+x.dataset.del]); }));
  const isFav = state.favorites.some((f) => f.url === cur);
  const star = $('#fav-toggle');
  star.classList.toggle('on', isFav);
  star.textContent = isFav ? '★ 즐겨찾기됨' : '☆ 즐겨찾기';
  star.title = isFav ? '누르면 즐겨찾기에서 삭제' : '이 주소를 즐겨찾기에 추가';
  renderSites();
}

async function deleteFavorite(f) {
  try {
    state.favorites = await api('/api/favorites/delete', { body: { url: f.url } });
    toast(`즐겨찾기에서 삭제했습니다: ${f.name}`, 'info');
  } catch (e) { toast(e.message, 'bad'); }
  renderFavorites();
}

function onUrlChange() { $('#fav-add').hidden = true; renderFavorites(); checkUrl(); }
$('#rec-url').oninput = onUrlChange;

// ☆ 누르면 이름 입력칸이 나오고, ★(이미 즐겨찾기)이면 삭제
$('#fav-toggle').onclick = () => {
  const url = $('#rec-url').value.trim();
  const fav = state.favorites.find((f) => f.url === url);
  if (fav) { deleteFavorite(fav); return; }
  if (!/^https?:\/\/\S+$/.test(url)) { toast('http:// 또는 https:// 로 시작하는 주소를 입력하세요', 'bad'); $('#rec-url').focus(); return; }
  let guess = url;
  try { const u = new URL(url); guess = u.hostname + (u.pathname !== '/' ? u.pathname : ''); } catch (e) {}
  $('#fav-name').value = guess;
  $('#fav-add').hidden = false;
  $('#fav-name').select();
};
async function saveFavorite() {
  const url = $('#rec-url').value.trim(), name = $('#fav-name').value.trim();
  try {
    state.favorites = await api('/api/favorites', { body: { url, name } });
    $('#fav-add').hidden = true;
    toast(`즐겨찾기에 추가했습니다: ${name || url}`, 'ok');
  } catch (e) { toast(e.message, 'bad'); }
  renderFavorites();
}
$('#fav-save').onclick = saveFavorite;
$('#fav-name').onkeydown = (e) => { if (e.key === 'Enter') saveFavorite(); if (e.key === 'Escape') $('#fav-add').hidden = true; };
$('#fav-cancel').onclick = () => ($('#fav-add').hidden = true);

// ---------------- 엑셀 파일 목록·올리기 (녹화와 매크로 실행에서 함께 사용)
async function loadExcelFiles() {
  try { state.xlFiles = await api('/api/excel/files'); } catch (e) { state.xlFiles = []; }
}

// 파일 선택 상자 채우기 (샘플 / 올린 파일 묶음). extra: 목록에 없는 현재 값도 넣기
function fillFileSelect(sel, selected = sel.value, extra = '', extraLabel = '(녹화 때 파일)') {
  const groups = {};
  state.xlFiles.forEach((f) => (groups[f.group] ||= []).push(f));
  let html = Object.entries(groups).map(([g, fs]) =>
    `<optgroup label="${esc(g)}">${fs.map((f) => `<option value="${esc(f.path)}">${esc(f.name)}</option>`).join('')}</optgroup>`).join('');
  if (extra && !state.xlFiles.some((f) => f.path === extra)) html = `<option value="${esc(extra)}">${esc(extra.split('/').pop())} ${esc(extraLabel)}</option>` + html;
  sel.innerHTML = html || '<option value="">엑셀 파일이 없습니다</option>';
  if ([...sel.options].some((o) => o.value === selected)) sel.value = selected;
}

// 올린 파일(uploads/)을 고르면 실제 데이터 주의 문구
function checkXlWarn() { $('#xl-warn').hidden = !isExcelRec() || !$('#xl-file').value.startsWith('uploads/') || !externalAIOn(); }
$('#use-ai').addEventListener('change', () => { checkUrl(); checkXlWarn(); });
$('#xl-file').onchange = checkXlWarn;

async function uploadExcel(input) {
  const file = input.files[0];
  input.value = '';
  if (!file) return null;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch('/api/excel/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    await loadExcelFiles();
    toast(`올렸습니다: ${data.name}`, 'ok');
    return data.path;
  } catch (e) { toast(e.message, 'bad'); return null; }
}
$('#xl-upload').onchange = async (e) => {
  const path = await uploadExcel(e.target);
  if (path) { fillFileSelect($('#xl-file'), path); checkXlWarn(); }
};

$('#rec-start').onclick = async () => {
  const name = $('#rec-name').value.trim();
  if (!name) { toast('녹화 이름을 입력하세요', 'bad'); $('#rec-name').focus(); return; }
  let body = { kind: 'record', name, url: $('#rec-url').value };
  if (isExcelRec()) {
    if (!state.excel) { toast('이 PC에 Microsoft Excel이 없습니다', 'bad'); return; }
    if (!$('#xl-file').value) { toast('엑셀 파일을 고르세요', 'bad'); return; }
    body = { kind: 'record', name, source: 'excel', file: $('#xl-file').value };
    toast('잠시 후 Excel이 열립니다. 작업을 마치면 Excel 창을 닫으세요', 'info');
    $('#rec-msg').textContent = '녹화 중… 작업을 마치면 Excel 창(통합 문서)을 닫으세요.';
  } else {
    toast('잠시 후 녹화용 창이 열립니다. 시연 후 그 창을 닫으세요', 'info');
    $('#rec-msg').textContent = '녹화 중… 시연을 마치면 녹화용 브라우저 창을 닫으세요.';
  }
  const job = await runJob(body, 'rec', [$('#rec-start')], '녹화');
  $('#rec-msg').textContent = job.status === 'done' ? '저장되었습니다. 목록에서 "열기 →"를 눌러 해석하세요.' : '녹화에 실패했습니다. 로그를 확인하세요.';
  loadRecordings();
};

// ================================================================ ② 해석 / ③ 매뉴얼

async function loadDetail() {
  const has = !!state.recId;
  ['#btn-interpret', '#btn-macro', '#btn-questions', '#btn-manual'].forEach((s) => ($(s).disabled = !has));
  if (!has) {
    ['#steps-area', '#ov-area', '#vars-area', '#qa-area', '#manual-area'].forEach((s) => ($(s).hidden = true));
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
  renderOverview(d);
  state.detail = d;
  renderAiCards(d);
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

// 업무·화면 개요 카드 (화면 카드를 누르면 그 화면의 첫 단계로)
function renderOverview(d) {
  const ov = d.overview;
  $('#ov-area').hidden = !ov;
  if (!ov) return;
  const ai = !String(ov.model).startsWith('규칙');
  $('#ov-model').innerHTML = `${ai ? '🤖 AI' : '⚙ 규칙 기반'} · ${esc(ov.model)}`;
  $('#ov-purpose').textContent = ov.task.purpose || '목적은 AI를 켜고 ① 단계 해석을 다시 하면 정리됩니다.';
  const chips = (label, items) => items?.length ? `<div><b>${label}</b>${items.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>` : '';
  $('#ov-meta').innerHTML = chips('매번 정하는 값', ov.task.inputs) + chips('결과물', ov.task.outputs)
    + (ov.task.when ? `<div><b>하는 때</b><span class="chip">${esc(ov.task.when)}</span></div>` : '');
  $('#ov-screens').innerHTML = ov.screens.map((s, i) => `
    <button class="ov-screen" data-step="${s.steps[0]}">
      ${s.screenshot ? `<img src="/files/recordings/${encodeURIComponent(d.id)}/${esc(s.screenshot)}" alt="">` : '<div class="ov-noimg">📄</div>'}
      <span class="ov-no">${i + 1}</span>
      <b>${esc(s.name)}</b>
      <small>${s.steps.length > 1 ? `${s.steps[0]}~${s.steps[s.steps.length - 1]}단계` : `${s.steps[0]}단계`}</small>
      <span class="ov-desc">${esc(s.description)}</span>
    </button>`).join('');
  $$('#ov-screens .ov-screen').forEach((b) => (b.onclick = () => {
    const tr = $(`#steps-table tbody tr[data-step="${b.dataset.step}"]`);
    if (tr) { tr.click(); tr.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  }));
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

// ---------------- 매뉴얼 보기 + 부분 수정
async function loadManual() {
  renderManual(await api(`/api/manuals/${encodeURIComponent(state.recId)}`));
}

function renderManual(m) {
  state.manual = m;
  $('#manual-area').hidden = !m.exists;
  $('#manual-pdf').hidden = !m.exists;
  if (!m.exists) return;
  $('#manual-pdf').href = `/api/manuals/${encodeURIComponent(state.recId)}/pdf`;
  $('#manual-edited').hidden = !m.edited;
  $('#manual-undo').hidden = !m.history;
  $('#manual-undo').title = `보관된 이전 버전 ${m.history}개`;
  $('#manual-view').innerHTML = m.blocks.map((b, i) => `
    <section class="mblock" data-i="${i}">
      <div class="mtools">
        <button class="ghost sm" data-edit="${i}" title="이 부분 고치기">✏️ 수정</button>
        ${i > 0 ? `<button class="ghost sm danger" data-del="${i}" title="이 부분 지우기 (되돌리기 가능)">🗑 삭제</button>` : ''}
      </div>
      <div class="mbody">${b.html}</div>
    </section>`).join('');
  $$('#manual-view [data-edit]').forEach((b) => (b.onclick = () => editBlock(+b.dataset.edit)));
  $$('#manual-view [data-del]').forEach((b) => (b.onclick = () => deleteBlock(+b.dataset.del)));
}

// 조각 하나 지우기: 바로 지우고, 알림의 '되돌리기'나 ↶ 이전 버전으로 복구
async function deleteBlock(i) {
  const blocks = state.manual.blocks.map((b) => b.md);
  const first = blocks[i].trim().split('\n')[0].replace(/[#*`]/g, '').trim().slice(0, 30);
  blocks.splice(i, 1);
  const sec = $(`#manual-view .mblock[data-i="${i}"]`);
  if (sec) { sec.classList.add('removing'); await sleep(250); }
  const saved = await saveManual(blocks.join(''), null, `삭제했습니다: ${first}`, true);
  if (!saved) renderManual(state.manual);   // 검토 창에서 '돌아가서 고치기'를 고르면 원래대로
}

// ---- 매뉴얼 조각의 이미지: 글과 따로 보여 주고, 끌어다 놓기·선택·붙여넣기로 바꾼다
const IMG_LINE = /^(\s*)!\[([^\]]*)\]\(([^)]+)\)\s*$/;
const imgUrl = (src) => src.replace(/^\.\.\/recordings\//, '/files/recordings/');

async function uploadManualImage(file) {
  if (!file || !/^image\//.test(file.type)) { toast('이미지 파일(PNG, JPG 등)만 넣을 수 있습니다', 'bad'); return null; }
  const fd = new FormData();
  fd.append('file', file, file.name || 'capture.png');
  try {
    const res = await fetch(`/api/manuals/${encodeURIComponent(state.recId)}/images`, { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data.path;
  } catch (e) { toast(e.message, 'bad'); return null; }
}

// 조각 하나를 편집 상자로 바꾼다 (글은 입력칸, 이미지는 아래 썸네일)
function editBlock(i) {
  const sec = $(`#manual-view .mblock[data-i="${i}"]`);
  if (!sec || sec.classList.contains('editing')) return;
  const md = state.manual.blocks[i].md.replace(/\s+$/, '');
  const images = [];
  const text = md.split('\n').filter((line) => {
    const m = IMG_LINE.exec(line);
    if (m) images.push({ indent: m[1], alt: m[2], src: m[3] });
    return !m;
  }).join('\n').replace(/\n{3,}/g, '\n\n').replace(/\s+$/, '');
  const isStep = /^\d+\. /.test(md);
  const indent = images[0]?.indent ?? (isStep ? '    ' : '');
  const altBase = isStep ? `${md.match(/^(\d+)\./)[1]}단계` : '이미지';

  sec.classList.add('editing');
  sec.innerHTML = `
    <textarea aria-label="매뉴얼 ${i + 1}번째 부분 글">${esc(text)}</textarea>
    <div class="mimgs" aria-label="이미지"></div>
    <div class="medit-actions">
      <button class="primary sm" data-save>저장</button>
      <button class="ghost sm" data-cancel>취소</button>
      <span class="hint">이미지는 아래 칸에 <b>끌어다 놓기 · 눌러서 선택 · Ctrl+V 붙여넣기</b> · 저장 Ctrl+Enter · 취소 Esc</span>
    </div>`;
  const ta = $('textarea', sec);
  const gal = $('.mimgs', sec);
  const fit = () => { ta.style.height = 'auto'; ta.style.height = `${Math.max(90, ta.scrollHeight + 4)}px`; };

  // 드롭·선택·붙여넣기 공통: 파일 → 업로드 → k번째 이미지 바꾸기(k가 없으면 추가)
  const put = async (file, k = null) => {
    const card = k === null ? $('.mimg.add', gal) : $(`.mimg[data-k="${k}"]`, gal);
    card?.classList.add('uploading');
    const path = await uploadManualImage(file);
    card?.classList.remove('uploading');
    if (!path) return;
    if (k === null) images.push({ indent, alt: `${altBase} 이미지`, src: path });
    else images[k].src = path;
    drawGallery();
    toast(k === null ? '이미지를 추가했습니다 (저장을 눌러야 반영)' : '이미지를 바꿨습니다 (저장을 눌러야 반영)', 'ok');
  };
  const dropZone = (el, k) => {
    el.ondragover = (e) => { e.preventDefault(); el.classList.add('over'); };
    el.ondragleave = () => el.classList.remove('over');
    el.ondrop = (e) => { e.preventDefault(); el.classList.remove('over'); const f = e.dataTransfer.files[0]; if (f) put(f, k); };
    const inp = $('input[type=file]', el);
    el.onclick = (e) => { if (!e.target.closest('.mimg-x')) inp.click(); };
    inp.onchange = () => { if (inp.files[0]) put(inp.files[0], k); inp.value = ''; };
  };
  function drawGallery() {
    gal.innerHTML = images.map((im, k) => `
      <figure class="mimg" data-k="${k}" title="끌어다 놓거나 눌러서 이미지 바꾸기">
        <img src="${esc(imgUrl(im.src))}" alt="${esc(im.alt)}">
        <figcaption>🔄 끌어다 놓기 · 눌러서 바꾸기</figcaption>
        <button class="mimg-x" type="button" title="이 이미지 빼기" aria-label="이미지 빼기">✕</button>
        <input type="file" accept="image/*" hidden>
      </figure>`).join('') + `
      <div class="mimg add" title="이미지 추가">
        <span>＋</span><b>이미지 추가</b><small>끌어다 놓기 · 클릭 · Ctrl+V</small>
        <input type="file" accept="image/*" hidden>
      </div>`;
    $$('.mimg[data-k]', gal).forEach((el) => {
      const k = +el.dataset.k;
      dropZone(el, k);
      $('.mimg-x', el).onclick = (e) => { e.stopPropagation(); images.splice(k, 1); drawGallery(); };
    });
    dropZone($('.mimg.add', gal), null);
  }
  drawGallery();

  // 입력칸에 이미지를 끌어다 놓거나 붙여넣으면 이미지 추가
  ta.ondragover = (e) => { if ([...e.dataTransfer.items].some((it) => it.kind === 'file')) e.preventDefault(); };
  ta.ondrop = (e) => { const f = e.dataTransfer.files[0]; if (f) { e.preventDefault(); put(f); } };
  sec.onpaste = (e) => {
    const item = [...(e.clipboardData?.items || [])].find((it) => it.type.startsWith('image/'));
    if (item) { e.preventDefault(); put(item.getAsFile()); }
  };
  ta.oninput = fit; fit(); ta.focus();

  $('[data-cancel]', sec).onclick = () => renderManual(state.manual);
  $('[data-save]', sec).onclick = async () => {
    const blocks = state.manual.blocks.map((b) => b.md);
    const imgMd = images.map((im) => `\n\n${im.indent}![${im.alt}](${im.src})`).join('');
    blocks[i] = ta.value.replace(/\s+$/, '') + imgMd + '\n\n';
    await saveManual(blocks.join(''), i);
  };
  sec.onkeydown = (e) => { if (e.key === 'Escape') renderManual(state.manual); if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) $('[data-save]', sec).click(); };
}

// 저장 전에 검토 (코드 검사 + AI 검사). 문제가 있으면 창으로 보여 주고 고르게 한다. 저장했으면 true
async function saveManual(markdown, flashIndex = null, message = '매뉴얼을 저장했습니다 (이전 내용은 보관됨)', undoable = false) {
  if (!(await reviewBeforeSave(markdown))) return false;
  try {
    const m = await api(`/api/manuals/${encodeURIComponent(state.recId)}`, { method: 'PUT', body: { markdown } });
    renderManual(m);
    toast(message, 'ok', undoable ? { label: '되돌리기', onClick: undoManual } : null);
    if (flashIndex !== null) $(`#manual-view .mblock[data-i="${flashIndex}"]`)?.classList.add('saved');
    return true;
  } catch (e) { toast(e.message, 'bad'); return false; }
}

// 1) 코드 검사는 바로 받는다  2) AI 검사는 기다리는 창을 띄우고, '건너뛰기'를 누르면 코드 검사 결과로 진행
async function reviewBeforeSave(markdown) {
  const url = `/api/manuals/${encodeURIComponent(state.recId)}/review`;
  let r;
  try { r = await api(url, { body: { markdown, use_ai: false } }); }
  catch (e) { return true; }   // 검토가 실패해도 저장은 막지 않음
  if (useAI() && state.hasKey) {
    const ctrl = new AbortController();
    $('#review-main').hidden = true;
    $('#review-wait').hidden = false;
    $('#review-modal').hidden = false;
    $('#review-skip').focus();
    const skipped = new Promise((res) => { $('#review-skip').onclick = () => { ctrl.abort(); res(null); }; });
    const asked = fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: ctrl.signal,
      body: JSON.stringify({ markdown, use_ai: true }) })
      .then((res) => (res.ok ? res.json() : null)).catch(() => null);
    const ar = await Promise.race([asked, skipped]);
    $('#review-wait').hidden = true;
    $('#review-main').hidden = false;
    $('#review-modal').hidden = true;
    if (ar) r = ar;
    else r = { ...r, ai_error: r.ai_error || 'AI 검토를 건너뛰었습니다' };
  }
  if (!r.issues.length) return true;
  return showReview(r);
}

// 어느 모델이 답했는지 (가벼운 예비 모델이면 그렇다고 알려 줌)
function modelNote(m) {
  if (!m) return '';
  return /lite/i.test(m) ? ` (${m} · 혼잡해서 가벼운 예비 모델이 답함)` : ` (${m})`;
}

function showReview(r) {
  return new Promise((resolve) => {
    const lv = { 위험: 'bad', 주의: 'warn', 참고: 'blue' };
    $('#review-sub').textContent = `수정 때문에 생길 수 있는 문제 ${r.issues.length}개 · 검사: 코드${r.ai_used ? ` + AI${modelNote(r.ai_model)}` : ''}`
      + (r.ai_error ? ` (AI 검사 안 됨: ${r.ai_error})` : '');
    $('#review-list').innerHTML = r.issues.map((i) => `<li>${tag(i.level, lv[i.level] || '')}
      ${i.where ? `<b>${esc(i.where)}</b>` : ''} ${esc(i.message)} <small>${esc(i.by)}</small></li>`).join('');
    $('#review-modal').hidden = false;
    const done = (v) => { $('#review-modal').hidden = true; resolve(v); };
    $('#review-ok').onclick = () => done(true);
    $('#review-back').onclick = () => done(false);
    $('#review-back').focus();
  });
}

async function undoManual() {
  try {
    renderManual(await api(`/api/manuals/${encodeURIComponent(state.recId)}/undo`, { body: {} }));
    toast('이전 버전으로 되돌렸습니다', 'ok');
  } catch (e) { toast(e.message, 'bad'); }
}

// 전체 편집: 매뉴얼 전체를 한 번에 고치기
$('#manual-edit-all').onclick = () => {
  const view = $('#manual-view');
  if (view.querySelector('.mblock.editing.all')) return;
  view.innerHTML = `<section class="mblock editing all"><textarea aria-label="매뉴얼 전체">${esc(state.manual.markdown)}</textarea>
    <div class="medit-actions"><button class="primary sm" data-save>전체 저장</button><button class="ghost sm" data-cancel>취소</button>
    <span class="hint">Ctrl+Enter 저장 · Esc 취소</span></div></section>`;
  const ta = $('textarea', view);
  ta.style.height = `${Math.min(innerHeight * 0.7, Math.max(300, ta.scrollHeight + 4))}px`;
  ta.focus();
  $('[data-cancel]', view).onclick = () => renderManual(state.manual);
  $('[data-save]', view).onclick = () => saveManual(ta.value);
  ta.onkeydown = (e) => { if (e.key === 'Escape') renderManual(state.manual); if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) saveManual(ta.value); };
};

// PDF는 만드는 데 몇 초 걸리므로 버튼에 진행 표시
$('#manual-pdf').onclick = async (e) => {
  e.preventDefault();
  const a = e.currentTarget;
  if (a.classList.contains('loading')) return;
  a.classList.add('loading');
  const label = a.textContent;
  a.textContent = 'PDF 만드는 중…';
  try {
    const res = await fetch(a.href);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = m ? decodeURIComponent(m[1]) : '매뉴얼.pdf';
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    toast('PDF를 내려받았습니다', 'ok');
  } catch (err) { toast(`PDF 실패: ${err.message}`, 'bad'); }
  finally { a.classList.remove('loading'); a.textContent = label; }
};

// ================================================================ AI: 매뉴얼에 물어보기 / 말로 고치기

const aiOn = () => state.hasKey && useAI();

function renderAiCards(d) {
  $('#chat-card').hidden = !d.steps;
  $('#edit-card').hidden = !d.macro_data;
  const off = !aiOn();
  $('#chat-off').hidden = !off; $('#edit-off').hidden = !off;
  ['#chat-q', '#chat-send', '#edit-req', '#edit-run'].forEach((s) => ($(s).disabled = off));
  // 시험 실행에 필요한 '직접 입력' 변수 (비밀번호 등)
  const secrets = (d.macro_data?.variables || []).filter((v) => v.default_rule === '직접 입력' || v.recorded_value === '***');
  $('#edit-vars').innerHTML = secrets.map((v) => `<label>${esc(v.name)} <small>바꾼 매크로 시험 실행에 필요 (저장하지 않음)</small>
    <input type="password" data-evar="${esc(v.name)}" autocomplete="off"></label>`).join('');
  $('#edit-result').innerHTML = '';
  $('#edit-job').hidden = true;
  if (d.steps) loadChat();
}
$('#use-ai').addEventListener('change', () => state.detail && renderAiCards(state.detail));

// ---- 매뉴얼에 물어보기
const BASIS_TAG = { '근거 있음': 'ok', '일부 추정': 'warn', '추정': 'bad', '일반 지식': 'blue' };
function chatItemHtml(c, i) {
  return `<div class="chat-q">${esc(c.question)}</div>
    <div class="chat-a">
      <div class="chat-meta">${tag(c.basis, BASIS_TAG[c.basis] || '')}${(c.refs || []).map((r) => `<span class="chip">${esc(r)}</span>`).join('')}${c.model ? `<small class="chat-model">${esc(modelNote(c.model).trim())}</small>` : ''}</div>
      <div class="chat-text">${esc(c.answer).replace(/\n/g, '<br>')}</div>
      ${c.add_to_manual ? `<div class="chat-add"><span>📌 ${esc(c.add_to_manual)}</span>
        <button class="sm" data-add="${i}">매뉴얼 판단 기준에 추가</button></div>` : ''}
    </div>`;
}
async function loadChat() {
  try { state.chat = await api(`/api/manuals/${encodeURIComponent(state.recId)}/chat`); } catch (e) { state.chat = []; }
  renderChat();
}
function renderChat() {
  const log = $('#chat-log');
  log.innerHTML = state.chat.map(chatItemHtml).join('') || '<p class="hint chat-empty">아직 질문이 없습니다. 아래에 궁금한 것을 적어 보세요.</p>';
  $$('[data-add]', log).forEach((b) => (b.onclick = async () => {
    const c = state.chat[+b.dataset.add];
    try {
      renderManual(await api(`/api/manuals/${encodeURIComponent(state.recId)}/criteria`, { body: { text: c.add_to_manual } }));
      toast('매뉴얼 "판단 기준"에 추가했습니다 (되돌리기 가능)', 'ok', { label: '되돌리기', onClick: undoManual });
      b.disabled = true; b.textContent = '추가함';
    } catch (e) { toast(e.message, 'bad'); }
  }));
  log.scrollTop = log.scrollHeight;
}
$('#chat-form').onsubmit = async (e) => {
  e.preventDefault();
  const q = $('#chat-q').value.trim();
  if (!q) return;
  $('#chat-q').value = '';
  const log = $('#chat-log');
  $('.chat-empty', log)?.remove();
  log.insertAdjacentHTML('beforeend', `<div class="chat-q">${esc(q)}</div><div class="chat-a pending"><span class="dots"><i></i><i></i><i></i></span> 매뉴얼과 녹화를 살펴보는 중…</div>`);
  log.scrollTop = log.scrollHeight;
  $('#chat-send').classList.add('loading'); $('#chat-send').disabled = true;
  try {
    const item = await api(`/api/manuals/${encodeURIComponent(state.recId)}/ask`, { body: { question: q } });
    state.chat.push(item);
  } catch (err) { toast(err.message, 'bad'); }
  finally { $('#chat-send').classList.remove('loading'); $('#chat-send').disabled = !aiOn(); }
  renderChat();
};
$('#chat-clear').onclick = async () => {
  if (!confirm('이 녹화의 질문·답변 기록을 지울까요?')) return;
  await api(`/api/manuals/${encodeURIComponent(state.recId)}/chat/clear`, { body: {} }).catch(() => {});
  state.chat = []; renderChat();
};

// ---- 말로 고치기
const FIELD_NAME = { value: '값', description: '설명', target: '대상 이름', 'excel.header': '열', 'excel.formula': '수식',
  'excel.cond.values': '조건 값', 'excel.scope.header': '채울 행 조건 열', 'excel.scope.values': '채울 행 조건 값' };
$('#edit-run').onclick = async () => {
  const request = $('#edit-req').value.trim();
  if (!request) { toast('무엇을 바꿀지 적어 주세요', 'bad'); $('#edit-req').focus(); return; }
  const values = {};
  $$('#edit-vars [data-evar]').forEach((i) => { if (i.value) values[i.dataset.evar] = i.value; });
  $('#edit-result').innerHTML = '';
  const job = await runJob({ kind: 'edit', rid: state.recId, request, values }, 'edit', [$('#edit-run')], '변경안 만들기');
  if (job.result) renderProposal(job.result);
};

function renderProposal(p) {
  const box = $('#edit-result');
  if (p.feasible === 'needs_recording') {
    box.innerHTML = `<div class="banner warn">🎬 이 변경은 녹화가 필요합니다<small>${esc(p.reason || p.summary)}</small></div>
      ${p.alternative ? `<div class="alt"><b>대안</b><div>${esc(p.alternative).replace(/\n/g, '<br>')}</div></div>` : ''}
      <div class="alt"><b>추천 방법</b><div>바뀐 부분(예: B 사이트에서 받아오기)만 <b>새로 녹화</b>해 매크로로 만들고,
        그대로 쓰는 부분(예: Excel 정리)과 <b>🔗 업무 연결</b>로 이어 붙이면 전체를 다시 녹화하지 않아도 됩니다.</div></div>
      <div class="actions"><button class="primary" id="alt-rec">🎬 바뀐 부분만 녹화하러 가기</button>
        <button id="alt-chain">🔗 업무 연결 만들러 가기</button></div>`;
    $('#alt-rec').onclick = () => { showTab('record'); setRecMode('site'); $('#rec-name').value = `${state.detail?.name || ''}_바뀐부분`; $('#rec-name').focus(); };
    $('#alt-chain').onclick = () => { showTab('run'); setTimeout(() => { $('#chain-card').scrollIntoView({ behavior: 'smooth' }); $('#chain-new').click(); }, 600); };
    return;
  }
  if (p.feasible !== 'yes') {
    box.innerHTML = `<div class="banner warn">🤔 요청을 조금 더 구체적으로 적어 주세요<small>${esc(p.reason || p.summary)}</small></div>`;
    return;
  }
  const mc = p.macro_changes.map((c) => `<tr><td>${c.step}</td><td>${esc(FIELD_NAME[c.field] || c.field)}</td>
    <td class="before">${esc(c.before)}</td><td class="after">${esc(c.after)}</td></tr>`).join('');
  const wc = p.web_changes.map((c) => `<tr><td>${c.step}</td><td>${c.adapted ? tag('이어서 맞춤', 'blue') : '화면 항목'}</td>
    <td class="before">${esc(c.before.target)} (${esc(c.before.action)})</td>
    <td class="after">${esc(c.after.target)} (${esc(c.after.action)})<small>찾은 항목: ${esc(c.element.tag)}${c.element.id ? '#' + esc(c.element.id) : ''}</small></td></tr>`).join('');
  const man = p.manual_changes.map((c) => `<div class="mdiff"><div class="before">${esc(c.before).replace(/\n/g, '<br>')}</div>
    <div class="arrow">→</div><div class="after">${esc(c.after).replace(/\n/g, '<br>')}</div></div>`).join('');
  const t = p.test || {};
  box.innerHTML = `
    <div class="banner ${p.applicable ? 'ok' : 'bad'}">${p.applicable ? '✅ 변경안이 준비됐습니다' : '⛔ 이대로는 적용할 수 없습니다'}
      <small>${esc(p.summary)}</small></div>
    ${p.problems.length ? `<ul class="review-list">${p.problems.map((x) => `<li>${tag('문제', 'bad')} ${esc(x)}</li>`).join('')}</ul>` : ''}
    ${p.warnings.length ? `<ul class="review-list">${p.warnings.map((x) => `<li>${tag('확인', 'warn')} ${esc(x)}</li>`).join('')}</ul>` : ''}
    ${mc || wc ? `<h3>매크로 변경</h3><div class="table-wrap"><table class="diff"><thead><tr><th>단계</th><th>항목</th><th>지금</th><th>바뀐 뒤</th></tr></thead>
      <tbody>${mc}${wc}</tbody></table></div>` : ''}
    ${man ? `<h3>매뉴얼 변경</h3>${man}` : ''}
    <h3>시험 실행</h3><p class="test-line ${t.success ? 'ok' : 'bad'}">${t.ran ? (t.success ? '✅ ' : '⛔ ') : 'ℹ '}${esc(t.message || '')}</p>
    <div class="actions">
      <button class="primary" id="edit-apply" ${p.applicable ? '' : 'disabled'}>✅ 적용</button>
      <button class="ghost" id="edit-discard">버리기</button>
      <span class="hint">적용하면 매크로와 매뉴얼이 바뀌고, 지금 내용은 이전 버전으로 보관됩니다</span>
    </div>`;
  $('#edit-discard').onclick = () => { box.innerHTML = ''; };
  $('#edit-apply').onclick = async () => {
    try {
      await api(`/api/edits/${encodeURIComponent(p.rid)}/${p.id}/apply`, { body: {} });
      toast('변경안을 적용했습니다', 'ok');
      confetti();
      $('#edit-req').value = '';
      await loadDetail();
      $('#manual-area').scrollIntoView({ behavior: 'smooth' });
    } catch (e) { toast(e.message, 'bad'); }
  };
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('#manual-undo').onclick = () => { if (confirm('가장 최근에 보관한 이전 버전으로 되돌릴까요?')) undoManual(); };

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
  if (state.manual?.edited && !confirm('직접 고친 매뉴얼이 있습니다.\n다시 생성하면 새 내용으로 바뀝니다. (지금 내용은 "↶ 이전 버전"으로 되돌릴 수 있게 보관됩니다)\n계속할까요?')) return;
  if ($$('#qa-form textarea').length) await saveAnswers(true).catch(() => {});   // 적어 둔 답변은 먼저 저장
  const job = await runJob({ kind: 'manual', rid: state.recId, use_ai: useAI() }, 'man', [$('#btn-manual'), $('#btn-questions')], '매뉴얼 생성');
  if (job.status === 'done') { await loadManual(); $('#manual-area').scrollIntoView({ behavior: 'smooth' }); }
};

// ================================================================ ④ 매크로 실행

async function loadMacros() {
  const list = state.macros = await api('/api/macros');
  loadChains();
  const visible = list.filter((m) => !m.is_eval);   // 평가 스크립트가 만든 eval_* 매크로는 목록에서 뺀다
  const sel = $('#macro-select');
  const prev = sel.value;
  sel.innerHTML = visible.map((m) =>
    `<option value="${esc(m.id)}">${m.kind === 'excel' ? '📊 ' : '🌐 '}${esc(m.name || m.id)} · 단계 ${m.steps} · 변수 ${m.variables}</option>`).join('');
  if (visible.find((m) => m.id === prev)) sel.value = prev;
  $('#macro-empty').hidden = visible.length > 0;
  $('#macro-area').hidden = !visible.length;
  if (visible.length) loadMacro();
}

async function loadMacro() {
  const m = state.macro = await api(`/api/macros/${encodeURIComponent($('#macro-select').value)}`);
  const hasFile = m.variables.some((v) => v.type === 'file');
  if (hasFile) await loadMacroFiles(m.id);
  $('#var-form').innerHTML = m.variables.map((v) => v.type === 'file' ? `
    <div class="vlabel span-all">${esc(v.name)} <small>이번에 처리할 파일 — 녹화 때와 <b>같은 양식</b>의 다른 파일(예: 다음 달 파일)을 고르면 같은 작업을 해 줍니다</small>
      <span class="var-file"><select data-var="${esc(v.name)}" data-default="${esc(v.default ?? '')}" aria-label="${esc(v.name)}"></select>
        <label class="button">⬆ 내 PC 파일 올리기<input type="file" data-upload="${esc(v.name)}" accept=".xlsx,.xlsm,.xls,.csv" hidden></label></span>
      <small class="file-match" data-match-for="${esc(v.name)}"></small>
    </div>` : `
    <label>${esc(v.name)} <small>${esc(v.description || '')} · ${esc(v.default_rule)}</small>
      <input data-var="${esc(v.name)}" type="${v.secret ? 'password' : 'text'}"
        value="${v.secret ? '' : esc(v.default ?? '')}" placeholder="${v.secret ? '실행할 때마다 입력' : ''}">
    </label>`).join('') || '<p class="hint">변수가 없는 매크로입니다.</p>';
  $$('#var-form select[data-var]').forEach((s) => fillMacroFileSelect(s, s.dataset.default, s.dataset.default));
  $$('#var-form [data-upload]').forEach((inp) => (inp.onchange = async () => {
    const path = await uploadExcel(inp);
    const sel = $(`#var-form select[data-var="${CSS.escape(inp.dataset.upload)}"]`);
    if (path) { await loadMacroFiles(state.macro.id); fillMacroFileSelect(sel, path, sel.dataset.default); renderMacroSteps(); }
  }));
  $$('#var-form [data-var]').forEach((i) => (i.oninput = i.onchange = renderMacroSteps));
  const changes = m.steps.some((s) => s.changes_data);
  $('#opt-allow').disabled = !changes;
  $('#change-warn').hidden = !changes || $('#opt-allow').checked;
  $('#run-result').hidden = true;
  $('#run-job').hidden = true;
  renderMacroSteps(true);
}

// ---------------- 엑셀 매크로의 입력 파일: 녹화 때와 같은 양식인지 표시
async function loadMacroFiles(mid) {
  try { state.macroFiles = await api(`/api/macros/${encodeURIComponent(mid)}/files`); } catch (e) { state.macroFiles = []; }
}

const MATCH_GROUP = { true: '✓ 녹화 때와 같은 양식 (실행 가능)', null: '양식 확인 못 함', false: '✗ 다른 양식 (실행하면 첫 단계에서 멈춤)' };

function fillMacroFileSelect(sel, selected, extra = '', extraLabel = '(녹화 때 파일)') {
  const files = state.macroFiles || [];
  const groups = {};
  files.forEach((f) => (groups[String(f.match)] ||= []).push(f));
  let html = ['true', 'null', 'false'].filter((k) => groups[k]).map((k) =>
    `<optgroup label="${esc(MATCH_GROUP[k])}">${groups[k].map((f) =>
      `<option value="${esc(f.path)}" data-match="${k}">${esc(f.name)}${f.group === '올린 파일' ? ' (올린 파일)' : ''}</option>`).join('')}</optgroup>`).join('');
  if (extra && !files.some((f) => f.path === extra)) {
    html = `<option value="${esc(extra)}" data-match="null">${esc(extra.split('/').pop())} ${esc(extraLabel)}</option>` + html;
  }
  sel.innerHTML = html || '<option value="">엑셀 파일이 없습니다 — ⬆ 버튼으로 올리세요</option>';
  if ([...sel.options].some((o) => o.value === selected)) sel.value = selected;
  const note = () => {
    const box = $(`[data-match-for="${CSS.escape(sel.dataset.var)}"]`);
    const k = sel.selectedOptions[0]?.dataset.match;
    if (!box) return;
    box.className = `file-match ${k === 'true' ? 'ok' : k === 'false' ? 'bad' : ''}`;
    box.textContent = k === 'true' ? '✓ 녹화 때 파일과 머리글이 같습니다'
      : k === 'false' ? '✗ 머리글(열 이름)이 녹화 때와 다릅니다. 실행하면 첫 단계에서 멈춥니다' : '';
  };
  sel.addEventListener('change', note);
  note();
}

function varValues() {
  const values = {};
  $$('#var-form [data-var]').forEach((i) => (values[i.dataset.var] = i.value));
  return values;
}

function renderMacroSteps(animate) {
  const values = varValues();
  const secret = new Set(state.macro.variables.filter((v) => v.secret).map((v) => v.name));
  const fill = (s) => (s || '').replace(/\{\{(.+?)\}\}/g, (_, n) => (secret.has(n) ? '***' : values[n] ?? ''));
  const tbody = $('#macro-steps tbody');
  tbody.innerHTML = state.macro.steps.map((s) => `
    <tr><td>${s.step}</td><td>${tag(s.action, 'blue')}</td><td>${esc(s.description)}</td>
    <td><b>${['입력', '선택'].includes(s.action) || s.excel ? esc(fill(s.value)) : ''}</b></td>
    <td>${s.changes_data ? tag('데이터 변경', 'warn') : ''}</td></tr>`).join('');
  if (animate === true) animateRows(tbody);
}

$('#macro-select').onchange = loadMacro;
$('#opt-allow').onchange = (e) => ($('#change-warn').hidden = e.target.checked || !state.macro.steps.some((s) => s.changes_data));

$('#btn-run').onclick = async () => {
  const values = varValues();
  const missing = Object.entries(values).filter(([, v]) => !v.trim()).map(([k]) => k);
  if (missing.length) { toast(`값을 입력하세요: ${missing.join(', ')}`, 'bad'); return; }
  $('#run-result').hidden = true;
  const xlMacro = state.macro.kind === 'excel';
  $('#run-msg').textContent = $('#opt-headless').checked ? ''
    : xlMacro ? 'Excel 창이 열려 자동으로 작업합니다. 건드리지 말고 지켜보세요.' : '자동 실행용 브라우저 창이 열립니다. 건드리지 말고 지켜보세요.';
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
    ${r.downloads.length ? `<p><b>${r.kind === 'excel' ? '📊 결과 파일 (눌러서 받기)' : '다운로드한 파일'}:</b> ${r.downloads.map((f) =>
      `<a href="/files/runs/${encodeURIComponent(runName)}/${encodeURIComponent(f)}">${esc(f)}</a>`).join(', ')}</p>` : ''}
    ${r.messages.length ? `<p><b>화면 메시지:</b> ${r.messages.map(esc).join(' / ')}</p>` : ''}
    <div class="table-wrap"><table><thead><tr><th>#</th><th>결과</th><th>동작</th><th>대상</th><th>메시지</th></tr></thead><tbody>
    ${r.steps.map((s) => `<tr><td>${s.step}</td><td>${tag(s.status, s.status === '실패' ? 'bad' : 'ok')}</td>
      <td>${esc(s.action)}</td><td>${esc(s.target)}</td><td>${esc(s.message)}</td></tr>`).join('')}
    </tbody></table></div>
    ${fail ? `<p><b>멈춘 화면</b></p><img class="fail-shot" src="/files/runs/${encodeURIComponent(runName)}/${fail.screenshot}">` : ''}`;
  animateRows($('tbody', box));
  if (r.success && r.downloads.length) addNextActions(box, `runs/${runName}/${r.downloads[0]}`);
  box.scrollIntoView({ behavior: 'smooth' });
  if (r.success) confetti();
}

// ---------------- 실행 결과 파일로 이어서 하기: 다음 매크로 실행 / Excel 녹화
function addNextActions(box, filePath) {
  const fileName = filePath.split('/').pop();
  const targets = (state.macros || []).filter((m) => m.file_vars?.length && m.id !== $('#macro-select').value);
  const isExcelFile = /\.(xlsx|xlsm|xls|csv)$/i.test(fileName);
  const div = document.createElement('div');
  div.className = 'next-actions';
  div.innerHTML = `<b>🔗 이 결과(${esc(fileName)})로 이어서:</b>
    ${targets.length ? `<select id="next-macro" aria-label="다음 업무 매크로">${targets.map((m) =>
      `<option value="${esc(m.id)}">📊 ${esc(m.name || m.id)}</option>`).join('')}</select>
      <button class="sm" id="next-run">다음 업무 실행 준비</button>` : '<span class="hint">입력 파일을 받는 매크로(엑셀 매크로)가 아직 없습니다.</span>'}
    ${isExcelFile ? '<button class="sm ghost" id="next-record">📊 이 파일로 Excel 녹화</button>' : ''}`;
  box.appendChild(div);
  const runBtn = $('#next-run', div);
  if (runBtn) runBtn.onclick = () => prepareNextMacro($('#next-macro', div).value, filePath);
  const recBtn = $('#next-record', div);
  if (recBtn) recBtn.onclick = () => recordWithFile(filePath);
}

// 다음 매크로를 고르고, 그 입력 파일 칸에 앞 결과 파일을 넣어 둔다 (실행은 사용자가 확인 후)
async function prepareNextMacro(mid, filePath) {
  await loadMacros();
  $('#macro-select').value = mid;
  await loadMacro();
  const sel = $('#var-form select[data-var]');
  if (sel) { fillMacroFileSelect(sel, filePath, filePath, '(앞 업무 결과)'); renderMacroSteps(); }
  $('#var-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
  toast('입력 파일에 앞 업무 결과를 넣었습니다. 확인 후 ⚡ 실행을 누르세요', 'ok');
}

// 결과 파일로 Excel 녹화 시작 준비 (녹화 탭으로 이동, Excel 모드, 그 파일 선택)
async function recordWithFile(filePath) {
  showTab('record');
  setRecMode('excel');
  await loadExcelFiles();
  fillFileSelect($('#xl-file'), filePath, filePath, '(매크로 실행 결과)');
  checkXlWarn();
  $('#rec-name').focus();
  toast('녹화 이름을 입력하고 ● 녹화 시작을 누르세요', 'info');
}

// ================================================================ 🔗 업무 연결 (A 결과 → B 입력)

const macroById = (id) => (state.macros || []).find((m) => m.id === id);
const macroLabel = (m) => `${m?.kind === 'excel' ? '📊' : '🌐'} ${m?.name || m?.id || '(없는 매크로)'}`;

async function loadChains() {
  let list = [];
  try { list = state.chains = await api('/api/chains'); } catch (e) { state.chains = []; }
  $('#chain-list').innerHTML = list.map((c) => `
    <div class="chain-row">
      <b>${esc(c.name)}</b>
      <div class="chain-flow">${c.steps.map((s, i) => `${i ? '<span class="arrow">→ 결과 파일 →</span>' : ''}
        <span class="node ${s.kind === 'excel' ? 'excel' : ''}">${s.kind === 'excel' ? '📊' : '🌐'} ${esc(s.name)}</span>`).join('')}</div>
      ${c.problems.length ? `<span class="tag warn" title="${esc(c.problems.join(' / '))}">⚠ 설정 확인 필요</span>` : ''}
      <button class="sm" data-chain-run="${esc(c.id)}" ${c.problems.length ? 'disabled' : ''}>▶ 실행 준비</button>
      <button class="ghost sm" data-chain-del="${esc(c.id)}">삭제</button>
    </div>`).join('') || '<p class="hint">저장된 연결이 없습니다. <b>＋ 새 연결</b>로 "A 업무 결과 → B 업무" 흐름을 만들어 보세요.</p>';
  $$('[data-chain-run]').forEach((b) => (b.onclick = () => prepareChain(b.dataset.chainRun)));
  $$('[data-chain-del]').forEach((b) => (b.onclick = async () => {
    const c = state.chains.find((x) => x.id === b.dataset.chainDel);
    if (!confirm(`'${c.name}' 연결을 삭제할까요? (매크로는 지워지지 않습니다)`)) return;
    try { await api(`/api/chains/${encodeURIComponent(c.id)}/delete`, { body: {} }); toast('연결을 삭제했습니다', 'info'); } catch (e) { toast(e.message, 'bad'); }
    loadChains();
  }));
}

// ---- 연결 만들기
state.chainDraft = [];
function renderChainEditor() {
  const all = state.macros || [];
  const fileMacros = all.filter((m) => m.file_vars?.length);
  $('#chain-steps').innerHTML = state.chainDraft.map((s, i) => {
    const pool = i === 0 ? all : fileMacros;
    const m = macroById(s.macro);
    return `<div class="chain-step">
      <span class="no">${i + 1}</span>
      <label>${i === 0 ? '처음 업무' : `${i + 1}번째 업무 (앞 결과를 받음)`}
        <select data-step-macro="${i}">${pool.map((x) => `<option value="${esc(x.id)}" ${x.id === s.macro ? 'selected' : ''}>${esc(macroLabel(x))}</option>`).join('')
          || '<option value="">입력 파일을 받는 매크로가 없습니다</option>'}</select></label>
      <div class="link-col">${i === 0 ? '<p class="link-note">이 업무의 다운로드 파일·결과 파일이 다음 업무로 넘어갑니다</p>'
        : `<label>앞 결과를 넣을 칸<select data-step-link="${i}">${(m?.file_vars || []).map((v) => `<option ${v === s.link_var ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></label>`}</div>
      ${i >= 2 ? `<button class="ghost rm" data-step-rm="${i}" title="이 업무 빼기">✕</button>` : '<span></span>'}
    </div>`;
  }).join('');
  $$('[data-step-macro]').forEach((sel) => (sel.onchange = () => {
    const i = +sel.dataset.stepMacro;
    state.chainDraft[i].macro = sel.value;
    state.chainDraft[i].link_var = macroById(sel.value)?.file_vars?.[0] || '';
    renderChainEditor();
  }));
  $$('[data-step-link]').forEach((sel) => (sel.onchange = () => (state.chainDraft[+sel.dataset.stepLink].link_var = sel.value)));
  $$('[data-step-rm]').forEach((b) => (b.onclick = () => { state.chainDraft.splice(+b.dataset.stepRm, 1); renderChainEditor(); }));
}
function newDraftStep(i) {
  const pool = i === 0 ? (state.macros || []) : (state.macros || []).filter((m) => m.file_vars?.length);
  const m = pool.find((x) => !state.chainDraft.some((s) => s.macro === x.id)) || pool[0];
  return { macro: m?.id || '', link_var: i ? (m?.file_vars?.[0] || '') : undefined };
}
$('#chain-new').onclick = () => {
  if (!(state.macros || []).length) { toast('먼저 매크로를 만드세요', 'bad'); return; }
  state.chainDraft = [];
  state.chainDraft.push(newDraftStep(0));
  state.chainDraft.push(newDraftStep(1));
  $('#chain-name').value = '';
  $('#chain-editor').hidden = false;
  $('#chain-runner').hidden = true;
  renderChainEditor();
  $('#chain-name').focus();
};
$('#chain-add-step').onclick = () => { state.chainDraft.push(newDraftStep(state.chainDraft.length)); renderChainEditor(); };
$('#chain-cancel').onclick = () => ($('#chain-editor').hidden = true);
$('#chain-save').onclick = async () => {
  const name = $('#chain-name').value.trim();
  if (!name) { toast('연결 이름을 입력하세요', 'bad'); $('#chain-name').focus(); return; }
  try {
    await api('/api/chains', { body: { name, steps: state.chainDraft } });
    $('#chain-editor').hidden = true;
    toast('업무 연결을 저장했습니다', 'ok');
    loadChains();
  } catch (e) { toast(e.message, 'bad'); }
};

// ---- 연결 실행
async function prepareChain(cid) {
  const c = state.chains.find((x) => x.id === cid);
  state.chainRun = { chain: c, macros: [] };
  $('#chain-editor').hidden = true;
  $('#chain-runner').hidden = false;
  $('#chain-result').innerHTML = '';
  $('#chain-job').hidden = true;
  $('#chain-run-title').textContent = `🔗 ${c.name}`;
  await loadExcelFiles();
  const forms = [];
  for (const [i, s] of c.steps.entries()) {
    const m = await api(`/api/macros/${encodeURIComponent(s.macro)}`);
    state.chainRun.macros.push(m);
    const fields = m.variables.map((v) => {
      if (i > 0 && v.name === s.link_var) {
        return `<div class="vlabel">${esc(v.name)}<span class="linked">🔗 ${i}번째 업무의 결과 파일이 자동으로 들어갑니다</span></div>`;
      }
      if (v.type === 'file') {
        return `<div class="vlabel">${esc(v.name)} <small>${esc(v.description || '')}</small>
          <select data-cvar="${i}" data-name="${esc(v.name)}" data-default="${esc(v.default ?? '')}" aria-label="${esc(v.name)}"></select></div>`;
      }
      return `<label>${esc(v.name)} <small>${esc(v.description || '')}</small>
        <input data-cvar="${i}" data-name="${esc(v.name)}" type="${v.secret ? 'password' : 'text'}"
          value="${v.secret ? '' : esc(v.default ?? '')}" placeholder="${v.secret ? '실행할 때마다 입력' : ''}"></label>`;
    }).join('') || '<p class="hint">변수가 없는 매크로입니다.</p>';
    forms.push(`<fieldset><legend>${i + 1}. ${esc(macroLabel(m))}</legend><div class="var-grid">${fields}</div></fieldset>`);
  }
  $('#chain-run-forms').innerHTML = forms.join('');
  $$('#chain-run-forms select[data-cvar]').forEach((sel) => fillFileSelect(sel, sel.dataset.default, sel.dataset.default));
  const changes = state.chainRun.macros.some((m) => m.steps.some((s) => s.changes_data));
  $('#chain-opt-allow').disabled = !changes;
  $('#chain-runner').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
$('#chain-close').onclick = () => ($('#chain-runner').hidden = true);

$('#chain-run').onclick = async () => {
  const values = {};
  let missing = [];
  $$('#chain-run-forms [data-cvar]').forEach((el) => {
    (values[el.dataset.cvar] ||= {})[el.dataset.name] = el.value;
    if (!el.value.trim()) missing.push(`${+el.dataset.cvar + 1}번째 업무의 ${el.dataset.name}`);
  });
  if (missing.length) { toast(`값을 입력하세요: ${missing.join(', ')}`, 'bad'); return; }
  $('#chain-result').innerHTML = '';
  const job = await runJob({
    kind: 'chain', cid: state.chainRun.chain.id, values,
    checks: $('#chain-opt-checks').checked, allow_changes: $('#chain-opt-allow').checked, headless: $('#chain-opt-headless').checked,
  }, 'chain', [$('#chain-run')], '업무 연결 실행');
  if (job.result) renderChainResult(job.result);
};

function renderChainResult(r) {
  const fileLink = (runDir, f) => `<a href="/files/runs/${encodeURIComponent(runDir.split(/[\\/]/).pop())}/${encodeURIComponent(f)}">${esc(f)}</a>`;
  $('#chain-result').innerHTML = `
    ${r.success ? `<div class="banner ok">✅ 연결된 업무 ${r.steps.length}개를 모두 마쳤습니다</div>`
      : `<div class="banner bad">⛔ ${r.stopped_at ?? ''}번째 업무에서 멈췄습니다<small>${esc(r.stop_message || '')}</small></div>`}
    <div class="table-wrap"><table><thead><tr><th>#</th><th>업무</th><th>결과</th><th>파일</th><th>메시지</th></tr></thead><tbody>
    ${r.steps.map((s) => `<tr><td>${s.step}</td><td>${s.kind === 'excel' ? '📊' : '🌐'} ${esc(s.name)}</td>
      <td>${tag(s.success ? '성공' : '실패', s.success ? 'ok' : 'bad')}</td>
      <td>${s.downloads.map((f) => fileLink(s.run_dir, f)).join(', ')}</td><td>${esc(s.stop_message || '')}</td></tr>`).join('')}
    </tbody></table></div>`;
  animateRows($('#chain-result tbody'));
  if (r.success) confetti();
}

// ================================================================ 시작

refreshStatus();
setInterval(refreshStatus, 5000);
loadFavorites().then(checkSites);
setInterval(checkSites, 120000);   // 업무 사이트 접속 상태는 2분마다
// 처음에는 사용 안내부터. '다음부터 바로 시작'을 고른 사람은 마지막에 보던 화면으로
let firstTab = 'guide';
try {
  if (localStorage.getItem('skipGuide') === '1') {
    firstTab = localStorage.getItem('tab') || 'record';
    // 없어진 화면(예: 예전 '평가 결과')을 기억하고 있으면 녹화 화면으로
    if (firstTab === 'guide' || !$(`#tab-${firstTab}`)) firstTab = 'record';
  }
} catch (e) {}
showTab(firstTab);
let firstMode = 'site';
try { firstMode = localStorage.getItem('recMode') === 'excel' ? 'excel' : 'site'; } catch (e) {}
setRecMode(firstMode);
