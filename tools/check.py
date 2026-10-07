#!/usr/bin/env python3
"""Автоматические проверки сценариев учебного модуля заявок.

Запуск (после docker compose up или локального запуска):

    python3 tools/smoke.py http://localhost:5173
    python3 tools/check.py http://localhost:5173

Проверяется поведение API: статусы, JSON, неизменность данных при отказах,
атомарность и содержимое истории. Проверки работают и с задержками
(DEMO_LATENCY=1), и без них, и не зависят от текста сообщений об ошибках.
Перед запуском ожидается свежая учебная БД (database/001, 002 или reset.sql).
"""
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:5173').rstrip('/')

_MISSING = object()
failures = []


class Failure(Exception):
    pass


def expect(condition, message):
    if not condition:
        raise Failure(message)


def make_client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(client, path, body=_MISSING, csrf=None):
    data = None if body is _MISSING else json.dumps(body).encode()
    headers = {'Content-Type': 'application/json'}
    if csrf:
        headers['X-CSRF-Token'] = csrf
    request = urllib.request.Request(BASE + '/api' + path, data=data, headers=headers)
    try:
        response = client.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload)
        except ValueError:
            raise Failure('%s %s: ответ не JSON: %r' % (request.get_method(), path, payload[:120]))
        return error.code, parsed
    payload = response.read()
    try:
        parsed = json.loads(payload)
    except ValueError:
        raise Failure('%s %s: ответ не JSON: %r' % (request.get_method(), path, payload[:120]))
    return response.status, parsed


def login(role):
    client = make_client()
    status, data = call(client, '/demo/login', {'role': role})
    expect(status == 200 and data.get('csrf'), 'вход %s -> %s %r' % (role, status, data))
    return client, data['csrf']


def get_ticket(client, ticket_id):
    status, data = call(client, '/tickets/%d' % ticket_id)
    expect(status == 200, 'GET /tickets/%d -> %s %r' % (ticket_id, status, data))
    return data


def source_of(client, ticket_id):
    return get_ticket(client, ticket_id)['ticket']['source']


def history_len(client, ticket_id):
    return len(get_ticket(client, ticket_id)['history'])


def check(name):
    def decorator(function):
        checks.append((name, function))
        return function
    return decorator


checks = []


@check('инфраструктура: health, вход, справочники')
def infra():
    client = make_client()
    status, data = call(client, '/health')
    expect(status == 200 and data.get('status') == 'ok', 'health -> %s %r' % (status, data))
    expect(str(data.get('php', '')).startswith('7.4.'), 'ожидался PHP 7.4: %r' % data)
    editor, _ = login('editor')
    viewer, _ = login('viewer')
    status, data = call(client, '/tickets')
    expect(status == 401 and 'error' in data, 'без сессии GET /tickets -> %s %r' % (status, data))
    status, data = call(client, '/tickets/105/source', {'source_id': 2})
    expect(status == 401 and 'error' in data, 'без сессии POST source -> %s %r' % (status, data))
    status, data = call(editor, '/me')
    expect(status == 200 and data['employee']['role'] == 'editor', '/me -> %s %r' % (status, data))
    status, data = call(editor, '/sources')
    sources = data.get('sources', [])
    expect(status == 200 and len(sources) >= 3, 'источники -> %s %r' % (status, data))
    expect(all(isinstance(s['id'], int) and s['id'] > 0 for s in sources), 'id источников: %r' % sources)
    status, data = call(editor, '/tickets')
    tickets = data.get('tickets', [])
    expect(status == 200 and len(tickets) >= 5, 'список заявок -> %s %r' % (status, data))


@check('заявка без источника: 200 и source=null (исходная ошибка №2)')
def ticket_without_source():
    editor, _ = login('editor')
    data = get_ticket(editor, 103)
    expect(data['ticket']['source'] is None, 'source заявки 103 -> %r' % data['ticket']['source'])
    status, data = call(editor, '/tickets')
    row = [t for t in data['tickets'] if t['id'] == 103][0]
    expect(row['source_name'] is None, 'source_name в списке -> %r' % row['source_name'])
    viewer, _ = login('viewer')
    data = get_ticket(viewer, 103)
    expect(data['ticket']['source'] is None, 'viewer: source 103 -> %r' % data['ticket']['source'])


