interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_APP_NAME: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

const API_BASE_URL = import.meta.env.VITE_API_URL || 
  (import.meta.env.MODE === 'production' 
    ? 'https://manthan-ai-69lq.onrender.com/api'
    : 'http://localhost:8000/api');

export interface AnalysisResults {
  transcript: Array<{
    id: string;
    speaker: string;
    text: string;
    start_time: number;
    end_time: number;
    confidence: number;
  }>;
  summary: string;
  action_items: Array<{
    id: string;
    text: string;
    assignee?: string;
    deadline?: string;
    priority: string;
    confidence: number;
  }>;
  key_decisions: Array<{
    id: string;
    decision: string;
    rationale?: string;
    impact: string;
    confidence: number;
  }>;
  processing_time: number;
}

interface BackendAnalysisResponse {
  transcript: Array<{
    id: string;
    speaker: string;
    text: string;
    start_time: number;
    end_time: number;
    confidence: number;
  }>;
  summary: string;
  action_items: Array<{
    id: string;
    text: string;
    assignee?: string;
    deadline?: string;
    priority: string;
    confidence: number;
  }>;
  key_decisions: Array<{
    id: string;
    decision: string;
    rationale?: string;
    impact: string;
    confidence: number;
  }>;
  processing_time: number;
}

function transformBackendResponse(backendData: BackendAnalysisResponse): AnalysisResults {
  return {
    transcript: backendData.transcript || [],
    summary: backendData.summary || '',
    action_items: backendData.action_items || [],
    key_decisions: backendData.key_decisions || [],
    processing_time: backendData.processing_time || 0
  };
}

export class ApiClient {
  private baseURL: string;

  constructor(baseURL: string = API_BASE_URL) {
    this.baseURL = baseURL;
  }

  async analyzeFile(file: File): Promise<AnalysisResults> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${this.baseURL}/analyze`, {
      method: 'POST',
      credentials: 'include',
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
    }

    const backendData: BackendAnalysisResponse = await response.json();
    return transformBackendResponse(backendData);
  }

  async getSupportedFormats(): Promise<any> {
    const response = await fetch(`${this.baseURL}/formats`, {
      method: 'GET',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    });
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return response.json();
  }

  async healthCheck(): Promise<any> {
    const response = await fetch(`${this.baseURL.replace('/api', '')}/health`, {
      method: 'GET',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    });
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    return response.json();
  }
}

export const apiClient = new ApiClient();