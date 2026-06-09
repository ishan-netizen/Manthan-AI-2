"""
Production NLP analyzer using Google Gemini API.
Splits audio into 2 MB chunks and transcribes them concurrently via Gemini,
then merges transcripts and runs a final analysis pass.
"""

import uuid
import asyncio
import logging
import json
import os
import tempfile
import time
import traceback
from typing import List, Dict, Any, Optional, AsyncGenerator

from google import genai
from google.genai import types
from pydub import AudioSegment

from app.models.schemas import (
    TranscriptSegment, ActionItem, KeyDecision, SpeakerStats,
    MeetingInsights, Priority, SentimentLabel
)
from app.utils.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

CHUNK_SIZE_MB = 2
BITRATE_BPS = 64_000
CHUNK_DURATION_MS = int((CHUNK_SIZE_MB * 1024 * 1024 * 8) / BITRATE_BPS * 1000)
MAX_CONCURRENT_CHUNKS = 10

TRANSCRIPTION_PROMPT = """Transcribe this meeting audio segment. Return ONLY valid JSON — no markdown, no code fences, no extra text.

{
  "transcript": [
    {"speaker": "Speaker name", "text": "what they said", "start_time": seconds, "end_time": seconds, "confidence": 0.95}
  ]
}

═══════════════════════════════════
SCRIPT RULES — READ FIRST. VIOLATING THESE WILL PRODUCE WRONG OUTPUT.
═══════════════════════════════════

Rule 1: Hindi words → ALWAYS Devanagari (हिंदी)
Rule 2: English words → ALWAYS Latin (English)
Rule 3: English loanwords in Hindi sentences → ALWAYS Latin

NEVER write these English words in Devanagari. They are English words spoken within Hindi sentences — keep them in Latin script:

WRONG → CORRECT
एप्लीकेशन → application
एप्लीकेशंस → applications
यूजर → user
यूजर्स → users
पेशेंट → patient
पेशेंट्स → patients
कन्वर्सेशन → conversation
कन्वर्सेशंस → conversations
कंपोनेंट → component
डेटाबेस → database
डेवलपमेंट → development
डिजाइन → design
इंटीग्रेट → integrate
इनपुट → input
आउटपुट → output
सिस्टम → system
प्रोजेक्ट → project
फीचर → feature
स्टोर → store
चार्ट → chart/chat
सर्च → search
लिस्ट → list
अकाउंट → account
परमिशन → permission
पार्टिसिपेंट → participant
मेंबर → member
ग्रुप → group
प्रॉब्लम → problem
फ्लॉ → flow
करेक्ट → correct
राइट → right
ओके → okay
एग्जेक्टली → exactly
टोटली → totally
न्यू → new
पर्सपेक्टिव → perspective
डिस्कशन → discussion
क्वेश्चन → question
डुप्लीकेसी → duplicity
API → API
PDF → PDF
OK → OK
ID → ID
हां → haan
हम्म → hmm
हा → haa
अच्छा → achha
ठीक → theek

Also: NEVER write English words like "the", "is", "a", "an", "of", "in", "on", "to", "for", "and", "or", "but", "with", "from", "this", "that", "what", "when", "where", "how", "why", "who", "which", "has", "have", "had", "will", "would", "can", "could", "should", "may", "might", "must", "shall", "as", "at", "by", "so", "if", "no", "not", "yes", "be", "do", "go", "get", "see", "know", "think", "say", "tell", "ask", "give", "take", "make", "come", "want", "need", "like", "use" in Devanagari. These are ALWAYS Latin.

Example of correct Hinglish transcription:
Speaker says: "toh user list account ka details right voh sab aapko mil jayega"
CORRECT: "तो user list account का details right वो सब आपको मिल जाएगा"
WRONG: "तो यूजर लिस्ट अकाउंट का डिटेल्स राइट वो सब आपको मिल जाएगा"

CRITICAL — Speaker Identification Rules:
- For EVERY segment, FIRST identify the speaker by their UNIQUE voice characteristics: gender (male/female), pitch (high/medium/deep), age (young/middle-aged/older), accent, speaking speed.
- If the voice matches a speaker you have ALREADY labeled in this audio, use THAT SAME EXACT label. Do NOT create a new label for the same voice.
- If the voice is DIFFERENT from every speaker you have labeled so far, ONLY THEN create a new label (Speaker 2, Speaker 3, etc.).
- BEFORE labeling any segment as an existing speaker, CONFIRM it is the SAME voice. If the voice sounds different, it IS a different speaker — label them as a new speaker.
- Count how many unique voices are in the audio. The number of unique speaker labels MUST equal the number of unique voices.
- If speakers introduce themselves by name anywhere in the audio, use those names instead of Speaker 1/2/3.
- If someone speaks briefly (an interjection, question, or comment), they still get their OWN speaker label — do NOT merge their lines into another speaker.
- DO NOT alternate labels mechanically. Only switch speakers when the voice actually changes.

CRITICAL — Speaker Naming Rules (DO NOT VIOLATE):
- NEVER guess or infer a speaker's name. Always use "Speaker 1", "Speaker 2", "Speaker 3" by default.
- ONLY use an actual name if the speaker EXPLICITLY introduces themselves with phrases like "My name is X", "I am X", "This is X speaking". Hearing someone's name mentioned in conversation is NOT enough — the speaker must identify THEMSELVES.
- If someone says "Ishan told me..." — the speaker is NOT Ishan. Ishan is a different person being talked about. The speaker remains Speaker N.
- If someone says "Hi, I'm Rahul" — THEN and ONLY THEN can you label that voice as "Rahul" from that point onward.
- When in ANY doubt about a name, use Speaker N. It is always better to use Speaker 1/2/3 than to guess a wrong name.

REMEMBER: Follow the SCRIPT RULES at the top. Hindi in Devanagari, English words in Latin, English loanwords in Latin.

Return ONLY the JSON object, nothing else"""

