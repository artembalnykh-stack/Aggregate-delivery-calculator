/* Калькулятор доставки — логика интерфейса (ванильный JS, без зависимостей) */
'use strict';

const API = {
  async get(url) {
    const r = await fetch(url);
    return r.json();
  },
  async post(url, body, method = 'POST') {
    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? '{}' : JSON.stringify(body),
    });
    return r.json();
  },
};

const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const fmtMoney = (v) => v === '—' ? v : fmt.format(parseFloat(v)) + ' ₽';
const fmtNum = (v) => fmt.format(parseFloat(v));

/* ------------------------------------------------ state */
const state = {
  data: null,            // /api/state
  order: [],             // [{sku, name, unit, price, qty, mass_kg, volume_m3, cargo_type}]
  lastCalc: null,        // последний snapshot
  lastCalcLoadedAt: null,
  mode: 'delivery',
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

/* ------------------------------------------------ toasts */
function toast(msg, kind = 'info', sub = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.innerHTML = `${msg}${sub ? `<div class="t-sub">${sub}</div>` : ''}`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .4s'; }, 4200);
  setTimeout(() => el.remove(), 4800);
}

/* ------------------------------------------------ порядок строк заказа */

/* шаг изменения количества по типу груза: штучный — целыми, остальное — по 0,1 */
const qtyStep = (cargoType) => (cargoType === 'Штучный' ? 1 : 0.1);

/* приводим количество к допустимому виду (после ручного ввода тоже) */
function snapQty(v, cargoType) {
  if (cargoType === 'Штучный') return Math.max(1, Math.round(v));
  return Math.max(0.1, Math.round(v * 10) / 10);
}

/* денежные/весовые округления — те же, что на сервере (копейки — HALF_UP) */
const r2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;

function orderAdd(product, qty = 1) {
  const existing = state.order.find(l => l.sku === product.sku);
  if (existing) {
    existing.qty = snapQty(existing.qty + qty, existing.cargo_type);
  } else {
    state.order.push({
      sku: product.sku, name: product.name, unit: product.unit,
      price: parseFloat(product.price), qty: snapQty(qty, product.cargo_type),
      mass_kg: parseFloat(product.mass_kg),
      volume_m3: product.volume_m3 ? parseFloat(product.volume_m3) : null,
      cargo_type: product.cargo_type, active: product.active,
    });
  }
  saveOrder(); renderOrder(); renderResultPreview();
}

function orderRemove(sku) {
  state.order = state.order.filter(l => l.sku !== sku);
  saveOrder(); renderOrder();
}

/* в localStorage хранится ТОЛЬКО {sku, qty}: цены/масса/объём всегда берутся
   из актуального прайса (resolveOrder) при каждой загрузке /api/state —
   после «Обновить» таблица и расчёт считает одни и те же свежие данные */
function saveOrder() {
  localStorage.setItem('ns_order',
    JSON.stringify(state.order.map(l => ({ sku: l.sku, qty: l.qty }))));
}
function loadOrder() {
  try { state.order = JSON.parse(localStorage.getItem('ns_order')) || []; }
  catch { state.order = []; }
}

/* пересобираем строки заказа по актуальному списку товаров;
   пропавшие из прайса SKU удаляются с уведомлением */
function resolveOrder() {
  if (!state.data) return;
  const bySku = new Map(state.data.products.map(p => [p.sku, p]));
  const kept = [], dropped = [];
  for (const l of state.order) {
    const p = bySku.get(l.sku);
    if (!p) { dropped.push(l.name || l.sku || '—'); continue; }
    kept.push({
      sku: p.sku, name: p.name, unit: p.unit,
      price: parseFloat(p.price), qty: snapQty(l.qty, p.cargo_type),
      mass_kg: parseFloat(p.mass_kg),
      volume_m3: p.volume_m3 ? parseFloat(p.volume_m3) : null,
      cargo_type: p.cargo_type, active: p.active,
    });
  }
  state.order = kept;
  if (dropped.length) {
    toast(`Нет в актуальном прайсе, убрано из заказа: ${dropped.join(', ')}`, 'warn');
  }
  saveOrder();
}

/* ------------------------------------------------ расчёт превью строки */
function lineSum(l) { return l.price * l.qty; }
function lineMass(l) { return l.mass_kg * l.qty / 1000; }
function lineVol(l) { return l.volume_m3 === null ? null : l.volume_m3 * l.qty; }

function renderResultPreview() {
  // итоговые значения округляются как на сервере (до копеек, HALF_UP),
  // чтобы превью никогда не расходилось с результатом «Рассчитать»
  const goods = r2(state.order.reduce((s, l) => s + lineSum(l), 0));
  const mass = r2(state.order.reduce((s, l) => s + lineMass(l), 0));
  const anyVol = state.order.some(l => lineVol(l) === null);
  $('#r-goods').textContent = state.order.length ? fmtNum(goods) + ' ₽' : '—';
  $('#r-mass').textContent = state.order.length ? fmtNum(mass) + ' т' : '—';
  $('#r-volume').textContent = anyVol && state.order.length ? 'частично нет данных'
    : state.order.length ? fmtNum(r2(state.order.reduce((s, l) => s + lineVol(l), 0))) + ' м³' : '—';
}

/* ------------------------------------------------ таблица заказа */
function renderOrder() {
  const body = $('#order-body');
  $('#order-empty').classList.toggle('hidden', state.order.length > 0);
  body.innerHTML = '';
  for (const l of state.order) {
    const tr = document.createElement('tr');
    const srEm = document.createElement('td'); srEm.textContent = l.sku;
    const nmEm = document.createElement('td'); nmEm.textContent = l.name;
    const unit = document.createElement('td'); unit.textContent = l.unit;
    const qty = document.createElement('td'); qty.className = 'ta-r';
    const qinp = document.createElement('input');
    qinp.type = 'number';
    const step = qtyStep(l.cargo_type);
    qinp.step = String(step); qinp.min = String(step);
    if (step === 1) qinp.placeholder = 'целое';
    qinp.className = 'qty-input'; qinp.value = l.qty;
    qinp.addEventListener('change', () => {
      let v = parseFloat(qinp.value.replace(',', '.'));
      if (Number.isNaN(v) || v <= 0) { toast('Количество должно быть больше нуля', 'warn'); qinp.value = l.qty; return; }
      v = snapQty(v, l.cargo_type);           // штучный — всегда целое, сыпучий — до 0,1
      if (v !== parseFloat(qinp.value)) qinp.value = v;
      l.qty = v; saveOrder(); renderOrder(); renderResultPreview();
      markStaleMaybe();
    });
    qty.appendChild(qinp);
    const mass = document.createElement('td'); mass.className = 'ta-r muted';
    mass.textContent = fmtNum(lineMass(l));
    const vol = document.createElement('td'); vol.className = 'ta-r muted';
    vol.textContent = lineVol(l) === null ? '—' : fmtNum(lineVol(l));
    const sum = document.createElement('td'); sum.className = 'ta-r';
    sum.innerHTML = `<b>${fmtNum(lineSum(l))}</b>`;
    const del = document.createElement('td');
    const dbtn = document.createElement('button');
    dbtn.className = 'del-row'; dbtn.innerHTML = '✕'; dbtn.title = 'Убрать позицию';
    dbtn.addEventListener('click', () => orderRemove(l.sku));
    del.appendChild(dbtn);
    tr.append(srEm, nmEm, unit, qty, mass, vol, sum, del);
    body.appendChild(tr);
  }
  renderResultPreview();
}

/* ------------------------------------------------ поиск товаров */
function productSearch(q) {
  q = (q || '').trim().toLowerCase();
  if (!state.data || q.length < 2) return [];
  const items = state.data.products.filter(p =>
    p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q));
  return items.slice(0, 30);
}

