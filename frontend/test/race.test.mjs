import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';

// DOM-окружение для React должно быть готово до загрузки бандла компонента.
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/',
  pretendToBeVisual: true,
});
Object.defineProperty(globalThis, 'window', { value: dom.window, configurable: true, writable: true });
Object.defineProperty(globalThis, 'document', { value: dom.window.document, configurable: true, writable: true });
try {
  Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true, writable: true });
} catch (error) {
  // Node уже предоставляет navigator — React обходит это место без него
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { TicketPanel, React, act, createRoot } = await import('./.build/race.bundle.mjs');

const sources = [
  { id: 1, name: 'Телефон' },
  { id: 2, name: 'Личный кабинет' },
  { id: 3, name: 'Сотрудник' },
];
const employee = {
  id: 2,
  name: 'Илья Учебный',
  role: 'editor',
  permissions: ['FUNC_TICKETS_VIEW', 'FUNC_TICKETS_SOURCE_EDIT'],
};
const changedTickets = [];

let pendingCalls = [];

function installFetchStub() {
  pendingCalls = [];
  globalThis.fetch = (url, options = {}) =>
    new Promise((resolve, reject) => {
      pendingCalls.push({
        url: String(url),
        options,
        resolve(status, body) {
          resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
        },
        fail: reject,
      });
    });
}

function findCall(pattern, method = 'GET') {
  const call = pendingCalls.find(item => (item.options.method || 'GET') === method && pattern.test(item.url));
  assert.ok(call, `ожидался ${method} ${pattern} среди: ${pendingCalls.map(item => item.url).join(', ') || 'нет запросов'}`);
  pendingCalls.splice(pendingCalls.indexOf(call), 1);
  return call;
}

function makeTicket(id, title, sourceId) {
  return {
    id,
    title,
    description: 'Учебная заявка',
    status: 'incoming',
    source: sourceId === null ? null : sources.find(source => source.id === sourceId),
    updated_at: '2026-10-07T10:00:00+03:00',
  };
}

const container = document.createElement('div');
document.body.appendChild(container);
const root = createRoot(container);

async function renderPanel(selectedId) {
  await act(async () => {
    root.render(React.createElement(TicketPanel, {
      employee,
      sources,
      selectedId,
      onChanged: ticket => changedTickets.push(ticket),
    }));
  });
}

test('быстрое переключение: устаревший ответ не подменяет карточку', async () => {
  installFetchStub();
  await renderPanel(101); // ответ по ДОГОВОРУ задерживается дольше всех
  await renderPanel(102); // пользователь успел переключиться

  const slow = findCall(/\/tickets\/101$/);
  const fast = findCall(/\/tickets\/102$/);

  // быстрый ответ 102 приходит первым
  await act(async () => {
    fast.resolve(200, { ticket: makeTicket(102, 'Настроить роутер', 2), history: [] });
  });
  assert.match(container.textContent, /Настроить роутер/, 'карточка 102 должна отобразиться');

  // затем приходит опоздавший ответ 101 — он не должен подменить текущую заявку
  await act(async () => {
    slow.resolve(200, { ticket: makeTicket(101, 'Не работает интернет', 1), history: [] });
  });
  assert.match(container.textContent, /Настроить роутер/, 'текущая заявка должна остаться 102');
  assert.doesNotMatch(container.textContent, /Не работает интернет/, 'ответ по предыдущей заявке не должен отображаться');
});

test('ответ сохранения предыдущей заявки не подменяет текущую карточку', async () => {
  installFetchStub();
  changedTickets.length = 0;
  await renderPanel(105);
  await act(async () => {
    findCall(/\/tickets\/105$/).resolve(200, {
      ticket: makeTicket(105, 'Перенести точку подключения', 1),
      history: [],
    });
  });
  assert.match(container.textContent, /Перенести точку подключения/);

  const select = container.querySelector('#source');
  assert.ok(select, 'должен существовать контрол выбора источника');
  await act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLSelectElement.prototype, 'value').set.call(select, '3');
    select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  });

  const saveButton = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Сохранить'));
  assert.ok(saveButton, 'должна существовать кнопка Сохранить');
  await act(async () => {
    saveButton.click();
  });
  const saveCall = findCall(/\/tickets\/105\/source$/, 'POST');
  assert.match(saveCall.options.body || '', /"source_id":\s*3/, 'в запросе должен быть выбранный источник');

  // повторный клик во время запроса не создаёт второй POST
  await act(async () => {
    saveButton.click();
  });
  assert.equal(
    pendingCalls.filter(call => call.options.method === 'POST').length,
    0,
    'повторное сохранение должно быть заблокировано на время запроса',
  );

  // пользователь переключился на другую заявку, пока шло сохранение
  await renderPanel(101);
  await act(async () => {
    findCall(/\/tickets\/101$/).resolve(200, {
      ticket: makeTicket(101, 'Не работает интернет', 1),
      history: [],
    });
  });
  assert.match(container.textContent, /Не работает интернет/);

  // ответ сохранения заявки 105 приходит уже после переключения
  await act(async () => {
    saveCall.resolve(200, {
      ticket: makeTicket(105, 'Перенести точку подключения', 3),
      history: [{
        id: 1,
        employee_id: 2,
        employee_name: 'Илья Учебный',
        old_source_id: 1,
        new_source_id: 3,
        created_at: '2026-10-07T10:00:00+03:00',
      }],
    });
  });

  assert.match(container.textContent, /Не работает интернет/, 'карточка должна остаться на текущей заявке');
  assert.doesNotMatch(container.textContent, /Перенести точку подключения/, 'сохранённая заявка не должна подменить карточку');
  assert.doesNotMatch(container.textContent, /Источник сохранён/, 'сообщение об успехе относится к другой заявке');
  assert.ok(
    changedTickets.some(ticket => ticket.id === 105),
    'список заявок всё равно должен обновиться сохранённой заявкой',
  );
});