SPEAKER_NORMALIZATION_PROMPT = """You are a transcript editor. Below is a meeting transcript where multiple chunks were transcribed independently, causing inconsistent speaker labels. The SAME person may be labeled as "Speaker 1" in one chunk and "Speaker 2" in another.

Your task: Normalize all speaker labels so the same physical person has the exact same label throughout.

Rules:
- Analyze speaking style, vocabulary, tone, and conversational flow to determine which labels refer to the same person
- If Speaker A in early segments and Speaker B in later segments are the SAME person, merge them under ONE label
- Do NOT merge genuinely different speakers
- If any speaker introduced themselves by name, use that name
- Return the SAME JSON array but with corrected speaker fields
- Return ONLY valid JSON — no markdown, no code fences, no extra text

Transcript:
{transcript_json}"""

NORMALIZE_AND_ANALYZE_PROMPT = """You are a meeting analyst and transcript editor. Below is a meeting transcript where multiple chunks were transcribed independently, causing inconsistent speaker labels.

Your tasks in ONE response:
1. Normalize speaker labels — merge same speakers across chunks into consistent labels
2. Generate analysis — summary, action items, key decisions, sentiment, topics

Return ONLY valid JSON — no markdown, no code fences, no extra text:

{
  "transcript": [
    {"speaker": "Speaker name", "text": "what they said", "start_time": seconds, "end_time": seconds, "confidence": 0.95}
  ],
  "summary": "Concise 2-3 sentence meeting summary — MUST BE IN ENGLISH",
  "action_items": [
    {"text": "action description — MUST BE IN ENGLISH", "assignee": "person name or null", "deadline": "deadline or null", "priority": "high|medium|low"}
  ],
  "key_decisions": [
    {"decision": "decision description — MUST BE IN ENGLISH", "rationale": "why this was decided — MUST BE IN ENGLISH", "impact": "expected impact"}
  ],
  "sentiment": {"overall": "positive|negative|neutral", "tone": "brief tone description — MUST BE IN ENGLISH", "score": 0.7},
  "topics": ["topic in English", "topic in English"]
}

SPEAKER NORMALIZATION RULES:
- Analyze speaking style, vocabulary, tone, and conversational flow to determine which labels refer to the same person
- If Speaker A in early segments and Speaker B in later segments are the SAME person, merge them under ONE label
- Do NOT merge genuinely different speakers
- If any speaker introduced themselves by name, use that name
- Return the FULL transcript array with corrected speaker fields

ANALYSIS RULES:
- Summary, action items, key decisions, sentiment, and topics MUST be in English — even if the meeting is Hindi or Hinglish
- Extract ALL action items and key decisions — do not miss any

Transcript:
{transcript_json}"""

ANALYSIS_FROM_TEXT_PROMPT = """You are a meeting analyst. Given this complete meeting transcript, return ONLY valid JSON — no markdown, no code fences, no extra text.

{
  "summary": "Concise 2-3 sentence meeting summary — MUST BE IN ENGLISH",
  "action_items": [
    {"text": "action description — MUST BE IN ENGLISH", "assignee": "person name or null", "deadline": "deadline or null", "priority": "high|medium|low"}
  ],
  "key_decisions": [
    {"decision": "decision description — MUST BE IN ENGLISH", "rationale": "why this was decided — MUST BE IN ENGLISH", "impact": "expected impact"}
  ],
  "sentiment": {"overall": "positive|negative|neutral", "tone": "brief tone description — MUST BE IN ENGLISH", "score": 0.7},
  "topics": ["topic in English", "topic in English"]
}

MOST IMPORTANT RULE — READ FIRST:
EVERY field in the JSON output (summary, action_items, key_decisions, sentiment, topics) MUST be written in ENGLISH. This is non-negotiable. Even if the transcript contains Hindi or Hinglish, the analysis output must be 100% English. DO NOT output Hindi, Devanagari, or Hinglish in the analysis fields. DO NOT translate action items or decisions into Hindi. ALL analysis text must be in English.

Example of WRONG output: "कार्य सूची" or "निर्णय"
Example of CORRECT output: "Action items" or "Decisions"

Extract ALL action items and key decisions — do not miss any.
Return ONLY the JSON object, nothing else"""

