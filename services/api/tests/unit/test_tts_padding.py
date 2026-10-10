"""Spoken prompts keep their first and last syllables when played in a browser."""

import io
import wave

from app.ai.providers.tts.base import LEAD_SILENCE_MS, TAIL_SILENCE_MS, pad_with_silence


def _wav(frames: bytes, *, rate: int = 22_050, width: int = 2) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(width)
        target.setframerate(rate)
        target.writeframes(frames)
    return output.getvalue()


def test_speech_gets_silence_before_and_after_without_losing_any_audio() -> None:
    speech = b"\x10\x27" * 22_050  # one second of loud 16-bit samples
    padded = pad_with_silence(_wav(speech))
    with wave.open(io.BytesIO(padded), "rb") as result:
        rate = result.getframerate()
        frames = result.readframes(result.getnframes())
    lead = rate * LEAD_SILENCE_MS // 1000 * 2
    tail = rate * TAIL_SILENCE_MS // 1000 * 2
    assert frames[:lead] == b"\x00" * lead
    assert frames[lead : lead + len(speech)] == speech
    assert frames[-tail:] == b"\x00" * tail
    assert len(frames) == lead + len(speech) + tail


def test_eight_bit_audio_is_padded_with_its_own_silence_value() -> None:
    padded = pad_with_silence(_wav(b"\xff" * 800, rate=8_000, width=1), lead_ms=10, tail_ms=10)
    with wave.open(io.BytesIO(padded), "rb") as result:
        frames = result.readframes(result.getnframes())
    assert frames[:80] == b"\x80" * 80 and frames[-80:] == b"\x80" * 80


def test_anything_that_is_not_pcm_wav_is_left_alone() -> None:
    mp3 = b"ID3\x04\x00\x00not really audio"
    assert pad_with_silence(mp3) == mp3
