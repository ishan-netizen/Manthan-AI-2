"""
Complete production analysis endpoint router using API services.
"""

import json
import os
import tempfile
import traceback
import uuid
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, File, Form, Request, UploadFile, HTTPException, BackgroundTasks, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.models.schemas import AnalysisResponse
from app.services.audio_processor import ProductionAudioProcessor
from app.services.nlp_analyzer import ProductionNLPAnalyzer
from app.utils.file_handler import validate_audio_file, cleanup_temp_files
from app.utils.config import get_settings, Settings
from app.database import analyses_collection
from app.routers.auth import get_current_user
from app.services import gcs_handler

router = APIRouter()

logger = logging.getLogger(__name__)

# Initialize services
audio_processor = ProductionAudioProcessor()

try:
    from app.services.speech_diarizer import SpeechDiarizer
    diarizer = SpeechDiarizer()
    logger.info("Deepgram diarizer loaded — will use for speaker-accurate transcription")
except Exception as e:
    diarizer = None
    logger.warning(f"SpeechDiarizer not available ({e}) — falling back to Gemini transcription")


# --- History response models ---

class AnalysisSummary(BaseModel):
    """Lightweight summary returned in history list."""
    id: str
    filename: str
    created_at: str
    action_items_count: int = 0
    decisions_count: int = 0
    word_count: int = 0
    duration_seconds: float = 0
    processing_time: float = 0

    class Config:
        from_attributes = True


class AnalysisHistoryResponse(BaseModel):
    analyses: List[AnalysisSummary]
    total: int


class DeleteResponse(BaseModel):
    message: str


def _utc_isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _analysis_to_summary(doc: dict) -> AnalysisSummary:
    return AnalysisSummary(
        id=str(doc["_id"]),
        filename=doc.get("filename", "Unknown"),
        created_at=_utc_isoformat(doc.get("created_at", datetime.now(timezone.utc))),
        action_items_count=len(doc.get("action_items", [])),
        decisions_count=len(doc.get("key_decisions", [])),
        word_count=doc.get("word_count", 0),
        duration_seconds=doc.get("duration", 0),
        processing_time=doc.get("processing_time", 0),
    )


# --- Existing endpoints ---