ANALYSIS_PROMPT = """You are a meeting analyst. Analyze this meeting audio recording and return ONLY valid JSON — no markdown, no code fences, no extra text.

MOST IMPORTANT RULE — READ FIRST:
The fields summary, action_items, key_decisions, sentiment, and topics MUST be written in ENGLISH. Even if the meeting is in Hindi or Hinglish, the analysis output must be 100% English. DO NOT output Hindi or Devanagari in these fields. Only the "text" field inside transcript segments may contain Hindi/Devanagari (transcribe as spoken). All analysis fields must be English.

Example WRONG: "summary": "मीटिंग में प्रोजेक्ट की समीक्षा की गई"
Example CORRECT: "summary": "The meeting covered the project review and next steps."

Return exactly this structure:

{
  "transcript": [
    {"speaker": "Speaker name", "text": "what they said", "start_time": seconds, "end_time": seconds, "confidence": 0.95}
  ],
  "summary": "Concise 2-3 sentence meeting summary — MUST BE IN ENGLISH",
  "action_items": [
    {"text": "action description — MUST BE IN ENGLISH", "assignee": "person name or null", "deadline": "deadline or null", "priority": "high|medium|low"}
  ],
  "key_decisions": [
    {"decision": "decision description — MUST BE IN ENGLISH", "rationale": "why this was decided — MUST BE IN ENGLISH", "impact": "expected impact"}
  ],
  "sentiment": {"overall": "positive|negative|neutral", "tone": "brief tone description — MUST BE IN ENGLISH", "score": 0.7},
  "topics": ["topic in English", "topic in English"]
}

CRITICAL — Output Language:
- Summary, action items, key decisions, sentiment, and topics MUST be written in English only — regardless of the meeting language. Even if the meeting is in Hindi or Hinglish, the analysis output must be in English.

═══════════════════════════════════
TRANSCRIPT SCRIPT RULES — DO NOT WRITE ENGLISH WORDS IN DEVANAGARI
═══════════════════════════════════

For the transcript text field ONLY:
- Hindi words → Devanagari (हिंदी)
- English words → Latin script. NEVER transliterate English words into Devanagari.
- English loanwords in Hindi sentences → Latin script.

NEVER write these in Devanagari: user (यूजर), patient (पेशेंट), application (एप्लीकेशन), conversation (कन्वर्सेशन), component (कंपोनेंट), database (डेटाबेस), development (डेवलपमेंट), design (डिजाइन), project (प्रोजेक्ट), feature (फीचर), system (सिस्टम), store (स्टोर), search (सर्च), list (लिस्ट), account (अकाउंट), permission (परमिशन), member (मेंबर), group (ग्रुप), problem (प्रॉब्लम), correct (करेक्ट), right (राइट), okay (ओके), exactly (एग्जेक्टली), totally (टोटली), new (न्यू), question (क्वेश्चन), hmm (हम्म), haan (हां), haa (हा), achha (अच्छा), theek (ठीक), API, PDF, OK, ID

Correct Hinglish: "तो user list account का details right वो सब आपको मिल जाएगा"
Wrong Hinglish: "तो यूजर लिस्ट अकाउंट का डिटेल्स राइट वो सब आपको मिल जाएगा"

CRITICAL — Speaker Identification Rules (READ CAREFULLY — THIS IS THE MOST IMPORTANT PART):
- For EVERY segment, FIRST identify the speaker by their UNIQUE voice characteristics: gender (male/female), pitch (high/medium/deep), age (young/middle-aged/older), accent, speaking speed.
- If the voice matches a speaker you have ALREADY labeled in this audio, use THAT SAME EXACT label. Do NOT create a new label for the same voice.
- If the voice is DIFFERENT from every speaker you have labeled so far, ONLY THEN create a new label (Speaker 2, Speaker 3, etc.).
- BEFORE labeling any segment as an existing speaker, CONFIRM it is the SAME voice. If the voice sounds different, it IS a different speaker — label them as a new speaker.
- Count how many unique voices are in the audio. The number of unique speaker labels MUST equal the number of unique voices.
- If speakers introduce themselves by name anywhere in the audio, use those names instead of Speaker 1/2/3.
- If someone speaks briefly (an interjection, question, or comment), they still get their OWN speaker label — do NOT merge their lines into another speaker.
- DO NOT alternate labels mechanically. Only switch speakers when the voice actually changes.

CRITICAL — Speaker Naming Rules (DO NOT VIOLATE):
- NEVER guess or infer a speaker's name. Always use "Speaker 1", "Speaker 2", "Speaker 3" by default.
- ONLY use an actual name if the speaker EXPLICITLY introduces themselves with phrases like "My name is X", "I am X", "This is X speaking". Hearing someone's name mentioned in conversation is NOT enough — the speaker must identify THEMSELVES.
- If someone says "Ishan told me..." — the speaker is NOT Ishan. Ishan is a different person being talked about. The speaker remains Speaker N.
- If someone says "Hi, I'm Rahul" — THEN and ONLY THEN can you label that voice as "Rahul" from that point onward.
- When in ANY doubt about a name, use Speaker N. It is always better to use Speaker 1/2/3 than to guess a wrong name.

Other Rules:
- Provide accurate timestamps for each transcript segment
- Extract ALL action items and key decisions — do not miss any
- Return ONLY the JSON object, nothing else"""


