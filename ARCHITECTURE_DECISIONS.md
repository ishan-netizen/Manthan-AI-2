# Architecture Decisions - Next Iteration

## Core Changes
1. **Move from synchronous POST /analyze/stream to Eventarc-based async processing**
2. **Direct GCS upload from browser (resumable uploads)**
3. **Skip Cloud Run downloading the file** - Deepgram accepts signed GCS URL directly
4. **ffmpeg streaming** (no temp files in RAM)
5. **Async processing pipeline**: GCS finalized → Eventarc → Cloud Run → ffmpeg → Deepgram → Gemini → Firestore
6. **Cost**: ~$0.42 per 1GB video (same-region, Nova-3 Deepgram)

## Production Decisions

| Concern | Decision |
|---|---|
| Upload | Browser → GCS via resumable signed URLs (no Cloud Run size limit) |
| Trigger | GCS object.finalized → Eventarc → Cloud Run job/stub |
| Audio extraction | ffmpeg pipe (GCS → stdin → stdout → GCS), no /tmp RAM consumption |
| Transcription | Deepgram Nova-3 pre-recorded API via signed GCS URL (Cloud Run never touches bytes, Deepgram fetches directly) |
| Analysis | Gemini 2.5 Flash (summary, decisions, actions — English output) |
| Results | Written to Firestore → frontend polls/SSE for completion |
| Memory | Stays at 2GB (no file in RAM), scales to 32GB if needed |
| Timeout | No 60-min limit — async Eventarc model |
| Cost/60-min video | ~$0.27 Deepgram + ~$0.01 Gemini + ~$0.005 GCS/Cloud Run ≈ $0.29 total |
| Security | V4 signed URLs, 15-min expiry, locked Content-Type, IAM service account signing |

## What Stays the Same
- Deepgram for STT + diarization
- Gemini for analysis
- MongoDB for user data
- Cloud Run for compute
- Same frontend

**Only the upload/processing pipeline changes.**
