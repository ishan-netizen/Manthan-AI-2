import { API_BASE } from './client'
import type { AnalysisResults } from '@/types/analysis'

interface BackendAnalysisResponse {
  transcript: AnalysisResults['transcript']
  summary: string
  action_items: AnalysisResults['action_items']
  key_decisions: AnalysisResults['key_decisions']
  processing_time: number
  gcs_path?: string
  audio_gcs_path?: string
}

function transformResponse(data: BackendAnalysisResponse): AnalysisResults {
  return {
    transcript: data.transcript || [],
    summary: data.summary || '',
    action_items: data.action_items || [],
    key_decisions: data.key_decisions || [],
    processing_time: data.processing_time || 0,
    gcs_path: data.gcs_path,
    audio_gcs_path: data.audio_gcs_path,
  }
}

export async function analyzeFile(
  file: File,
  signal?: AbortSignal,
  onProgress?: (percent: number, step: string, message: string) => void,
): Promise<AnalysisResults> {
  // Try GCS direct upload first
  try {
    return await analyzeViaGCS(file, signal, onProgress)
  } catch (e) {
    // Fallback to direct upload for small files
    return analyzeDirect(file, signal, onProgress)
  }
}

async function analyzeViaGCS(
  file: File,
  signal?: AbortSignal,
  onProgress?: (percent: number, step: string, message: string) => void,
): Promise<AnalysisResults> {
  onProgress?.(0, 'uploading', 'Preparing upload...')

  const initRes = await fetch(`${API_BASE}/upload/init`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ filename: file.name, content_type: file.type || 'application/octet-stream' }).toString(),
    signal,
  })
  if (!initRes.ok) throw new Error('GCS init failed')

  const { upload_url, gcs_path } = await initRes.json()

  onProgress?.(5, 'uploading', 'Uploading to cloud storage...')

  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', upload_url)
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
    if (signal) signal.addEventListener('abort', () => xhr.abort())
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        onProgress?.(5 + Math.round((e.loaded / e.total) * 10), 'uploading', `Uploading ${Math.round(e.loaded / 1024 / 1024)}MB...`)
      }
    }
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Upload failed: ${xhr.status}`)))
    xhr.onerror = () => reject(new Error('Upload failed'))
    xhr.send(file)
  })

  const formData = new FormData()
  formData.append('gcs_path', gcs_path)
  formData.append('original_filename', file.name)
  return streamAnalysis(`${API_BASE}/analyze/stream`, formData, signal, onProgress)
}

async function analyzeDirect(
  file: File,
  signal?: AbortSignal,
  onProgress?: (percent: number, step: string, message: string) => void,
): Promise<AnalysisResults> {
  const formData = new FormData()
  formData.append('file', file)
  return streamAnalysis(`${API_BASE}/analyze/stream`, formData, signal, onProgress)
}

async function streamAnalysis(
  url: string,
  formData: FormData,
  signal?: AbortSignal,
  onProgress?: (percent: number, step: string, message: string) => void,
): Promise<AnalysisResults> {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    body: formData,
    signal,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || err.message || `Request failed (${res.status})`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const event = JSON.parse(line)
        if (event.status === 'progress') {
          onProgress?.(event.percent, event.step, event.message)
        } else if (event.status === 'complete') {
          return transformResponse(event)
        } else if (event.status === 'error') {
          throw new Error(event.message)
        }
      } catch (e) {
        if (e instanceof SyntaxError) continue
        throw e
      }
    }
  }

  throw new Error('Stream ended without completion')
}

export async function getSupportedFormats() {
  const { apiFetch } = await import('./client')
  return apiFetch('/formats')
}

export async function getServiceStatus() {
  const { apiFetch } = await import('./client')
  return apiFetch('/status')
}

export async function translateText(text: string, targetLang: 'hi' | 'en'): Promise<string> {
  const { apiFetch } = await import('./client')
  const data = await apiFetch<{ translated: string }>('/translate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, target_lang: targetLang }),
  })
  return data.translated
}