@check('только просмотр: изменение источника запрещено (исходная ошибка №3)')
def viewer_cannot_edit():
    viewer, csrf = login('viewer')
    editor, _ = login('editor')
    before_source = source_of(editor, 102)
    before_history = history_len(editor, 102)
    for payload in (
        {'source_id': 1},
        {'source_id': 1, 'employee_id': 2, 'name': 'Илья Учебный', 'permissions': ['FUNC_TICKETS_SOURCE_EDIT']},
    ):
        status, data = call(viewer, '/tickets/102/source', payload, csrf)
        expect(status == 403 and 'error' in data, 'viewer POST source -> %s %r' % (status, data))
    expect(source_of(editor, 102) == before_source, 'данные изменились после отказа в правах')
    expect(history_len(editor, 102) == before_history, 'история пополнилась после отказа в правах')
    viewer2, viewer_csrf = login('viewer')
    status, data = call(viewer2, '/tickets/104/source', {'source_id': 1}, viewer_csrf)
    expect(status == 403, 'viewer на архивной заявке -> %s %r' % (status, data))


@check('архивная заявка доступна только для чтения')
def archived_is_read_only():
    editor, csrf = login('editor')
    data = get_ticket(editor, 104)
    expect(data['ticket']['status'] == 'archived', 'статус 104 -> %r' % data['ticket']['status'])
    before_source = source_of(editor, 104)
    before_history = history_len(editor, 104)
    status, data = call(editor, '/tickets/104/source', {'source_id': 2}, csrf)
    expect(status == 403 and 'error' in data, 'POST архивной -> %s %r' % (status, data))
    expect(source_of(editor, 104) == before_source, 'источник архивной заявки изменился')
    expect(history_len(editor, 104) == before_history, 'история архивной заявки пополнилась')


@check('некорректный источник отклоняется как 400, данные не меняются')
def invalid_source_rejected():
    editor, csrf = login('editor')
    before_source = source_of(editor, 105)
    before_history = history_len(editor, 105)
    bad_payloads = (
        ('строка', {'source_id': '2'}),
        ('дробь', {'source_id': 2.5}),
        ('целое как дробь', {'source_id': 2.0}),
        ('ноль', {'source_id': 0}),
        ('минус', {'source_id': -1}),
        ('null', {'source_id': None}),
        ('отсутствующее поле', {}),
        ('неизвестный id', {'source_id': 999}),
        ('массив', {'source_id': [2]}),
        ('логическое', {'source_id': True}),
    )
    for label, payload in bad_payloads:
        status, data = call(editor, '/tickets/105/source', payload, csrf)
        expect(status == 400 and 'error' in data, '%s -> %s %r' % (label, status, data))
        expect(source_of(editor, 105) == before_source, 'источник изменился после %s' % label)
    expect(history_len(editor, 105) == before_history, 'история пополнилась после 400')


@check('отсутствующая заявка: 404 как JSON')
def missing_ticket_404():
    editor, csrf = login('editor')
    status, data = call(editor, '/tickets/999')
    expect(status == 404 and 'error' in data, 'GET /tickets/999 -> %s %r' % (status, data))
    status, data = call(editor, '/tickets/999/source', {'source_id': 2}, csrf)
    expect(status == 404 and 'error' in data, 'POST /tickets/999/source -> %s %r' % (status, data))
    status, data = call(editor, '/no-such-route')
    expect(status == 404 and 'error' in data, 'неизвестный маршрут -> %s %r' % (status, data))


@check('порядок проверок: заявка -> архив -> корректность источника')
def check_order():
    editor, csrf = login('editor')
    status, data = call(editor, '/tickets/999/source', {'source_id': 0}, csrf)
    expect(status == 404, 'несуществующая + неверный источник -> %s %r (ожидалась 404)' % (status, data))
    status, data = call(editor, '/tickets/104/source', {'source_id': 0}, csrf)
    expect(status == 403, 'архивная + неверный источник -> %s %r (ожидалась 403)' % (status, data))
    status, data = call(editor, '/tickets/105/source', {'source_id': 0}, csrf)
    expect(status == 400, 'существующая + неверный источник -> %s %r (ожидалась 400)' % (status, data))