function renderSearchResults(items) {
  const box = $('#search-results');
  box.innerHTML = '';
  if (!items.length) { box.classList.add('hidden'); return; }
  for (const p of items) {
    const it = document.createElement('div');
    it.className = 'sr-item';
    it.innerHTML =
      `<div><div class="name">${p.sku} · ${p.name}</div>
        <div class="sub">${p.category} · ${p.unit} · ${p.active ? '' :
        '<span class="inactive-tag">не активен</span>'}</div></div>
       <div class="price">${fmtNum(p.price)} ₽</div>`;
    it.addEventListener('click', () => {
      orderAdd(p, 1);
      $('#inp-search').value = '';
      box.classList.add('hidden');
      toast(`Добавлено: ${p.name}`, 'ok');
    });
    box.appendChild(it);
  }
  box.classList.remove('hidden');
}

/* ------------------------------------------------ заполняем selects */
function fillSelects() {
  const wh = $('#inp-warehouse'), dst = $('#inp-destination');
  wh.innerHTML = state.data.warehouses.map(w =>
    `<option value="${w.id}" title="${w.address || ''}">${w.name}${w.city && w.city !== w.name ? ' · ' + w.city : ''}</option>`).join('');
  dst.innerHTML = state.data.destinations.map(d =>
    `<option value="${d.id}">${d.name}</option>`).join('');
  // демо-сценарий по умолчанию
  wh.value = state.data.warehouses[0]?.id ?? '';
  dst.value = (state.data.destinations.find(d => /ЮАО/.test(d.name)) ||
               state.data.destinations[0])?.id ?? '';
}