def get_settings_dependency() -> Settings:
    """Dependency to get settings."""
    return get_settings()


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_meeting(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings_dependency),
    user: dict = Depends(get_current_user),
) -> AnalysisResponse:
    """
    Analyze uploaded meeting recording using OpenAI API services.
    
    This endpoint:
    1. Validates the uploaded audio/video file
    2. Processes audio for optimal transcription
    3. Transcribes using OpenAI Whisper API
    4. Analyzes content using GPT models
    5. Returns structured analysis results
    6. Saves results to the user's history
    
    Supported formats: MP3, WAV, MP4, M4A, OGG, FLAC (max 150MB, up to 120 minutes)
    """
    
    session_id = str(uuid.uuid4())
    temp_dir = None
    temp_filepath = None
    start_time = time.time()
    
    logger.info(f"[ANALYZE] New session {session_id} — file: {file.filename}, size: {getattr(file, 'size', 'unknown')} bytes, user: {user.get('email')}")
    
    try:
        # Validate API key first
        if not settings.validate_api_keys():
            raise HTTPException(
                status_code=500, 
                detail="OpenAI API key not configured or invalid"
            )
        
        # Validate file presence
        if not file.filename:
            raise HTTPException(
                status_code=400, 
                detail="No file provided"
            )
            
        # Validate file format and size
        if not validate_audio_file(file):
            supported_formats = ", ".join(settings.supported_formats_list)
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid file format. Supported: {supported_formats.upper()} (max {settings.MAX_FILE_SIZE / 1024 / 1024:.1f}MB)"
            )
        
        # Check file size if available
        if hasattr(file, 'size') and file.size:
            if file.size > settings.MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"File too large ({file.size / 1024 / 1024:.1f}MB). Maximum size: {settings.MAX_FILE_SIZE / 1024 / 1024:.1f}MB"
                )
        
        # Create temporary directory for this session
        temp_dir = tempfile.mkdtemp(prefix=f"session_{session_id}_")
        safe_filename = f"{session_id}_{file.filename}"
        temp_filepath = os.path.join(temp_dir, safe_filename)
        
        logger.info(f"[ANALYZE] Reading file from request stream...")
        t_read = time.time()
        content = await file.read()
        bytes_read = len(content)
        logger.info(f"[ANALYZE] Read complete: {bytes_read / 1024 / 1024:.1f} MB ({bytes_read} bytes) in {time.time()-t_read:.1f}s")

        with open(temp_filepath, "wb") as temp_file:
            temp_file.write(content)
        del content  # free memory immediately

        logger.info(f"[ANALYZE] File saved: {temp_filepath} ({os.path.getsize(temp_filepath) / 1024 / 1024:.1f} MB)")
        
        # Validate saved file
        if not audio_processor.validate_audio_file(temp_filepath):
            raise HTTPException(
                status_code=400,
                detail="Uploaded file appears to be corrupted or invalid"
            )
        
        # Get audio info for logging
        audio_info = audio_processor.get_audio_info(temp_filepath)
        logger.info(f"Audio info: {audio_info.get('duration', 0):.1f}s, {audio_info.get('sample_rate', 0)}Hz")
        
        # Check duration limits
        if audio_info.get('duration', 0) > settings.MAX_AUDIO_DURATION:
            raise HTTPException(
                status_code=400,
                detail=f"Audio too long ({audio_info['duration']:.1f}s). Maximum: {settings.MAX_AUDIO_DURATION}s"
            )
        
        # Analyze — use STT diarizer if available, else fall back to Gemini transcription
        if diarizer and diarizer.is_ready():
            logger.info(f"[ANALYZE] [{time.time()-start_time:.0f}s elapsed] Transcribing with Deepgram diarization...")
            t_stt = time.time()
            transcript_segments = await diarizer.transcribe(temp_filepath)
            logger.info(f"[ANALYZE] STT done in {time.time()-t_stt:.1f}s — {len(transcript_segments)} segments")

            logger.info(f"[ANALYZE] [{time.time()-start_time:.0f}s elapsed] Running Gemini analysis on transcript...")
            t_nlp = time.time()
            async with ProductionNLPAnalyzer() as nlp_analyzer:
                analysis_result = await nlp_analyzer.analyze_transcript_only(transcript_segments)
        else:
            processed_audio_path = await audio_processor.process_audio(temp_filepath, session_id)
            processed_size_mb = os.path.getsize(processed_audio_path) / 1024 / 1024
            logger.info(f"[ANALYZE] Pydub done in {time.time()-start_time:.1f}s | output: {processed_size_mb:.1f} MB")

            logger.info(f"[ANALYZE] [{time.time()-start_time:.0f}s elapsed] Starting Gemini analysis...")
            t_nlp = time.time()
            async with ProductionNLPAnalyzer() as nlp_analyzer:
                analysis_result = await nlp_analyzer.analyze_meeting(processed_audio_path)
        logger.info(f"[ANALYZE] [{time.time()-start_time:.0f}s elapsed] Analysis done in {time.time()-t_nlp:.1f}s")
        
        # Calculate total processing time
        processing_time = time.time() - start_time
        analysis_result["processing_time"] = round(processing_time, 2)

        # Detect demo fallback — real analysis failed, do NOT silently return fake data
        is_demo = analysis_result.pop("_is_demo_fallback", False)
        fallback_reason = analysis_result.pop("_fallback_reason", "")
        if is_demo:
            logger.error(
                f"[ANALYZE] ANALYSIS PIPELINE FAILED — demo fallback triggered after {processing_time:.1f}s | "
                f"reason: {fallback_reason}"
            )
            if temp_dir:
                background_tasks.add_task(cleanup_temp_files, temp_dir)
            raise HTTPException(
                status_code=500,
                detail=f"Analysis failed: {fallback_reason or 'Gemini API error'}. Check server logs for details.",
            )

        # Calculate word count
        transcript = analysis_result.get("transcript", [])
        word_count = sum(len(seg.get("text", "").split()) for seg in transcript if isinstance(seg, dict))

        # Build response
        response = AnalysisResponse(
            session_id=session_id,
            filename=file.filename,
            **analysis_result
        )
        
        # Save to user's history
        try:
            logger.info(f"[ANALYZE] Saving to MongoDB...")
            t_save = time.time()
            # Serialize Pydantic models to plain dicts for MongoDB
            transcript_data = [seg.dict() if hasattr(seg, 'dict') else seg for seg in response.transcript]
            action_items_data = [item.dict() if hasattr(item, 'dict') else item for item in response.action_items]
            decisions_data = [d.dict() if hasattr(d, 'dict') else d for d in response.key_decisions]

            await analyses_collection.insert_one({
                "user_id": user["_id"],
                "session_id": session_id,
                "filename": file.filename,
                "transcript": transcript_data,
                "summary": response.summary,
                "action_items": action_items_data,
                "key_decisions": decisions_data,
                "processing_time": processing_time,
                "duration": audio_info.get("duration", 0),
                "word_count": word_count,
                "created_at": datetime.now(timezone.utc),
            })
            logger.info(f"[ANALYZE] Saved to MongoDB in {time.time() - t_save:.1f}s — user: {user.get('email')}, session: {session_id}")
        except Exception as save_error:
            logger.error(f"Failed to save analysis to history: {save_error}")
            # Don't fail the request — the analysis still succeeded
        
        # Schedule cleanup
        background_tasks.add_task(cleanup_temp_files, temp_dir)
        
        logger.info(f"[ANALYZE] COMPLETE — session: {session_id}, total: {processing_time:.2f}s")
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions (they have proper error messages)
        if temp_dir:
            background_tasks.add_task(cleanup_temp_files, temp_dir)
        raise
        
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Cleanup on error
        if temp_dir:
            background_tasks.add_task(cleanup_temp_files, temp_dir)
        
        # Return generic error to user (don't expose internal details)
        raise HTTPException(
            status_code=500, 
            detail="An error occurred while processing your file. Please try again or contact support if the problem persists."
        )