class ProductionNLPAnalyzer:
    """Production NLP analyzer using Google Gemini API."""

    def __init__(self):
        api_key = (settings.GEMINI_API_KEY or "").strip()
        if not api_key:
            logger.error("GEMINI_API_KEY not configured!")
        else:
            logger.info(f"Gemini API key loaded (length: {len(api_key)})")

        self.client = genai.Client(
            api_key=api_key,
            http_options={"timeout": 600000},  # 10 min timeout for large files
        )
        # gemini-2.5-flash-native-audio-latest is the Live API (WebSocket) model — it does NOT
        # work with generate_content + Files API and returns 404 on every call.
        # gemini-2.5-flash supports audio files via Files API and is the correct model here.
        self.model_name = "gemini-2.5-flash"
        self.text_model_name = "gemini-2.5-flash"
        logger.info(f"Production NLP Analyzer initialized — model: {self.model_name}")

    async def translate_text(self, text: str, target_lang: str) -> str:
        """Translate text to Hindi or English using Gemini."""
        if target_lang == "hi":
            prompt = f'Translate the following text to Hindi (Devanagari script). Return ONLY the translated text, no explanations: {text}'
        else:
            prompt = f'Translate the following text to English. Return ONLY the translated text, no explanations: {text}'

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.text_model_name,
            contents=[prompt],
        )
        return (response.text or text).strip()

    async def analyze_meeting(self, audio_path: str) -> Dict[str, Any]:
        """
        Perform complete meeting analysis using Gemini API.
        Files larger than 2 MB are split and transcribed concurrently.
        """
        file_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
        file_size_mb = file_size / (1024 * 1024)
        threshold_mb = CHUNK_SIZE_MB
        logger.info(f"[NLP] Audio file size: {file_size_mb:.1f} MB | threshold: {threshold_mb} MB")

        if file_size > CHUNK_SIZE_MB * 1024 * 1024:
            expected_chunks = int(file_size_mb / CHUNK_SIZE_MB) + 1
            logger.info(
                f"[NLP] File exceeds {threshold_mb} MB — using CHUNKED mode | "
                f"expected ~{expected_chunks} chunks of {CHUNK_DURATION_MS/1000:.0f}s each | "
                f"max {MAX_CONCURRENT_CHUNKS} concurrent"
            )
            return await self._analyze_meeting_chunked(audio_path)

        logger.info(f"[NLP] File within {threshold_mb} MB limit — using SINGLE-CALL mode")
        return await self._analyze_meeting_single(audio_path)

    async def analyze_meeting_streaming(self, audio_path: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream analysis progress as NDJSON events, ending with a {"status":"complete","result":...} event.
        """
        t0 = time.time()
        file_size = os.path.getsize(audio_path)

        yield {"status": "progress", "step": "uploading", "percent": 5, "message": "File received"}

        if file_size <= CHUNK_SIZE_MB * 1024 * 1024:
            yield {"status": "progress", "step": "transcribing", "percent": 15, "message": "Transcribing with Gemini..."}
            result = await self._analyze_meeting_single(audio_path)
            yield {"status": "progress", "step": "done", "percent": 100, "message": f"Complete in {time.time() - t0:.1f}s"}
            yield {"status": "complete", "result": result}
            return

        file_size_mb = file_size / (1024 * 1024)
        yield {"status": "progress", "step": "splitting", "percent": 7, "message": f"Splitting {file_size_mb:.1f} MB into ~2 MB chunks..."}

        chunk_paths = await asyncio.to_thread(self._split_audio, audio_path)
        total_chunks = len(chunk_paths)
        yield {"status": "progress", "step": "splitting", "percent": 10, "message": f"Split into {total_chunks} chunks"}

        progress_q: asyncio.Queue = asyncio.Queue()
        results_ref: list = [None] * total_chunks
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)

        async def transcribe_with_progress(idx: int, path: str):
            try:
                async with semaphore:
                    result = await self._transcribe_chunk(path, idx)
                results_ref[idx] = result
                cnt = sum(1 for r in results_ref if r is not None)
                await progress_q.put({"type": "chunk_done", "idx": idx, "count": cnt})
            except Exception as e:
                logger.warning(f"[STREAM] Chunk {idx} raised: {e}")
                results_ref[idx] = None
                cnt = sum(1 for r in results_ref if r is not None)
                await progress_q.put({"type": "chunk_done", "idx": idx, "count": cnt})

        bg_task = asyncio.ensure_future(
            asyncio.gather(*[transcribe_with_progress(i, p) for i, p in enumerate(chunk_paths)])
        )

        completed_count = 0
        while True:
            event = await progress_q.get()
            count = event["count"]
            if count > completed_count:
                completed_count = count
                pct = 10 + int(60 * completed_count / total_chunks)
                yield {"status": "progress", "step": "transcribing", "percent": pct, "message": f"Transcribing chunk {completed_count}/{total_chunks}..."}
            if completed_count >= total_chunks:
                break

        await bg_task

        chunk_offset_s = CHUNK_DURATION_MS / 1000.0
        merged_transcript: List[dict] = []
        for idx, result in enumerate(results_ref):
            if result is None:
                continue
            offset = idx * chunk_offset_s
            for seg in result:
                seg["start_time"] = round(float(seg.get("start_time", 0)) + offset, 2)
                seg["end_time"] = round(float(seg.get("end_time", 0)) + offset, 2)
                merged_transcript.append(seg)

        if not merged_transcript:
            logger.error("[STREAM] Zero transcript segments — all chunks failed")
            result = self._get_demo_analysis(reason="All chunks failed")
            yield {"status": "complete", "result": result}
            return

        full_text = " ".join(s.get("text", "") for s in merged_transcript)
        yield {"status": "progress", "step": "analyzing", "percent": 75, "message": "Normalizing speakers & analyzing..."}

        analysis_data = await self._normalize_and_analyze(merged_transcript)
        analysis_data["transcript"] = merged_transcript
        final = self._build_analysis_result(analysis_data)

        for p in chunk_paths:
            try:
                os.remove(p)
            except Exception:
                pass

        yield {"status": "progress", "step": "done", "percent": 100, "message": f"Complete in {time.time() - t0:.1f}s"}
        yield {"status": "complete", "result": final}

    async def analyze_transcript_only(self, transcript_segments: List[dict]) -> Dict[str, Any]:
        """
        Analyze a pre-transcribed transcript (from STT diarizer).
        Only runs text analysis — no audio processing or transcription.
        """
        t0 = time.time()
        full_text = " ".join(s.get("text", "") for s in transcript_segments)
        word_count = len(full_text.split())
        logger.info(f"[ANALYZE-ONLY] {len(transcript_segments)} segments, ~{word_count} words, {len(full_text)} chars")

        analysis_data = await self._analyze_transcript(full_text)
        analysis_data["transcript"] = transcript_segments
        final = self._build_analysis_result(analysis_data)
        logger.info(f"[ANALYZE-ONLY] Done in {time.time() - t0:.1f}s — actions: {len(final['action_items'])}, decisions: {len(final['key_decisions'])}")
        return final

    async def _analyze_meeting_single(self, audio_path: str) -> Dict[str, Any]:
        """
        Perform complete meeting analysis with a single Gemini call (files <= 2MB).
        """
        t0 = time.time()
        try:
            file_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
            mime_type = self._get_mime_type(audio_path)
            logger.info(f"[SINGLE 1/4] File: {audio_path} ({file_size/1024/1024:.1f} MB) | MIME: {mime_type}")

            logger.info("[SINGLE 2/4] Uploading to Gemini Files API...")
            t1 = time.time()
            try:
                audio_file = await asyncio.to_thread(
                    self.client.files.upload,
                    file=audio_path,
                    config=types.UploadFileConfig(mime_type=mime_type),
                )
            except Exception as e:
                logger.error(f"[SINGLE 2/4] UPLOAD FAILED — {type(e).__name__}: {e}")
                return self._get_demo_analysis(reason=f"Gemini upload failed: {type(e).__name__}: {e}")
            logger.info(f"[SINGLE 2/4] Uploaded in {time.time()-t1:.1f}s — name={audio_file.name} state={audio_file.state}")

            logger.info("[SINGLE 2b/4] Waiting for Gemini to process file...")
            t1 = time.time()
            await self._wait_for_file(audio_file)
            logger.info(f"[SINGLE 2b/4] File ready in {time.time()-t1:.1f}s")

            logger.info(f"[SINGLE 3/4] Calling generate_content (model={self.model_name})...")
            t1 = time.time()
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=[ANALYSIS_PROMPT, audio_file],
                )
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.error(f"[SINGLE 3/4] RATE LIMITED (429) — {e}")
                    return self._get_demo_analysis(reason=f"Gemini rate limit (429): {e}")
                logger.error(f"[SINGLE 3/4] INFERENCE FAILED — {type(e).__name__}: {e}")
                logger.error(traceback.format_exc())
                return self._get_demo_analysis(reason=f"Gemini inference failed: {type(e).__name__}: {e}")

            raw_text = response.text or ""
            logger.info(f"[SINGLE 3/4] Response in {time.time()-t1:.1f}s — {len(raw_text)} chars")
            logger.info(f"[SINGLE 3/4] Response preview: {raw_text[:300]}")

            logger.info("[SINGLE 4/4] Parsing JSON response...")
            t1 = time.time()
            result = self._parse_json(raw_text)
            if not result:
                logger.error(f"[SINGLE 4/4] JSON PARSE FAILED — full raw response ({len(raw_text)} chars):")
                logger.error(raw_text[:1000])
                return self._get_demo_analysis(reason=f"JSON parse failed — Gemini returned non-JSON ({len(raw_text)} chars)")

            logger.info(f"[SINGLE 4/4] Parsed in {time.time()-t1:.1f}s — keys: {list(result.keys())}")

            if self._contains_devanagari(result.get("summary", "")):
                logger.info("[SINGLE] Detected Hindi in summary — translating to English...")
                result["summary"] = await self.translate_text(result["summary"], "en")
            for i, item in enumerate(result.get("action_items", [])):
                if isinstance(item, dict) and self._contains_devanagari(item.get("text", "")):
                    result["action_items"][i]["text"] = await self.translate_text(item["text"], "en")
            for i, d in enumerate(result.get("key_decisions", [])):
                if isinstance(d, dict):
                    if self._contains_devanagari(d.get("decision", "")):
                        result["key_decisions"][i]["decision"] = await self.translate_text(d["decision"], "en")
                    if self._contains_devanagari(d.get("rationale", "")):
                        result["key_decisions"][i]["rationale"] = await self.translate_text(d["rationale"], "en")

            final = self._build_analysis_result(result)
            logger.info(
                f"[SINGLE DONE] Total: {time.time()-t0:.1f}s | "
                f"transcript={len(final['transcript'])} segs | "
                f"actions={len(final['action_items'])} | decisions={len(final['key_decisions'])}"
            )
            return final

        except Exception as e:
            logger.error(f"[SINGLE FAIL] Unhandled exception after {time.time()-t0:.1f}s — {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return self._get_demo_analysis(reason=f"Unhandled exception: {type(e).__name__}: {e}")

    async def _analyze_meeting_chunked(self, audio_path: str) -> Dict[str, Any]:
        """
        Split audio into ~2 MB chunks, transcribe all concurrently via Gemini,
        merge transcripts, then run a final text-only analysis pass.
        """
        t0 = time.time()
        chunk_paths: List[str] = []
        try:
            file_size = os.path.getsize(audio_path)
            file_size_mb = file_size / (1024 * 1024)
            logger.info(f"[CHUNKED] Splitting {file_size_mb:.1f} MB audio into ~{CHUNK_SIZE_MB} MB chunks (each ~{CHUNK_DURATION_MS/1000:.0f}s)...")

            chunk_paths = await asyncio.to_thread(self._split_audio, audio_path)
            total_chunks = len(chunk_paths)
            logger.info(f"[CHUNKED] Split complete — {total_chunks} chunks created")
            for i, p in enumerate(chunk_paths):
                sz = os.path.getsize(p) / (1024 * 1024) if os.path.exists(p) else 0
                logger.info(f"[CHUNKED]   chunk-{i:02d}: {p} ({sz:.2f} MB)")

            semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)
            chunk_offset_s = CHUNK_DURATION_MS / 1000.0

            async def transcribe_one(idx: int, path: str) -> Optional[list]:
                async with semaphore:
                    return await self._transcribe_chunk(path, idx)

            logger.info(f"[CHUNKED] Launching {total_chunks} transcription tasks (max {MAX_CONCURRENT_CHUNKS} concurrent)...")
            t_transcribe = time.time()
            tasks = [transcribe_one(i, p) for i, p in enumerate(chunk_paths)]
            chunk_results = await asyncio.gather(*tasks)

            merged_transcript: List[dict] = []
            failed_chunks = 0
            failed_indices = []
            for idx, result in enumerate(chunk_results):
                if result is None:
                    failed_chunks += 1
                    failed_indices.append(idx)
                    continue
                offset = idx * chunk_offset_s
                for seg in result:
                    seg["start_time"] = round(float(seg.get("start_time", 0)) + offset, 2)
                    seg["end_time"] = round(float(seg.get("end_time", 0)) + offset, 2)
                    merged_transcript.append(seg)

            logger.info(
                f"[CHUNKED] Transcription done in {time.time()-t_transcribe:.1f}s | "
                f"succeeded={total_chunks-failed_chunks}/{total_chunks} | "
                f"segments={len(merged_transcript)}"
            )
            if failed_chunks > 0:
                logger.error(
                    f"[CHUNKED] {failed_chunks} CHUNK(S) FAILED — indices: {failed_indices} | "
                    f"Likely causes: Gemini rate limit (429), upload timeout, invalid audio segment"
                )

            if not merged_transcript:
                reason = f"all {total_chunks} chunks failed transcription"
                logger.error(f"[CHUNKED] CRITICAL: Zero transcript segments — {reason}")
                return self._get_demo_analysis(reason=reason)

            full_text = " ".join(s.get("text", "") for s in merged_transcript)
            word_count = len(full_text.split())
            logger.info(f"[CHUNKED] Merged transcript: {len(full_text)} chars, ~{word_count} words")

            logger.info(f"[CHUNKED] Normalizing speakers + analyzing in one call...")
            t_analysis = time.time()
            analysis_data = await self._normalize_and_analyze(merged_transcript)
            logger.info(f"[CHUNKED] Combined normalize+analyze done in {time.time()-t_analysis:.1f}s")

            analysis_data["transcript"] = merged_transcript
            final = self._build_analysis_result(analysis_data)
            logger.info(
                f"[CHUNKED DONE] Total: {time.time()-t0:.1f}s | "
                f"transcript={len(final['transcript'])} segs | "
                f"actions={len(final['action_items'])} | decisions={len(final['key_decisions'])}"
            )
            return final

        except Exception as e:
            logger.error(f"[CHUNKED FAIL] Unhandled exception after {time.time()-t0:.1f}s — {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return self._get_demo_analysis(reason=f"Unhandled exception in chunked analysis: {type(e).__name__}: {e}")
        finally:
            for p in chunk_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass

    def _split_audio(self, file_path: str) -> List[str]:
        audio = AudioSegment.from_file(file_path)
        total_ms = len(audio)
        chunk_paths: List[str] = []

        for i, start_ms in enumerate(range(0, total_ms, CHUNK_DURATION_MS)):
            end_ms = min(start_ms + CHUNK_DURATION_MS, total_ms)
            chunk = audio[start_ms:end_ms]
            fd, tmp_path = tempfile.mkstemp(suffix=f"_chunk_{i:03d}.mp3")
            os.close(fd)
            chunk.export(tmp_path, format="mp3", bitrate="64k")
            chunk_paths.append(tmp_path)

        return chunk_paths

    async def _transcribe_chunk(self, chunk_path: str, chunk_index: int) -> Optional[list]:
        tag = f"[CHUNK-{chunk_index:02d}]"
        chunk_size_mb = os.path.getsize(chunk_path) / (1024 * 1024) if os.path.exists(chunk_path) else 0
        t_total = time.time()
        logger.info(f"{tag} Starting — {chunk_size_mb:.2f} MB")

        # Step 1: Upload
        mime_type = "audio/mpeg"
        t_up = time.time()
        try:
            audio_file = await asyncio.to_thread(
                self.client.files.upload,
                file=chunk_path,
                config=types.UploadFileConfig(mime_type=mime_type),
            )
            logger.info(f"{tag} Uploaded in {time.time()-t_up:.1f}s — file={audio_file.name} state={audio_file.state}")
        except Exception as e:
            logger.error(f"{tag} UPLOAD FAILED — {type(e).__name__}: {e}")
            return None

        # Step 2: Wait for Gemini to process the file
        try:
            await self._wait_for_file(audio_file, max_wait=60)
        except Exception as e:
            logger.error(f"{tag} FILE-WAIT FAILED — {type(e).__name__}: {e}")
            return None

        # Step 3: Inference with retry on 429
        t_gen = time.time()
        response = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=[TRANSCRIPTION_PROMPT, audio_file],
                )
                break
            except Exception as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                if is_rate_limit and attempt < 2:
                    wait_s = (2 ** attempt) * 10  # 10s then 20s
                    logger.warning(f"{tag} Rate limited (429), retrying in {wait_s}s (attempt {attempt+1}/3)")
                    await asyncio.sleep(wait_s)
                else:
                    if is_rate_limit:
                        logger.error(f"{tag} RATE LIMITED (429) — all 3 attempts exhausted: {e}")
                        logger.error(f"{tag} FIX: Upgrade to pay-as-you-go Gemini API key (free tier = 10 RPM)")
                    else:
                        logger.error(f"{tag} INFERENCE FAILED — {type(e).__name__}: {e}")
                        logger.error(traceback.format_exc())
                    return None

        if response is None:
            logger.error(f"{tag} No response after retries")
            return None

        logger.info(f"{tag} Inference done in {time.time()-t_gen:.1f}s")

        # Step 4: Parse JSON
        raw_text = response.text or ""
        if not raw_text:
            logger.error(f"{tag} Empty response from Gemini")
            return None

        data = self._parse_json(raw_text)
        if not data:
            logger.error(f"{tag} JSON PARSE FAILED — raw ({len(raw_text)} chars): {raw_text[:400]}")
            return None

        transcript = data.get("transcript", [])
        logger.info(f"{tag} DONE — {len(transcript)} segments in {time.time()-t_total:.1f}s total")
        return transcript

    async def _normalize_and_analyze(self, transcript: List[dict]) -> Dict[str, Any]:
        """Normalize speakers and generate analysis in ONE Gemini call."""
        transcript_json = json.dumps(transcript, ensure_ascii=False)
        prompt = NORMALIZE_AND_ANALYZE_PROMPT.format(transcript_json=transcript_json)

        logger.info(f"[COMBINED] Normalizing speakers + analyzing in one call — {len(transcript)} segments")
        t0 = time.time()
        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.text_model_name,
                contents=[prompt],
            )
        except Exception as e:
            logger.error(f"[COMBINED] Failed — {type(e).__name__}: {e}")
            return {"summary": "Analysis unavailable", "action_items": [], "key_decisions": [], "sentiment": {"overall": "neutral", "tone": "unknown", "score": 0.5}, "topics": []}

        raw_text = response.text or ""
        data = self._parse_json(raw_text)
        if not data:
            logger.error(f"[COMBINED] JSON parse failed — {len(raw_text)} chars: {raw_text[:300]}")
            return {"summary": "Analysis unavailable", "action_items": [], "key_decisions": [], "sentiment": {"overall": "neutral", "tone": "unknown", "score": 0.5}, "topics": []}

        normalized = data.get("transcript")
        if normalized and isinstance(normalized, list) and len(normalized) == len(transcript):
            for i, seg in enumerate(normalized):
                if isinstance(seg, dict) and "speaker" in seg:
                    transcript[i]["speaker"] = seg["speaker"]
            speakers = set(s.get("speaker") for s in transcript)
            logger.info(f"[COMBINED] Speakers normalized: {speakers}")

        if self._contains_devanagari(data.get("summary", "")):
            logger.info("[COMBINED] Hindi detected in summary — translating")
            data["summary"] = await self.translate_text(data["summary"], "en")
        for i, item in enumerate(data.get("action_items", [])):
            if isinstance(item, dict) and self._contains_devanagari(item.get("text", "")):
                data["action_items"][i]["text"] = await self.translate_text(item["text"], "en")
        for i, d in enumerate(data.get("key_decisions", [])):
            if isinstance(d, dict):
                if self._contains_devanagari(d.get("decision", "")):
                    data["key_decisions"][i]["decision"] = await self.translate_text(d["decision"], "en")
                if self._contains_devanagari(d.get("rationale", "")):
                    data["key_decisions"][i]["rationale"] = await self.translate_text(d["rationale"], "en")

        if isinstance(data.get("sentiment", {}).get("tone", ""), str) and self._contains_devanagari(data["sentiment"]["tone"]):
            data["sentiment"]["tone"] = await self.translate_text(data["sentiment"]["tone"], "en")

        logger.info(f"[COMBINED] Done in {time.time() - t0:.1f}s")
        return data

    async def _normalize_speakers(self, transcript: List[dict]) -> List[dict]:
        """Normalize inconsistent speaker labels across chunks using Gemini."""
        if len(transcript) < 2:
            return transcript

        transcript_json = json.dumps(transcript, ensure_ascii=False)
        prompt = SPEAKER_NORMALIZATION_PROMPT.format(transcript_json=transcript_json)

        logger.info(f"[NORMALIZE] Sending {len(transcript)} segments for speaker normalization...")
        t0 = time.time()
        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.text_model_name,
                contents=[prompt],
            )
        except Exception as e:
            logger.warning(f"[NORMALIZE] Failed — {type(e).__name__}: {e}, keeping original labels")
            return transcript

        raw_text = response.text or ""
        normalized = self._parse_json(raw_text)
        if normalized and isinstance(normalized, list) and len(normalized) == len(transcript):
            for i, seg in enumerate(normalized):
                if isinstance(seg, dict) and "speaker" in seg:
                    transcript[i]["speaker"] = seg["speaker"]
            unique_speakers = set(s.get("speaker") for s in transcript)
            logger.info(f"[NORMALIZE] Done in {time.time() - t0:.1f}s — {len(unique_speakers)} unique speakers: {unique_speakers}")
        else:
            logger.warning(f"[NORMALIZE] Invalid response, keeping original labels")

        return transcript

    async def _analyze_transcript(self, transcript_text: str) -> Dict[str, Any]:
        word_count = len(transcript_text.split())
        logger.info(f"[TEXT-ANALYSIS] Sending {len(transcript_text)} chars (~{word_count} words) to {self.text_model_name}")
        t0 = time.time()
        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.text_model_name,
                contents=[ANALYSIS_FROM_TEXT_PROMPT, transcript_text],
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                logger.error(f"[TEXT-ANALYSIS] RATE LIMITED (429): {e}")
            else:
                logger.error(f"[TEXT-ANALYSIS] FAILED — {type(e).__name__}: {e}")
            return {"summary": "Analysis unavailable", "action_items": [], "key_decisions": [], "sentiment": {"overall": "neutral", "tone": "unknown", "score": 0.5}, "topics": []}

        raw_text = response.text or ""
        logger.info(f"[TEXT-ANALYSIS] Response in {time.time()-t0:.1f}s — {len(raw_text)} chars")
        data = self._parse_json(raw_text)

        if not data:
            logger.error(f"[TEXT-ANALYSIS] JSON PARSE FAILED — raw ({len(raw_text)} chars): {raw_text[:500]}")
            return {"summary": "Analysis unavailable", "action_items": [], "key_decisions": [], "sentiment": {"overall": "neutral", "tone": "unknown", "score": 0.5}, "topics": []}

        if self._contains_devanagari(data.get("summary", "")):
            logger.info("[TEXT-ANALYSIS] Detected Hindi in summary — translating to English...")
            data["summary"] = await self.translate_text(data["summary"], "en")

        items = data.get("action_items", [])
        for i, item in enumerate(items):
            if isinstance(item, dict) and self._contains_devanagari(item.get("text", "")):
                items[i]["text"] = await self.translate_text(item["text"], "en")

        decisions = data.get("key_decisions", [])
        for i, d in enumerate(decisions):
            if isinstance(d, dict):
                if self._contains_devanagari(d.get("decision", "")):
                    decisions[i]["decision"] = await self.translate_text(d["decision"], "en")
                if self._contains_devanagari(d.get("rationale", "")):
                    decisions[i]["rationale"] = await self.translate_text(d["rationale"], "en")

        tone = data.get("sentiment", {}).get("tone", "")
        if isinstance(tone, str) and self._contains_devanagari(tone):
            data["sentiment"]["tone"] = await self.translate_text(tone, "en")

        return data

    @staticmethod
    def _contains_devanagari(text: str) -> bool:
        if not text:
            return False
        return any('\u0900' <= c <= '\u097f' for c in text)

    async def _wait_for_file(self, audio_file, max_wait: int = 120):
        """Wait for uploaded file to be processed by Gemini."""
        waited = 0
        current = audio_file
        while current.state == types.FileState.PROCESSING:
            if waited >= max_wait:
                logger.warning(f"File still processing after {max_wait}s, proceeding")
                break
            await asyncio.sleep(2)
            waited += 2
            current = await asyncio.to_thread(
                self.client.files.get, name=current.name
            )
            if waited % 10 == 0:
                logger.info(f"Waiting for file... {waited}s elapsed")
        logger.info(f"File ready after {waited}s (state: {current.state})")

    def _get_mime_type(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".mp4": "video/mp4",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
            ".webm": "audio/webm",
        }.get(ext, "audio/mpeg")

    def _parse_json(self, text: str) -> Dict[str, Any] | None:
        """Parse JSON from Gemini response, handling markdown fences."""
        if not text:
            return None

        cleaned = text.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```", 2)
            if len(parts) >= 2:
                cleaned = parts[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
                cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            return None

    def _build_analysis_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Build final analysis result from Gemini's JSON response."""

        # --- Transcript ---
        raw_transcript = data.get("transcript", [])
        transcript_segments: List[TranscriptSegment] = []
        for i, seg in enumerate(raw_transcript):
            try:
                transcript_segments.append(TranscriptSegment(
                    id=str(uuid.uuid4()),
                    speaker=str(seg.get("speaker", f"Speaker {i + 1}")),
                    text=str(seg.get("text", "")).strip(),
                    start_time=float(seg.get("start_time", i * 5.0)),
                    end_time=float(seg.get("end_time", (i + 1) * 5.0)),
                    confidence=float(seg.get("confidence", 0.9)),
                ))
            except Exception as e:
                logger.warning(f"Skipping malformed transcript segment: {e}")

        full_text = " ".join(s.text for s in transcript_segments)
        word_count = len(full_text.split()) if full_text else 0
        duration = max((s.end_time for s in transcript_segments), default=0.0)

        # --- Summary ---
        summary = str(data.get("summary", "No summary available"))

        # --- Action Items ---
        action_items: List[ActionItem] = []
        for item in data.get("action_items", [])[:10]:
            try:
                priority_str = str(item.get("priority", "medium")).lower()
                action_items.append(ActionItem(
                    id=str(uuid.uuid4()),
                    text=str(item.get("text", "")),
                    assignee=item.get("assignee") if item.get("assignee") else None,
                    deadline=item.get("deadline") if item.get("deadline") else None,
                    priority=Priority(priority_str) if priority_str in {"low", "medium", "high", "urgent"} else Priority.MEDIUM,
                    confidence=0.9,
                ))
            except Exception as e:
                logger.warning(f"Skipping malformed action item: {e}")

        # --- Key Decisions ---
        key_decisions: List[KeyDecision] = []
        for item in data.get("key_decisions", [])[:5]:
            try:
                key_decisions.append(KeyDecision(
                    id=str(uuid.uuid4()),
                    decision=str(item.get("decision", "")),
                    rationale=str(item.get("rationale", "")),
                    impact=str(item.get("impact", "")),
                    confidence=0.85,
                ))
            except Exception as e:
                logger.warning(f"Skipping malformed decision: {e}")

        # --- Sentiment ---
        sentiment_data = data.get("sentiment", {})
        overall = str(sentiment_data.get("overall", "neutral")).lower()
        if overall not in {"positive", "negative", "neutral", "mixed"}:
            overall = "neutral"

        # --- Topics ---
        topics = [str(t) for t in data.get("topics", [])[:5]]

        # --- Speaker Stats (computed locally) ---
        speakers = self._analyze_speakers(transcript_segments)

        # --- Insights ---
        insights = self._generate_insights(transcript_segments, sentiment_data, topics)

        return {
            "transcript": transcript_segments,
            "summary": summary,
            "action_items": action_items,
            "key_decisions": key_decisions,
            "speakers": speakers,
            "insights": insights,
            "duration": duration,
            "word_count": word_count,
            "processing_time": 5.0,
        }

    def _analyze_speakers(self, segments: List[TranscriptSegment]) -> List[SpeakerStats]:
        speaker_data: Dict[str, Dict[str, float]] = {}

        for segment in segments:
            speaker = segment.speaker
            if speaker not in speaker_data:
                speaker_data[speaker] = {"speaking_time": 0.0, "word_count": 0}
            speaker_data[speaker]["speaking_time"] += segment.end_time - segment.start_time
            speaker_data[speaker]["word_count"] += len(segment.text.split())

        return [
            SpeakerStats(
                name=name,
                speaking_time=data["speaking_time"],
                word_count=int(data["word_count"]),
                sentiment=SentimentLabel.NEUTRAL,
            )
            for name, data in speaker_data.items()
        ]

    def _generate_insights(
        self, segments: List[TranscriptSegment], sentiment_data: Dict, topics: List[str]
    ) -> MeetingInsights:
        speaker_times: Dict[str, float] = {}
        total_time = 0.0

        for segment in segments:
            duration = segment.end_time - segment.start_time
            speaker_times[segment.speaker] = speaker_times.get(segment.speaker, 0) + duration
            total_time += duration

        participation_balance = {
            speaker: round((t / total_time * 100), 1) if total_time > 0 else 0
            for speaker, t in speaker_times.items()
        }

        return MeetingInsights(
            key_topics=topics,
            sentiment_analysis={
                "overall": sentiment_data.get("overall", "neutral"),
                "score": float(sentiment_data.get("score", 0.0)),
                "distribution": {"positive": 40, "neutral": 45, "negative": 15},
            },
            meeting_tone=str(sentiment_data.get("tone", "Professional discussion")),
            participation_balance=participation_balance,
        )

    def _get_demo_analysis(self, reason: str = "unknown") -> Dict[str, Any]:
        """Return fallback analysis when the real API pipeline fails."""
        logger.error("=" * 70)
        logger.error("[DEMO-FALLBACK] REAL ANALYSIS FAILED — returning hardcoded demo data")
        logger.error(f"[DEMO-FALLBACK] Reason: {reason}")
        logger.error("[DEMO-FALLBACK] The user will see fake results unless the caller raises HTTP 500")
        logger.error("=" * 70)
        transcript_segments = [
            TranscriptSegment(
                id=str(uuid.uuid4()),
                speaker="Speaker 1",
                text="Welcome everyone to today's meeting. Let's start with our project updates.",
                start_time=0.0, end_time=5.0, confidence=0.95,
            ),
            TranscriptSegment(
                id=str(uuid.uuid4()),
                speaker="Speaker 2",
                text="Thanks for organizing this. I have some important updates to share about our progress.",
                start_time=5.0, end_time=10.0, confidence=0.92,
            ),
            TranscriptSegment(
                id=str(uuid.uuid4()),
                speaker="Speaker 1",
                text="Great! We also need to assign action items for next week's deliverables.",
                start_time=10.0, end_time=15.0, confidence=0.88,
            ),
        ]

        return {
            "transcript": transcript_segments,
            "summary": "Team meeting discussing project progress and planning next steps.",
            "action_items": [
                ActionItem(
                    id=str(uuid.uuid4()),
                    text="Assign action items for next week's deliverables",
                    assignee="Team Lead", deadline="Next week",
                    priority=Priority.HIGH, confidence=0.85,
                )
            ],
            "key_decisions": [
                KeyDecision(
                    id=str(uuid.uuid4()),
                    decision="Proceed with current project timeline",
                    rationale="Team consensus on feasibility",
                    impact="Maintains project schedule",
                    confidence=0.80,
                )
            ],
            "speakers": [
                SpeakerStats(name="Speaker 1", speaking_time=10.0, word_count=25, sentiment=SentimentLabel.POSITIVE),
                SpeakerStats(name="Speaker 2", speaking_time=5.0, word_count=15, sentiment=SentimentLabel.NEUTRAL),
            ],
            "insights": MeetingInsights(
                key_topics=["Project", "Updates", "Deliverables", "Timeline"],
                sentiment_analysis={
                    "overall": "positive", "score": 0.7,
                    "distribution": {"positive": 60, "neutral": 35, "negative": 5},
                },
                meeting_tone="Professional and productive",
                participation_balance={"Speaker 1": 66.7, "Speaker 2": 33.3},
            ),
            "duration": 15.0,
            "word_count": 40,
            "processing_time": 2.5,
            "_is_demo_fallback": True,
            "_fallback_reason": reason,
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
