const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
localStorage.removeItem('hireproof_token')

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API}${path}`, { ...init, headers })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = body.detail
    const message = typeof detail === 'string' ? detail : detail?.message || detail?.code || 'Request failed. Please try again.'
    throw new Error(message)
  }
  return body as T
}

export function uploadWithProgress<T = any>(path: string, body: FormData, onProgress: (percent: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API}${path}`)
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100)) }
    xhr.onerror = () => reject(new Error('Upload failed because the API could not be reached.'))
    xhr.onload = () => {
      const result = JSON.parse(xhr.responseText || '{}')
      if (xhr.status < 200 || xhr.status >= 300) {
        const detail = result.detail
        reject(new Error(typeof detail === 'string' ? detail : detail?.message || detail?.code || 'Upload failed.'))
      } else resolve(result as T)
    }
    xhr.send(body)
  })
}