/* ------------------------------------------------ таблицы разделов */
function renderProducts(filter = '') {
  const q = filter.toLowerCase();
  const rows = (state.data?.products || []).filter(p =>
    !q || p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q)
        || p.category.toLowerCase().includes(q));
  $('#products-count').textContent = rows.length;
  const tbody = $('#products-table tbody');
  tbody.innerHTML = rows.slice(0, 400).map(p => `
    <tr>
      <td>${p.sku}</td><td>${p.name}</td><td>${p.category}</td><td>${p.unit}</td>
      <td class="ta-r"><b>${fmtNum(p.price)}</b></td>
      <td class="ta-r muted">${fmtNum(p.mass_kg)}</td>
      <td class="ta-r muted">${p.volume_m3 || '—'}</td>
      <td>${p.cargo_type}</td>
      <td>${p.active ? '<span class="badge badge-ok">да</span>' : '<span class="badge badge-off">нет</span>'}</td>
    </tr>`).join('');
}

function statusBadge(raw) {
  if (raw === true) return '<span class="badge badge-ok">да</span>';
  if (raw === false) return '<span class="badge badge-off">нет</span>';
  return '<span class="badge badge-unknown">не распознано</span>';
}

function renderFleet() {
  const v = state.data?.vehicles || [];
  $('#fleet-count').textContent = v.length;
  $('#fleet-table tbody').innerHTML = v.map(x => `
    <tr>
      <td>${x.id}</td><td><b>${x.model}</b></td><td class="muted">${x.plate}</td>
      <td>${x.body_type}</td>
      <td class="ta-r">${fmtNum(x.capacity_t)}</td>
      <td class="ta-r">${fmtNum(x.body_m3)}</td>
      <td>${statusBadge(x.online)}</td>
      <td>${statusBadge(x.serviceable)}</td>
    </tr>`).join('');
}