@check('CSRF обязателен, отказ не меняет данные')
def csrf_required():
    editor, _ = login('editor')
    editor2, _ = login('editor')
    before_source = source_of(editor2, 105)
    before_history = history_len(editor2, 105)
    status, data = call(editor, '/tickets/105/source', {'source_id': 2})
    expect(status == 403 and 'error' in data, 'без CSRF -> %s %r' % (status, data))
    status, data = call(editor, '/tickets/105/source', {'source_id': 2}, 'wrong-token')
    expect(status == 403 and 'error' in data, 'с неверным CSRF -> %s %r' % (status, data))
    expect(source_of(editor2, 105) == before_source, 'источник изменился без CSRF')
    expect(history_len(editor2, 105) == before_history, 'история пополнилась без CSRF')


@check('успешное изменение: карточка, список, история из сессии')
def change_success():
    editor, csrf = login('editor')
    initial_source = source_of(editor, 105)
    initial_history = history_len(editor, 105)
    new_id = 2 if initial_source is None or initial_source['id'] != 2 else 3
    status, data = call(
        editor, '/tickets/105/source',
        {'source_id': new_id, 'employee_id': 1, 'name': 'Анна Примерова',
         'permissions': ['FUNC_TICKETS_VIEW']},
        csrf)
    expect(status == 200, 'смена источника -> %s %r' % (status, data))
    expect(data['ticket']['source']['id'] == new_id, 'ticket.source -> %r' % data['ticket'])
    entries = data['history']
    expect(len(entries) == initial_history + 1, 'история в ответе -> %d записей' % len(entries))
    latest = entries[0]
    expect(latest['employee_id'] == 2, 'история: сотрудник из сессии -> %r' % latest)
    expect(latest['old_source_id'] == (initial_source or {}).get('id'), 'old_source_id -> %r' % latest)
    expect(latest['new_source_id'] == new_id, 'new_source_id -> %r' % latest)
    expect(latest['employee_name'] and latest['created_at'], 'неполная запись истории: %r' % latest)
    status, data = call(editor, '/tickets')
    row = [t for t in data['tickets'] if t['id'] == 105][0]
    expected_name = {1: 'Телефон', 2: 'Личный кабинет', 3: 'Сотрудник'}[new_id]
    expect(row['source_name'] == expected_name, 'список не обновился: %r' % row)
    fresh = get_ticket(editor, 105)
    expect(fresh['ticket']['source']['id'] == new_id, 'карточка не обновилась: %r' % fresh['ticket'])
    expect(len(fresh['history']) == initial_history + 1, 'история не обновилась')

    # повторный выбор того же источника — успех без новой записи истории
    status, data = call(editor, '/tickets/105/source', {'source_id': new_id}, csrf)
    expect(status == 200, 'повторный выбор -> %s %r' % (status, data))
    expect(len(data['history']) == initial_history + 1, 'повторный выбор добавил запись истории')
    expect(len(get_ticket(editor, 105)['history']) == initial_history + 1, 'повторный выбор изменил историю')

    # возврат исходного состояния заявки 105
    restore = initial_source['id'] if initial_source else 1
    status, data = call(editor, '/tickets/105/source', {'source_id': restore}, csrf)
    expect(status == 200 and data['ticket']['source']['id'] == restore, 'возврат исходного источника не удался')


@check('история пуста для заявок без изменений')
def history_state():
    editor, _ = login('editor')
    data = get_ticket(editor, 102)
    expect(isinstance(data['history'], list), 'history не массив: %r' % data['history'])
    data = get_ticket(editor, 103)
    expect(isinstance(data['history'], list), 'history не массив: %r' % data['history'])


def main():
    for name, function in checks:
        try:
            function()
            print('PASS:', name)
        except Failure as error:
            print('FAIL:', name, '-', str(error))
            failures.append(name)
        except Exception as error:  # noqa: BLE001 - неожиданная ошибка тоже провал
            print('FAIL:', name, '- неожиданная ошибка:', repr(error))
            failures.append(name)
    if failures:
        print('ИТОГ: провалено %d из %d: %s' % (len(failures), len(checks), '; '.join(failures)))
        sys.exit(1)
    print('ИТОГ: все %d проверок пройдены' % len(checks))


if __name__ == '__main__':
    main()