# --- Chunked upload ---

@router.post("/upload/chunk")
async def upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    original_filename: str = Form(...),
    chunk: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Receive one chunk of a chunked file upload. Reassembled later during analysis."""
    chunk_dir = os.path.join(tempfile.gettempdir(), "manthan_chunks", upload_id)
    os.makedirs(chunk_dir, exist_ok=True)

    manifest_path = os.path.join(chunk_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        with open(manifest_path, "w") as f:
            json.dump({"filename": original_filename, "total_chunks": total_chunks}, f)

    chunk_path = os.path.join(chunk_dir, f"chunk_{chunk_index:05d}")
    with open(chunk_path, "wb") as f:
        f.write(await chunk.read())

    return {"status": "ok", "chunk": chunk_index}


# --- GCS signed URL upload ---

@router.post("/upload/init")
async def init_upload(
    filename: str = Form(...),
    content_type: str = Form(...),
    user: dict = Depends(get_current_user),
):
    """Generate a signed GCS upload URL for direct browser-to-GCS upload."""
    if not gcs_handler.is_ready():
        raise HTTPException(status_code=500, detail="GCS not configured")
    session_id = str(uuid.uuid4())
    safe_filename = f"{session_id}/{filename}"
    upload_url, gcs_path = gcs_handler.generate_upload_url(safe_filename, content_type)
    return {"upload_url": upload_url, "gcs_path": gcs_path, "session_id": session_id}


# --- Streaming analysis endpoint ---

@router.post("/analyze/stream")
async def analyze_meeting_stream(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(None),
    upload_id: str = Form(None),
    original_filename: str = Form(None),
    gcs_path: str = Form(None),
    settings: Settings = Depends(get_settings_dependency),
    user: dict = Depends(get_current_user),
):
    session_id = str(uuid.uuid4())
    temp_dir = None
    temp_filepath = None
    start_time = time.time()

    is_chunked = bool(upload_id)
    is_gcs = bool(gcs_path)
    if is_gcs:
        if not original_filename:
            original_filename = gcs_path.rsplit("/", 1)[-1]
    elif is_chunked:
        if not original_filename:
            original_filename = "recording.mp3"
    elif not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    elif not validate_audio_file(file):
        raise HTTPException(status_code=400, detail=f"Invalid file format")
    elif hasattr(file, 'size') and file.size and file.size > settings.MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large")
    else:
        original_filename = file.filename

    async def event_stream():
        nonlocal temp_dir, temp_filepath
        try:
            temp_dir = tempfile.mkdtemp(prefix=f"session_{session_id}_")
            safe_filename = f"{session_id}_{original_filename}"
            temp_filepath = os.path.join(temp_dir, safe_filename)

            if is_chunked:
                chunk_dir = os.path.join(tempfile.gettempdir(), "manthan_chunks", upload_id)
                manifest_path = os.path.join(chunk_dir, "manifest.json")
                if not os.path.exists(manifest_path):
                    yield json.dumps({"status": "error", "message": "Upload incomplete"}) + "\n"
                    return
                with open(manifest_path) as mf:
                    manifest = json.load(mf)
                with open(temp_filepath, "wb") as out:
                    for i in range(manifest["total_chunks"]):
                        cp = os.path.join(chunk_dir, f"chunk_{i:05d}")
                        if not os.path.exists(cp):
                            yield json.dumps({"status": "error", "message": f"Missing chunk {i}"}) + "\n"
                            return
                        with open(cp, "rb") as cf:
                            out.write(cf.read())
                background_tasks.add_task(cleanup_temp_files, chunk_dir)
            elif is_gcs:
                yield json.dumps({"status": "progress", "step": "transcribing", "percent": 5, "message": "Preparing audio..."}) + "\n"
                if not diarizer or not diarizer.is_ready():
                    yield json.dumps({"status": "error", "message": "Diarizer not available for GCS uploads"}) + "\n"
                    return

                audio_gcs_path = gcs_path
                # Extract audio if video file
                ext = os.path.splitext(original_filename)[1].lower()
                if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
                    audio_gcs_path = gcs_path.rsplit(".", 1)[0] + "_audio.mp3"
                    yield json.dumps({"status": "progress", "step": "extracting", "percent": 7, "message": "Extracting audio from video..."}) + "\n"
                    gcs_handler.extract_audio(gcs_path, audio_gcs_path)

                signed_url = gcs_handler.generate_download_url(audio_gcs_path)
                transcript_segments = await diarizer.transcribe_url(signed_url)
                yield json.dumps({"status": "progress", "step": "transcribing", "percent": 70, "message": f"Transcribed {len(transcript_segments)} segments"}) + "\n"

                yield json.dumps({"status": "progress", "step": "analyzing", "percent": 75, "message": "Running Gemini analysis..."}) + "\n"
                async with ProductionNLPAnalyzer() as nlp_analyzer:
                    analysis_result = await nlp_analyzer.analyze_transcript_only(transcript_segments)
                yield json.dumps({"status": "progress", "step": "done", "percent": 100, "message": f"Complete in {time.time()-start_time:.0f}s"}) + "\n"

                processing_time = round(time.time() - start_time, 2)
                analysis_result["processing_time"] = processing_time
                transcript = analysis_result.get("transcript", [])
                word_count = sum(len(seg.get("text", "").split()) for seg in transcript if isinstance(seg, dict))

                response_obj = AnalysisResponse(session_id=session_id, filename=original_filename, **analysis_result)
                try:
                    transcript_data = [seg.dict() if hasattr(seg, 'dict') else seg for seg in response_obj.transcript]
                    action_items_data = [item.dict() if hasattr(item, 'dict') else item for item in response_obj.action_items]
                    decisions_data = [d.dict() if hasattr(d, 'dict') else d for d in response_obj.key_decisions]
                    await analyses_collection.insert_one({
                        "user_id": user["_id"], "session_id": session_id, "filename": original_filename,
                        "transcript": transcript_data, "summary": response_obj.summary,
                        "action_items": action_items_data, "key_decisions": decisions_data,
                        "processing_time": processing_time, "duration": 0, "word_count": word_count,
                        "created_at": datetime.now(timezone.utc),
                        "gcs_path": gcs_path, "audio_gcs_path": audio_gcs_path,
                    })
                except Exception as save_error:
                    logger.error(f"Failed to save analysis: {save_error}")

                yield json.dumps({
                    "status": "complete", "session_id": session_id, "filename": original_filename,
                    "transcript": transcript_data, "summary": response_obj.summary,
                    "action_items": action_items_data, "key_decisions": decisions_data,
                    "processing_time": processing_time,
                    "gcs_path": gcs_path, "audio_gcs_path": audio_gcs_path,
                }, default=str) + "\n"
                return
            else:
                with open(temp_filepath, "wb") as f:
                    f.write(await file.read())

            if not audio_processor.validate_audio_file(temp_filepath):
                yield json.dumps({"status": "error", "message": "Corrupted or invalid audio file"}) + "\n"
                return

            audio_info = audio_processor.get_audio_info(temp_filepath)
            if audio_info.get('duration', 0) > settings.MAX_AUDIO_DURATION:
                yield json.dumps({"status": "error", "message": f"Audio too long ({audio_info['duration']:.1f}s)"}) + "\n"
                return

            if not settings.validate_api_keys():
                yield json.dumps({"status": "error", "message": "API key not configured"}) + "\n"
                return

            if diarizer and diarizer.is_ready():
                yield json.dumps({"status": "progress", "step": "transcribing", "percent": 10, "message": "Transcribing with speaker diarization..."}) + "\n"
                t_stt = time.time()
                transcript_segments = await diarizer.transcribe(temp_filepath)
                yield json.dumps({"status": "progress", "step": "transcribing", "percent": 70, "message": f"Transcribed {len(transcript_segments)} segments in {time.time()-t_stt:.0f}s"}) + "\n"
                # Clean up raw file immediately — Deepgram is done
                try:
                    os.remove(temp_filepath)
                except Exception:
                    pass
                temp_filepath = None

                yield json.dumps({"status": "progress", "step": "analyzing", "percent": 75, "message": "Running Gemini analysis..."}) + "\n"
                async with ProductionNLPAnalyzer() as nlp_analyzer:
                    analysis_result = await nlp_analyzer.analyze_transcript_only(transcript_segments)
                yield json.dumps({"status": "progress", "step": "done", "percent": 100, "message": f"Complete in {time.time()-start_time:.0f}s"}) + "\n"

                processing_time = round(time.time() - start_time, 2)
                analysis_result["processing_time"] = processing_time
                transcript = analysis_result.get("transcript", [])
                word_count = sum(len(seg.get("text", "").split()) for seg in transcript if isinstance(seg, dict))

                response = AnalysisResponse(
                    session_id=session_id,
                    filename=original_filename,
                    **analysis_result,
                )

                try:
                    transcript_data = [seg.dict() if hasattr(seg, 'dict') else seg for seg in response.transcript]
                    action_items_data = [item.dict() if hasattr(item, 'dict') else item for item in response.action_items]
                    decisions_data = [d.dict() if hasattr(d, 'dict') else d for d in response.key_decisions]

                    await analyses_collection.insert_one({
                        "user_id": user["_id"],
                        "session_id": session_id,
                                    "filename": original_filename,
                        "transcript": transcript_data,
                        "summary": response.summary,
                        "action_items": action_items_data,
                        "key_decisions": decisions_data,
                        "processing_time": processing_time,
                        "duration": audio_info.get("duration", 0),
                        "word_count": word_count,
                        "created_at": datetime.now(timezone.utc),
                    })
                except Exception as save_error:
                    logger.error(f"Failed to save analysis: {save_error}")

                final_payload = {
                    "status": "complete",
                    "session_id": session_id,
                                "filename": original_filename,
                    "transcript": transcript_data,
                    "summary": response.summary,
                    "action_items": action_items_data,
                    "key_decisions": decisions_data,
                    "processing_time": processing_time,
                }
                yield json.dumps(final_payload, default=str) + "\n"
            else:
                processed_audio_path = await audio_processor.process_audio(temp_filepath, session_id)
                async with ProductionNLPAnalyzer() as nlp_analyzer:
                    async for event in nlp_analyzer.analyze_meeting_streaming(processed_audio_path):
                        if event["status"] == "complete":
                            analysis_result = event["result"]
                            processing_time = round(time.time() - start_time, 2)
                            analysis_result["processing_time"] = processing_time

                            transcript = analysis_result.get("transcript", [])
                            word_count = sum(len(seg.get("text", "").split()) for seg in transcript if isinstance(seg, dict))

                            response = AnalysisResponse(
                                session_id=session_id,
                                filename=original_filename,
                                **analysis_result,
                            )

                            try:
                                transcript_data = [seg.dict() if hasattr(seg, 'dict') else seg for seg in response.transcript]
                                action_items_data = [item.dict() if hasattr(item, 'dict') else item for item in response.action_items]
                                decisions_data = [d.dict() if hasattr(d, 'dict') else d for d in response.key_decisions]

                                await analyses_collection.insert_one({
                                    "user_id": user["_id"],
                                    "session_id": session_id,
                        "filename": original_filename,
                                    "transcript": transcript_data,
                                    "summary": response.summary,
                                    "action_items": action_items_data,
                                    "key_decisions": decisions_data,
                                    "processing_time": analysis_result.get("processing_time", 0),
                                    "duration": audio_info.get("duration", 0),
                                    "word_count": word_count,
                                    "created_at": datetime.now(timezone.utc),
                                })
                            except Exception as save_error:
                                logger.error(f"Failed to save analysis: {save_error}")

                            final_payload = {
                                "status": "complete",
                                "session_id": session_id,
                    "filename": original_filename,
                                "transcript": transcript_data,
                                "summary": response.summary,
                                "action_items": action_items_data,
                                "key_decisions": decisions_data,
                                "processing_time": analysis_result.get("processing_time", 0),
                            }
                            yield json.dumps(final_payload, default=str) + "\n"
                        else:
                            yield json.dumps(event) + "\n"

        except Exception as e:
            logger.error(f"Stream error: {e}")
            logger.error(traceback.format_exc())
            yield json.dumps({"status": "error", "message": str(e)}) + "\n"
        finally:
            if temp_dir:
                background_tasks.add_task(cleanup_temp_files, temp_dir)

    origin = request.headers.get("origin", "*")
    return StreamingResponse(
        event_stream(),
        media_type="text/plain",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
        },
    )


# --- Playback URL ---

@router.post("/playback-url")
async def get_playback_url(
    gcs_path: str = Form(...),
    user: dict = Depends(get_current_user),
):
    """Generate a signed URL for playing back a stored file from GCS."""
    if not gcs_handler.is_ready():
        raise HTTPException(status_code=500, detail="GCS not configured")
    url = gcs_handler.generate_download_url(gcs_path, minutes=120)
    return {"url": url}


# --- Translation endpoint ---

class TranslateRequest(BaseModel):
    text: str
    target_lang: str  # "hi" or "en"

class TranslateResponse(BaseModel):
    translated: str

@router.post("/translate", response_model=TranslateResponse)
async def translate_text(
    body: TranslateRequest,
    user: dict = Depends(get_current_user),
):
    if body.target_lang not in ("hi", "en"):
        raise HTTPException(status_code=400, detail="target_lang must be 'hi' or 'en'")
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    async with ProductionNLPAnalyzer() as nlp:
        translated = await nlp.translate_text(body.text, body.target_lang)

    return TranslateResponse(translated=translated)


# --- History endpoints ---

@router.get("/analyses", response_model=AnalysisHistoryResponse)
async def list_analyses(
    user: dict = Depends(get_current_user),
    limit: int = 50,
    skip: int = 0,
):
    """
    List all analyses for the authenticated user, newest first.
    """
    cursor = analyses_collection.find(
        {"user_id": user["_id"]}
    ).sort("created_at", -1).skip(skip).limit(limit)

    total = await analyses_collection.count_documents({"user_id": user["_id"]})
    documents = await cursor.to_list(length=limit)

    return AnalysisHistoryResponse(
        analyses=[_analysis_to_summary(doc) for doc in documents],
        total=total,
    )


@router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Get a single analysis by ID. Must belong to the authenticated user.
    """
    try:
        oid = ObjectId(analysis_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid analysis ID")

    doc = await analyses_collection.find_one({
        "_id": oid,
        "user_id": user["_id"],
    })

    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")

    doc["id"] = str(doc.pop("_id"))
    doc.pop("user_id", None)
    doc["created_at"] = _utc_isoformat(doc["created_at"])

    return doc


@router.delete("/analyses/{analysis_id}", response_model=DeleteResponse)
async def delete_analysis(
    analysis_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Delete an analysis by ID. Must belong to the authenticated user.
    """
    try:
        oid = ObjectId(analysis_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid analysis ID")

    result = await analyses_collection.delete_one({
        "_id": oid,
        "user_id": user["_id"],
    })

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return DeleteResponse(message="Analysis deleted")


@router.get("/sessions/{session_id}")
async def get_session_status(session_id: str):
    """
    Get status of a processing session.
    """
    return {
        "session_id": session_id,
        "status": "completed",
        "message": "Session processing completed"
    }


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    background_tasks: BackgroundTasks
):
    """
    Delete session data.
    """
    background_tasks.add_task(cleanup_temp_files)

    return {
        "session_id": session_id,
        "message": "Session cleanup initiated"
    }


@router.get("/status")
async def service_status(settings: Settings = Depends(get_settings_dependency)):
    """
    Get detailed service status.
    """
    return {
        "status": "operational",
        "services": {
            "audio_processor": audio_processor.is_ready(),
            "openai_api": settings.validate_api_keys(),
            "temp_directory": os.path.exists(settings.get_temp_dir())
        },
        "limits": {
            "max_file_size_mb": settings.MAX_FILE_SIZE / 1024 / 1024,
            "max_duration_seconds": settings.MAX_AUDIO_DURATION,
            "supported_formats": settings.supported_formats_list
        },
        "version": settings.APP_VERSION
    }