function renderTariffs() {
  const t = state.data?.tariffs || {};
  const r = state.data?.rules || {};
  const cells = [
    ['Базовый тариф за рейс', fmtMoney(t.base_per_trip || 0)],
    ['Тариф за километр', fmtMoney(t.per_km || 0) + '/км'],
    ['Мин. стоимость доставки', fmtMoney(t.min_delivery || 0)],
    ['Порог бесплатной доставки', t.free_threshold ? fmtMoney(t.free_threshold) : '—'],
    ['Макс. рейсов на машину', t.max_trips ?? '—'],
    ['Мин. объём доставки', t.min_volume ? t.min_volume + ' м³' : '—'],
    ['Коэф. расстояния «по прямой»', r.distance_coefficient || '—'],
    ['Режим обратного пути', r.return_mode_label || '—'],
  ];
  $('#tariff-view').innerHTML = cells.map(([k, v]) =>
    `<div class="tariff-cell"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
}

/* ------------------------------------------------ наряд-заказы */
async function renderOrders() {
  const res = await API.get('/api/exports');
  const items = res.items || [];
  $('#orders-count').textContent = items.length || '';
  $('#orders-empty').classList.toggle('hidden', items.length > 0);
  $('#orders-table tbody').innerHTML = items.map(f => `
    <tr>
      <td><b>🧾 ${f.name}</b></td>
      <td class="muted">${f.mtime}</td>
      <td class="ta-r muted">${(f.size / 1024).toFixed(1)} КБ</td>
      <td class="ta-r" style="white-space:nowrap">
        <a class="btn btn-success download-btn"
           href="/api/exports/download?name=${encodeURIComponent(f.name)}"
           download="КалькуляторДоставки_${f.name}">⬇ Скачать</a>
        <button class="btn btn-ghost" style="margin-left:6px"
                data-del="${encodeURIComponent(f.name)}">Удалить</button>
      </td>
    </tr>`).join('');
  $$('#orders-table [data-del]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Удалить документ наряд-заказа без возможности восстановления?')) return;
    const res2 = await API.post('/api/exports', { name: decodeURIComponent(b.dataset.del) }, 'DELETE');
    if (res2.ok) { toast('Документ удалён', 'ok'); renderOrders(); }
    else toast(res2.error, 'err');
  }));
}

async function renderArchive() {
  const res = await API.get('/api/archive');
  const items = res.items || [];
  $('#archive-list').innerHTML = items.length ? items.map(f => `
    <div class="archive-item">
      <div><div class="a-name">${f.name}</div></div>
      <div class="a-meta">${f.mtime}</div>
      <button class="btn btn-ghost" data-restore="${encodeURIComponent(f.name)}">Вернуть эту</button>
    </div>`).join('')
    : `<div class="muted small">Архив пуст — здесь появится прежняя база после первой кнопки «Обновить»</div>`;
  $$('#archive-list [data-restore]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Заменить текущий прайс версией из архива? Текущий тоже уйдёт в архив.')) return;
    const res2 = await API.post('/api/archive/restore', { name: decodeURIComponent(b.dataset.restore) });
    toast(res2.message, res2.ok ? 'ok' : 'err');
    if (res2.ok) { await bootstrap(true); updateStaleBadge(); }
  }));
}

async function updatePrice() {
  const btn = $('#btn-orders-refresh-price');
  btn.disabled = true; btn.textContent = 'Обновляем…';
  try {
    const url = $('#yd-url-orders').value.trim() || $('#yd-url').value.trim();
    await API.post('/api/settings', { yandex_disk_url: url });
    const res = await API.post('/api/download-price', { url });
    toast(res.message, res.ok ? 'ok' : 'err');
    if (res.ok) {
      await bootstrap(true);
      updateStaleBadge();
      renderArchive();          // в архиве появилась новая копия — покажем сразу
    }
  } finally {
    btn.disabled = false; btn.textContent = '🔄 Обновить';
  }
}

/* ------------------------------------------------ история */
async function renderHistory() {
  const res = await API.get('/api/history');
  const items = res.items || [];
  $('#history-empty').classList.toggle('hidden', items.length > 0);
  $('#nav-history-count').textContent = items.length || '';
  $('#history-table tbody').innerHTML = items.map(h => `
    <tr data-id="${h.id}" class="hrow">
      <td><b>${h.number}</b></td>
      <td class="muted">${new Date(h.created_at).toLocaleString('ru-RU')}</td>
      <td>${h.mode === 'delivery' ? '🚛 доставка' : '🎁 самовывоз'}</td>
      <td>${summary(h).склад || '—'}</td>
      <td>${summary(h).пункт || '—'}</td>
      <td class="ta-r"><b>${summary(h).итог || '—'}</b></td>
      <td><button class="btn" data-open="${h.id}">Открыть</button></td>
    </tr>`).join('');
  $$('#history-table button[data-open]').forEach(b =>
    b.addEventListener('click', () => openCalcDetail(b.dataset.open)));
}
function summary(h) {
  try { return JSON.parse(h.summary || '{}'); } catch { return {}; }
}

async function openCalcDetail(id) {
  const res = await API.get(`/api/history/${id}`);
  const snap = res.item?.snapshot;
  if (!snap) { toast('Расчёт не найден', 'err'); return; }
  state.lastCalc = snap; state.lastCalcLoadedAt = null;
  $('#btn-docx').disabled = false;
  $('#mc-title').textContent = `Расчёт ${res.item.number}`;
  $('#mc-body').innerHTML = detailsHtml(snap);
  $('#modal-calc').classList.remove('hidden');
  $('#mc-body-download').addEventListener('click', () => exportDocx(snap));
}

function detailsHtml(snap) {
  return `
    <div class="kv">
      <div class="k">Дата</div><div>${snap['дата'] || '—'}</div>
      <div class="k">Способ</div><div>${snap['режим'] === 'delivery' ? 'доставка' : 'самовывоз'}</div>
      <div class="k">Склад</div><div>${snap['склад'] || '—'}</div>
      <div class="k">Пункт</div><div>${snap['пункт'] || '—'}</div>
      <div class="k">Расстояние</div><div>${snap['прямая_км']} км по прямой → ${snap['расчётная_км']} км (${snap['режим_пути']})</div>
      <div class="k">Машины</div><div>${(snap['транспорт'] || []).map(a =>
        `${a.id} ${a.model} — ${a.trips} рейс(ов), ${a.cost} ₽`).join('; ') || '—'}</div>
      <div class="k">Товары</div><div>${snap['товары_сумма']} ₽</div>
      <div class="k">Доставка</div><div>${snap['доставка'] || '0'} ₽</div>
      <div class="k">Итог</div><div><b>${snap['итог']} ₽</b></div>
    </div>
    <div class="result-actions">
      <button class="btn btn-success" id="mc-body-download">📄 Заказ-наряд Word</button>
    </div>`;
}

/* ------------------------------------------------ расчёт */
function updateStaleBadge() {
  const loadedAt = state.data?.meta?.loaded_at;
  if (state.lastCalcLoadedAt && loadedAt && loadedAt !== state.lastCalcLoadedAt) {
    $('#stale-badge').classList.remove('hidden');
  }
}

function markStaleMaybe() { updateStaleBadge(); }

async function calculate() {
  if (!state.order.length) { toast('Добавьте хотя бы один товар', 'warn'); return; }
  if (state.mode === 'delivery' && (!$('#inp-warehouse').value || !$('#inp-destination').value)) {
    toast('Выберите склад и пункт доставки', 'warn'); return;
  }
  const btn = $('#btn-calc');
  btn.disabled = true; btn.textContent = 'Считаем…';
  try {
    const payload = {
      mode: state.mode,
      warehouse: $('#inp-warehouse').value,
      destination: $('#inp-destination').value,
      comment: $('#inp-comment').value.trim(),
      lines: state.order.map(l => ({ sku: l.sku, qty: l.qty })),
    };
    const res = await API.post('/api/calc', payload);
    state.lastCalcLoadedAt = state.data?.meta?.loaded_at;
    if (!res.ok) {
      $('#btn-docx').disabled = true;
      toast(res.error, 'err');
      renderFailure(res);
      return;
    }
    $('#stale-badge').classList.add('hidden');
    renderCalc(res.snapshot);
    state.lastCalc = res.snapshot;
    $('#btn-docx').disabled = false;
    toast('Расчёт выполнен и сохранён в историю', 'ok', `№ ${res.snapshot['номер']}`);
    renderHistory();
  } catch (e) {
    toast('Ошибка связи с сервером', 'err', String(e));
  } finally {
    btn.disabled = false; btn.textContent = 'Рассчитать';
  }
}

function renderFailure(res) {
  $('#r-total').textContent = '—';
  $('#r-total-sub').innerHTML = '';
  ['r-goods', 'r-mass', 'r-volume', 'r-dist', 'r-dist-calc', 'r-trip', 'r-delivery']
    .forEach(id => { $('#' + id).textContent = '—'; });
  $('#transport-block').classList.add('hidden');
  $('#excluded-block').classList.add('hidden');
  $('#r-warnings').innerHTML = `<div class="alert alert-err"><b>${res.error}</b></div>`;
  $('#r-notes').innerHTML = '';
}

function renderCalc(s) {
  const goods = parseFloat(s['товары_сумма'] || 0);
  const delivery = parseFloat(s['доставка'] || 0);
  const total = parseFloat(s['итог'] || 0);

  const tv = $('#r-total');
  tv.textContent = fmtNum(total) + ' ₽';
  tv.classList.remove('flash'); void tv.offsetWidth; tv.classList.add('flash');

  $('#r-total-sub').textContent =
    `товары ${fmtNum(goods)} ₽ + доставка ${fmtNum(delivery)} ₽` +
    (s['бесплатная_доставка'] ? ' (превышен порог — доставка бесплатно)' : '');

  $('#r-goods').textContent = fmtNum(goods) + ' ₽';
  $('#r-mass').textContent = fmtNum(s['масса_т']) + ' т';
  $('#r-volume').textContent = s['объём_известен'] ? fmtNum(s['объём_м3']) + ' м³' : 'нет данных';
  $('#r-dist').textContent = s['прямая_км'] ? fmtNum(s['прямая_км']) + ' км' : '—';
  $('#r-dist-calc').textContent = s['расчётная_км'] ? fmtNum(s['расчётная_км']) + ' км' : '—';
  $('#r-trip').textContent = s['рейс_стоимость'] ? fmtMoney(s['рейс_стоимость']) : '—';
  $('#r-delivery').textContent = fmtNum(delivery) + ' ₽';

  // транспорт
  const tb = $('#transport-block'), vb = $('#r-vehicles');
  const ass = s['транспорт'] || [];
  tb.classList.toggle('hidden', !(ass.length && state.mode === 'delivery'));
  vb.innerHTML = ass.map(a => `
    <div class="vehicle-chip">
      <div><b>${a.id} ${a.model}</b> <span class="sub">· ${a.plate} · ${a.body_type}</span>
        <div class="sub">рейсов: ${a.trips} (по весу ${a.trips_by_mass} / по объёму ${a.trips_by_volume ?? '—'})</div>
      </div>
      <div class="vcost">${fmtNum(a.cost)} ₽</div>
    </div>`).join('');

  const eb = $('#excluded-block');
  const ex = s['исключены'] || [];
  if (eb) eb.classList.add('hidden');   // блок неназначенных машин скрыт по ТЗ

  let html = '';
  if (state.mode === 'delivery' && s['примечание_расстояние']) {
    html += `<div class="alert alert-warn">🧭 ${s['примечание_расстояние']}</div>`;
  }
  if ((s['предупреждения'] || []).length) {
    html += `<div class="alert alert-warn">⚠️ <ul>${
      s['предупреждения'].map(w => `<li>${w}</li>`).join('')}</ul></div>`;
  }
  $('#r-warnings').innerHTML = html;
  $('#r-notes').innerHTML = (s['примечания'] || []).length
    ? `<div class="alert alert-info">ℹ️ <ul>${
        s['примечания'].map(w => `<li>${w}</li>`).join('')}</ul></div>` : '';
}

/* ------------------------------------------------ документ Word */
async function exportDocx(snap) {
  const target = snap || state.lastCalc;
  if (!target) { toast('Сначала выполните расчёт', 'warn'); return; }
  const res = await API.post('/api/export-docx', { snapshot: target });
  if (res.ok) toast('Документ Word сохранён', 'ok', `data/exports/${res.name}`);
  else toast(res.error, 'err');
}

/* ------------------------------------------------ настройки */
async function initSettings() {
  const s = state.data.settings?.yandex_disk_url || '';
  $('#yd-url').value = s;
  renderMeta();
}

function renderMeta() {
  const m = state.data?.meta || {};
  $('#data-status').textContent = m.loaded
    ? `Данные: ${m.source_file}\nЗагружено: ${new Date(m.loaded_at).toLocaleString('ru-RU')}`
    : 'Данные не загружены';
  $('#data-status').className = 'data-status' + (m.loaded ? ' ok' : '');
  $('#meta-data').innerHTML = m.loaded
    ? `Файл: <code>${m.source_file}</code><br>Обновлено: ${new Date(m.loaded_at).toLocaleString('ru-RU')}<br>
       товаров: ${m.counts?.products ?? '—'}, машин: ${m.counts?.vehicles ?? '—'}`
    : '—';
}

/* ------------------------------------------------ сброс к демо-сценарию */
function resetToDemo() {
  if (state.order.some(l => l.qty)) {
    if (!confirm('Сбросить поля калькулятора к демонстрационному заказу? Несохранённые изменения состава будут заменены.')) return;
  }
  state.order = [];
  const base = (state.data?.products || []).find(p => p.active && p.name.startsWith('Песок карьерный'));
  const destD = (state.data?.destinations || []).find(d => /ЮАО/.test(d.name));
  if (base) state.order.push({
    sku: base.sku, name: base.name, unit: base.unit,
    price: parseFloat(base.price), qty: 10,
    mass_kg: parseFloat(base.mass_kg),
    volume_m3: base.volume_m3 ? parseFloat(base.volume_m3) : null,
    cargo_type: base.cargo_type,
  });
  $('#inp-warehouse').value = state.data?.warehouses[0]?.id ?? '';
  $('#inp-destination').value = destD?.id ?? $('#inp-destination').options[0]?.value ?? '';
  $('#inp-comment').value = '';
  setMode('delivery');
  saveOrder(); renderOrder();
  toast('Поля сброшены к демо-заказу (история сохранена)', 'ok');
}

function setMode(mode) {
  state.mode = mode;
  $$('#seg-mode .seg-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === mode));
}

/* ------------------------------------------------ навигация и события */
function switchView(name) {
  $$('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
  if (name === 'history') renderHistory();
  if (name === 'orders') { renderOrders(); renderArchive(); }
  if (name === 'products') renderProducts($('#products-search')?.value || '');
  if (name === 'fleet') renderFleet();
  if (name === 'settings') { renderMeta(); renderTariffs(); initSettings(); }
}

function bindEvents() {
  $$('.nav-btn').forEach(b =>
    b.addEventListener('click', () => switchView(b.dataset.view)));
  $('#btn-refresh-exports')?.addEventListener('click', renderOrders);
  $('#btn-orders-refresh-price')?.addEventListener('click', updatePrice);
  $('#btn-shutdown')?.addEventListener('click', async () => {
    if (!confirm('Полностью завершить приложение? Фоновый сервер будет остановлен.')) return;
    try { await API.post('/api/shutdown'); } catch (e) { /* сервер гаснет — это нормально */ }
    toast('Приложение завершено. Это окно можно закрыть.', 'ok');
    window.close();
  });

  $('#btn-calc').addEventListener('click', calculate);
  $('#btn-reset').addEventListener('click', resetToDemo);
  $('#btn-clear-order').addEventListener('click', () => {
    state.order = []; saveOrder(); renderOrder();
  });
  $('#btn-docx').addEventListener('click', () => exportDocx(null));

  $$('#seg-mode .seg-btn').forEach(b =>
    b.addEventListener('click', () => setMode(b.dataset.mode)));

  const search = $('#inp-search');
  search.addEventListener('input', () => renderSearchResults(productSearch(search.value)));
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $('#search-results').classList.add('hidden');
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.product-search')) $('#search-results').classList.add('hidden');
  });

  $('#products-search').addEventListener('input', (e) =>
    renderProducts(e.target.value));

  $('#btn-add-product').addEventListener('click', () =>
    $('#modal-product').classList.remove('hidden'));
  $('#np-save').addEventListener('click', async () => {
    const item = {
      name: $('#np-name').value, category: $('#np-category').value,
      unit: $('#np-unit').value, price: $('#np-price').value,
      mass_kg: $('#np-mass').value, volume_m3: $('#np-volume').value,
      cargo_type: $('#np-cargo').value,
    };
    const res = await API.post('/api/add-product', item);
    if (res.ok) {
      toast(res.message, 'ok');
      $('#modal-product').classList.add('hidden');
      ['np-name', 'np-category', 'np-unit', 'np-price', 'np-mass', 'np-volume']
        .forEach(i => { $('#' + i).value = ''; });
      await bootstrap(true);
      switchView('products');
    } else toast(res.message, 'err');
  });

  $('#btn-reload').addEventListener('click', async () => {
    const btn = $('#btn-reload'); btn.disabled = true;
    try {
      const res = await API.post('/api/reload');
      if (res.ok) {
        markStaleMaybe();
        await bootstrap(true);
        toast(res.message, 'ok');
      } else toast(res.message, 'err');
    } finally { btn.disabled = false; }
  });

  $('#btn-yd-download').addEventListener('click', async () => {
    const btn = $('#btn-yd-download'); btn.disabled = true; btn.textContent = 'Обновляем…';
    try {
      const res = await API.post('/api/download-price', { url: $('#yd-url').value });
      toast(res.message, res.ok ? 'ok' : 'err');
      if (res.ok) { await bootstrap(true); updateStaleBadge(); renderArchive(); }
    } finally {
      btn.disabled = false; btn.textContent = '🔄 Обновить';
    }
  });

  // закрытие модалок
  $$('.modal [data-close]').forEach(b =>
    b.addEventListener('click', () => b.closest('.modal').classList.add('hidden')));
  $$('.modal').forEach(m => m.addEventListener('click', (e) => {
    if (e.target === m) m.classList.add('hidden');
  }));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $$('.modal').forEach(m => m.classList.add('hidden'));
  });
}

/* ------------------------------------------------ старт */
async function bootstrap(silent = false) {
  const res = await API.get('/api/state');
  if (!res.ok) {
    $('#data-status').textContent = 'Ошибка данных: ' + res.error;
    $('#data-status').className = 'data-status';
    toast(res.error, 'err', 'Проверьте data/прайс.xlsx в Настройках');
    return;
  }
  state.data = res;
  resolveOrder();          // строки заказа — всегда по актуальному прайсу
  fillSelects();
  renderFleet();
  renderTariffs();
  renderProducts($('#products-search')?.value || '');
  renderMeta();
  renderOrder(); renderResultPreview();   // таблица сразу показывает свежие цены
  $('#yd-url').value = state.data.settings?.yandex_disk_url || '';
  $('#yd-url-orders').value = state.data.settings?.yandex_disk_url || '';
  if (!silent) {
    if (!state.order.length) {
      const p = res.products.find(x => x.active && x.name.startsWith('Песок карьерный'));
      if (p) orderAdd(p, 10);              // предзаполненный демо-заказ
    }
    renderHistory();
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  loadOrder();
  bindEvents();
  await bootstrap(false);
  renderOrder();
  switchView('calc');
});
