export interface TranscriptSegment {
  id: string
  speaker: string
  text: string
  start_time: number
  end_time: number
  confidence: number
}

export interface ActionItem {
  id: string
  text: string
  assignee?: string
  deadline?: string
  priority: string
  confidence: number
}

export interface KeyDecision {
  id: string
  decision: string
  rationale?: string
  impact: string
  confidence: number
}

export interface AnalysisResults {
  transcript: TranscriptSegment[]
  summary: string
  action_items: ActionItem[]
  key_decisions: KeyDecision[]
  processing_time: number
  gcs_path?: string
  audio_gcs_path?: string
  filename?: string
}

export interface FileUploadProps {
  onFileAnalyzed: (results: AnalysisResults) => void
  isProcessing: boolean
  setIsProcessing: (processing: boolean) => void
}
