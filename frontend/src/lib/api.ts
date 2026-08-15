export type Json = Record<string, any> | any[];

const demoSummary = {
  foods: 0,
  participants: 0,
  experiments: 0,
  recalls: 0,
  component_values: 0,
  reported_components: 0,
  audit_events: 0,
  imports: 0,
  platform_mode: 'offline_preview',
  legacy_compatibility: 'unavailable_offline',
  validated_research: 'unavailable_offline',
  schema_version: 4,
  canonical_nutrients: 0,
  external_foods: 0,
  food_matches: 0,
  recipes: 0,
  calculation_runs: 0,
  usage: {
    plan_name: 'Unavailable offline',
    import_rows: { used: 0, limit: 0, remaining: 0 },
    calculation_runs: { used: 0, limit: 0, remaining: 0 }
  }
};

let selectedProject = 'proj_legacy_phd_research';
let selectedActor = 'local-researcher';
let csrfToken: string | null = null;

export function setAuthSession(value: any) {
  csrfToken = value?.csrf_token || null;
  if (value?.actor) selectedActor = value.actor;
}

export function setPlatformContext(projectUid: string, actor = selectedActor) {
  selectedProject = projectUid || 'proj_legacy_phd_research';
  selectedActor = actor || 'local-researcher';
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('dietary-recall-project', selectedProject);
    localStorage.setItem('dietary-recall-actor', selectedActor);
  }
}

export function restorePlatformContext() {
  if (typeof localStorage !== 'undefined') {
    selectedProject = localStorage.getItem('dietary-recall-project') || selectedProject;
    selectedActor = localStorage.getItem('dietary-recall-actor') || selectedActor;
  }
  return { projectUid: selectedProject, actor: selectedActor };
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const method = String(options.method || 'GET').toUpperCase();
  const response = await fetch(path, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-Research-Actor': selectedActor,
      'X-Project-UID': selectedProject,
      ...(method !== 'GET' && csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      ...(options.headers || {})
    }
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.error || message;
    } catch {}
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export async function authStatus() {
  try {
    const status = await api<any>('/api/auth/status');
    setAuthSession(status);
    return status;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return { authenticated: false, development_mode: false };
    throw error;
  }
}

export async function login(email: string, password: string) {
  const result = await api<any>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
  setAuthSession(result);
  return result;
}

export async function logout() {
  const result = await api<any>('/api/auth/logout', { method: 'POST', body: '{}' });
  setAuthSession(null);
  return result;
}

export const get = <T = any>(path: string) => api<T>(path);
export const post = <T = any>(path: string, body: any) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const put = <T = any>(path: string, body: any) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) });

export async function summaryWithFallback() {
  try {
    return { data: await get<any>('/api/summary'), live: true };
  } catch {
    return { data: demoSummary, live: false };
  }
}

export async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}
