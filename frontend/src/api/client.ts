const API_BASE_URL = import.meta.env.VITE_API_BASE_URL as string
const TOKEN_STORAGE_KEY = 'gss_token'

let inMemoryToken: string | null = localStorage.getItem(TOKEN_STORAGE_KEY)

export function getToken(): string | null {
  return inMemoryToken
}

export function setToken(token: string | null): void {
  inMemoryToken = token
  if (token) {
    localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } else {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })

  if (response.status === 204) {
    return undefined as T
  }

  const isJson = response.headers.get('content-type')?.includes('application/json')
  const body = isJson ? await response.json() : await response.text()

  if (!response.ok) {
    const message = isJson && body?.detail ? String(body.detail) : `Request failed (${response.status})`
    throw new ApiError(response.status, message)
  }

  return body as T
}

export async function apiFetchMultipart<T>(path: string, formData: FormData): Promise<T> {
  const headers = new Headers()
  headers.set('Accept', 'application/json')
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  const response = await fetch(`${API_BASE_URL}${path}`, { method: 'POST', headers, body: formData })

  const isJson = response.headers.get('content-type')?.includes('application/json')
  const body = isJson ? await response.json() : await response.text()

  if (!response.ok) {
    const message = isJson && body?.detail ? String(body.detail) : `Request failed (${response.status})`
    throw new ApiError(response.status, message)
  }
  return body as T
}

export async function apiFetchBlob(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const headers = new Headers()
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  const response = await fetch(`${API_BASE_URL}${path}`, { headers })
  if (!response.ok) {
    throw new ApiError(response.status, `Request failed (${response.status})`)
  }
  const disposition = response.headers.get('content-disposition')
  const match = disposition?.match(/filename=([^;]+)/)
  return { blob: await response.blob(), filename: match ? match[1].trim() : null }
}
