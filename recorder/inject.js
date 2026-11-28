// 녹화용 이벤트 수집 스크립트. 모든 페이지(이동 후 포함)에 자동 주입된다.
// 수집한 이벤트는 Python 쪽 window.__recordEvent(...)로 보낸다.
(() => {
  if (window.__recorderInstalled) return;
  window.__recorderInstalled = true;

  const TEXT_TYPES = new Set(['text', 'password', 'email', 'number', 'date', 'datetime-local',
    'month', 'time', 'search', 'tel', 'url', 'week']);
  const pending = new Set();     // 입력 중이지만 아직 기록하지 않은 입력칸
  let lastSubmitterClick = 0;    // 제출 버튼 클릭 시각 (submit이 클릭으로 일어났는지 구분)

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 120);

  // 텍스트 입력칸인지 (키 입력마다 기록하지 않고 변경 완료 시점에 한 번만 기록)
  const isTextField = (el) =>
    (el.tagName === 'INPUT' && TEXT_TYPES.has((el.type || 'text').toLowerCase())) ||
    el.tagName === 'TEXTAREA';

  const maskedValue = (el) => (el.type === 'password' ? '***' : el.value);

  // 연결된 label 텍스트
  function labelOf(el) {
    try { if (el.labels && el.labels.length) return clean(el.labels[0].innerText); } catch (e) {}
    const aria = el.getAttribute && el.getAttribute('aria-label');
    if (aria) return clean(aria);
    const wrap = el.closest && el.closest('label');
    if (wrap) return clean(wrap.innerText);
    return '';
  }

  // 라디오 묶음 같은 그룹 제목 (fieldset > legend)
  function groupLabelOf(el) {
    const fs = el.closest && el.closest('fieldset');
    const lg = fs && fs.querySelector('legend');
    return lg ? clean(lg.innerText) : '';
  }

  // 버튼/링크에 보이는 텍스트
  function textOf(el) {
    if (el.tagName === 'INPUT') return ['submit', 'button', 'reset'].includes(el.type) ? clean(el.value) : '';
    if (['BUTTON', 'A'].includes(el.tagName) || el.getAttribute('role') === 'button') return clean(el.innerText);
    if (el.tagName === 'FORM') return '';
    return clean(el.innerText).slice(0, 40);
  }

  // 요소를 다시 찾을 수 있는 CSS 선택자
  function cssPath(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const tag = el.tagName.toLowerCase();
    if (el.name) {
      let sel = `${tag}[name="${el.name}"]`;
      if (el.type === 'radio' || el.type === 'checkbox') sel += `[value="${el.value}"]`;
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body) {
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(cur) + 1})`;
      }
      parts.unshift(part);
      cur = parent;
    }
    if (cur === document.body) parts.unshift('body');
    return parts.join(' > ');
  }

  // 링크·버튼 안에 제목(strong 등)이 있으면 그 짧은 글자 (예: 메뉴 카드의 업무 이름)
  function shortTextOf(el) {
    if (!['A', 'BUTTON'].includes(el.tagName)) return '';
    const head = el.querySelector('strong, b, h1, h2, h3, h4');
    return head ? clean(head.innerText) : '';
  }

  function describe(el) {
    const form = el.form || (el.closest && el.closest('form'));
    return {
      short_text: shortTextOf(el),
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      input_type: el.tagName === 'INPUT' ? (el.type || 'text') : '',
      label: labelOf(el),
      group_label: groupLabelOf(el),
      text: textOf(el),
      selector: cssPath(el),
      form_method: form ? (form.getAttribute('method') || 'get').toLowerCase() : '',
    };
  }

  // 화면에서 요소 위치 (스크린샷에 빨간 상자를 그리는 용도)
  function rectOf(el) {
    try {
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
    } catch (e) { return null; }
  }

  function send(type, el, extra) {
    try {
      window.__recordEvent({
        type, ...describe(el), ...(extra || {}), rect: el.tagName === 'FORM' ? null : rectOf(el),
        url: location.href, title: document.title, ts: Date.now(),
      });
    } catch (e) { /* 녹화기 연결이 끊긴 경우 무시 */ }
  }

  // change 이벤트 없이 다른 동작으로 넘어간 입력칸을 기록
  function flushPending() {
    for (const el of pending) send('input', el, { value: maskedValue(el) });
    pending.clear();
  }

  // 누르는 순간(페이지 이동 전) 화면을 미리 찍게 알린다. 클릭 순간에는 이미 이동이 시작될 수 있다.
  const prepare = () => { try { window.__recordPrep && window.__recordPrep(); } catch (e) {} };
  document.addEventListener('pointerdown', prepare, true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Enter') prepare(); }, true);

  document.addEventListener('input', (e) => {
    if (isTextField(e.target)) pending.add(e.target);
  }, true);

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (isTextField(el)) {
      pending.delete(el);
      send('input', el, { value: maskedValue(el) });
    } else if (el.tagName === 'SELECT') {
      const opt = el.selectedOptions && el.selectedOptions[0];
      send('change', el, { value: el.value, option_text: clean(opt ? opt.text : '') });
    }
  }, true);

  document.addEventListener('click', (e) => {
    if (!(e.target instanceof Element)) return;
    let el = e.target.closest('button, a, input, select, textarea, label, [role=button]') || e.target;
    if (el.tagName === 'SELECT' || el.tagName === 'OPTION') return;   // 드롭다운은 change로 기록
    if (el.tagName === 'LABEL' && el.control) return;                 // 연결된 입력칸 클릭으로 따로 기록됨
    if (isTextField(el)) return;                                      // 입력칸 포커스 클릭은 의미 없음
    flushPending();
    if ((el.tagName === 'BUTTON' || el.tagName === 'INPUT') && el.type === 'submit') {
      lastSubmitterClick = Date.now();
    }
    const extra = {};
    if (el.tagName === 'INPUT' && (el.type === 'radio' || el.type === 'checkbox')) {
      extra.value = el.value;
      extra.checked = el.checked;
    }
    if (el.tagName === 'A') extra.href = el.getAttribute('href') || '';
    send('click', el, extra);
  }, true);

  document.addEventListener('submit', (e) => {
    flushPending();
    const via = Date.now() - lastSubmitterClick < 1000 ? 'click' : 'enter';
    send('submit', e.target, { via });
  }, true);
})();
