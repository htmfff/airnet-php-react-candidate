let csrf = '';

export function setCsrf(value) {
  csrf = value;
}

export async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...options.headers },
  });
  let data = null;
  try {
    data = await response.json();
  } catch (parseError) {
    const error = new Error(`Сервер вернул не-JSON ответ (HTTP ${response.status})`);
    error.status = response.status;
    throw error;
  }
  if (!response.ok) {
    const error = new Error((data && data.message) || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}
