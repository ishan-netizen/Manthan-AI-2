"""
Deepgram speech-to-text with speaker diarization.
Pure Deepgram — no post-processing, no transliteration.
"""

import asyncio
import logging
import os
import time
from typing import List

from deepgram import DeepgramClient

logger = logging.getLogger(__name__)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "").strip()
SPEAKER_LABELS = ["Speaker 1", "Speaker 2", "Speaker 3", "Speaker 4", "Speaker 5", "Speaker 6"]


class SpeechDiarizer:
    """Deepgram STT with speaker diarization."""

    def __init__(self):
        if not DEEPGRAM_API_KEY:
            self._ready = False
            logger.warning("DEEPGRAM_API_KEY not set — SpeechDiarizer disabled")
            return

        self.client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
        self._ready = True
        logger.info("SpeechDiarizer initialized (Deepgram)")

    def is_ready(self) -> bool:
        return self._ready

    async def transcribe(self, audio_path: str) -> List[dict]:
        t0 = time.time()
        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        logger.info(f"[DEEPGRAM] Transcribing {file_size_mb:.1f} MB — diarization + punctuation")

        def file_chunks():
            with open(audio_path, "rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk

        response = await asyncio.to_thread(
            self.client.listen.v1.media.transcribe_file,
            request=file_chunks(),
            model="nova-3",
            language="hi",
            diarize_model="latest",
            punctuate=True,
            smart_format=True,
            utterances=True,
        )

        segments = self._parse_response(response)
        logger.info(f"[DEEPGRAM] Done in {time.time() - t0:.1f}s — {len(segments)} segments")
        return segments

    async def transcribe_url(self, signed_url: str) -> List[dict]:
        """Transcribe audio from a signed GCS URL — Deepgram fetches directly."""
        t0 = time.time()
        logger.info(f"[DEEPGRAM] Transcribing from URL — diarization + punctuation")

        response = await asyncio.to_thread(
            self.client.listen.v1.media.transcribe_url,
            url=signed_url,
            model="nova-3",
            language="hi",
            diarize_model="latest",
            punctuate=True,
            smart_format=True,
            utterances=True,
        )

        segments = self._parse_response(response)
        logger.info(f"[DEEPGRAM] Done in {time.time() - t0:.1f}s — {len(segments)} segments")
        return segments

    def _parse_response(self, response) -> List[dict]:
        results = getattr(response, "results", None)
        if results is None:
            return []

        utterances = getattr(results, "utterances", None) or []

        segments: List[dict] = []
        for u in utterances:
            speaker_idx = getattr(u, "speaker", 0) or 0
            speaker = SPEAKER_LABELS[speaker_idx] if speaker_idx < len(SPEAKER_LABELS) else f"Speaker {speaker_idx + 1}"
            text = (getattr(u, "transcript", "") or "").strip()
            if text:
                segments.append({
                    "speaker": speaker,
                    "text": text,
                    "start_time": round(getattr(u, "start", 0) or 0, 2),
                    "end_time": round(getattr(u, "end", 0) or 0, 2),
                    "confidence": round(getattr(u, "confidence", 0.9) or 0.9, 3),
                })

        return self._merge_consecutive(segments)

    def _merge_consecutive(self, segments: List[dict]) -> List[dict]:
        """Merge consecutive same-speaker segments. Fix boundary fragments."""
        if not segments:
            return segments

        # First pass: fix fragments — if a segment is just 1-2 short words
        # and the next segment starts mid-sentence, merge forward
        for i in range(len(segments) - 1):
            curr = segments[i]
            nxt = segments[i + 1]
            curr_words = curr["text"].strip().split()
            nxt_words = nxt["text"].strip().split()

            # If current segment ends in a fragment (1-3 words) and
            # next segment starts lowercase (mid-sentence continuation),
            # merge current into next speaker
            if 0 < len(curr_words) <= 3 and nxt_words:
                first_nxt = nxt_words[0]
                if first_nxt and first_nxt[0].islower():
                    nxt["text"] = curr["text"] + " " + nxt["text"]
                    nxt["start_time"] = curr["start_time"]
                    nxt["confidence"] = round((curr["confidence"] + nxt["confidence"]) / 2, 3)
                    segments[i] = None  # mark for removal

        segments = [s for s in segments if s is not None]

        # Second pass: merge same speaker with gap < 3s
        if not segments:
            return segments

        merged = [dict(segments[0])]
        for seg in segments[1:]:
            last = merged[-1]
            gap = seg["start_time"] - last["end_time"]
            if seg["speaker"] == last["speaker"] and gap < 3.0:
                last["text"] = last["text"] + " " + seg["text"]
                last["end_time"] = seg["end_time"]
                last["confidence"] = round((last["confidence"] + seg["confidence"]) / 2, 3)
            else:
                merged.append(dict(seg))
        return merged
