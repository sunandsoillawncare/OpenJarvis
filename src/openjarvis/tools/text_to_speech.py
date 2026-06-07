"""Text-to-speech tool — synthesize text to audio via configurable TTS backend."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry, TTSRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("text_to_speech")
class TextToSpeechTool(BaseTool):
    """Synthesize text into spoken audio using a TTS backend."""

    tool_id = "text_to_speech"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="text_to_speech",
            description=(
                "Convert text to spoken audio. Returns the file path to the "
                "generated audio file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to synthesize into speech.",
                    },
                    "voice_id": {
                        "type": "string",
                        "description": "Voice identifier for the TTS backend.",
                    },
                    "backend": {
                        "type": "string",
                        "description": "TTS backend (cartesia, kokoro, openai_tts).",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Directory to save the audio file.",
                    },
                },
                "required": ["text"],
            },
            category="audio",
            timeout_seconds=120.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        # Ensure TTS backends are registered
        import openjarvis.speech  # noqa: F401

        text = params.get("text", "")
        voice_id = params.get("voice_id", "")
        output_dir = params.get("output_dir", "")
        speed = float(params.get("speed", 1.0))

        if not text:
            return ToolResult(
                tool_name="text_to_speech",
                content="No text provided.",
                success=False,
            )

        # Resolve the backend: explicit param > config default > first healthy
        _ALIASES = {"openai": "openai_tts"}
        requested = params.get("backend", "")
        if requested:
            backend_key = _ALIASES.get(requested, requested)
        else:
            from openjarvis.core.config import load_config
            backend_key = load_config().digest.tts_backend or "cartesia"
            backend_key = _ALIASES.get(backend_key, backend_key)

        if not TTSRegistry.contains(backend_key):
            return ToolResult(
                tool_name="text_to_speech",
                content=f"TTS backend '{backend_key}' not available.",
                success=False,
            )

        backend_cls = TTSRegistry.get(backend_key)
        backend = backend_cls()

        if not backend.health():
            return ToolResult(
                tool_name="text_to_speech",
                content=f"TTS backend '{backend_key}' is not healthy (missing API key or dependencies).",
                success=False,
            )

        synth_kwargs: dict[str, Any] = {"speed": speed}
        if voice_id:
            synth_kwargs["voice_id"] = voice_id
        result = backend.synthesize(text, **synth_kwargs)

        # Save to file
        if output_dir:
            out_dir = Path(output_dir)
        else:
            out_dir = Path(tempfile.mkdtemp(prefix="jarvis-tts-"))

        out_dir.mkdir(parents=True, exist_ok=True)
        ext = result.format or "mp3"
        audio_path = out_dir / f"digest.{ext}"
        result.save(audio_path)

        return ToolResult(
            tool_name="text_to_speech",
            content=str(audio_path),
            success=True,
            metadata={
                "audio_path": str(audio_path),
                "format": ext,
                "duration_seconds": result.duration_seconds,
                "voice_id": result.voice_id,
                "backend": backend_key,
            },
        )
