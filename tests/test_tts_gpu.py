"""Optional integration test of the hub's TTS client (T-016) against our Piper service (T-015).

Opt-in: runs only with PIOTR_VOICE_HUB_PIPER_URI set (for example tcp://127.0.0.1:10201) and
the service up on piotr's GPU; tools/piper-gpu-test.sh sets it.
"""

import asyncio
import os
import time

import pytest

from piotr_voice_hub.config import TtsConfig
from piotr_voice_hub.tts.interfaces import AudioFormat, TtsError, TtsTextError
from piotr_voice_hub.tts.wyoming_piper import MAX_TEXT_CHARS, WyomingPiperTts

URI = os.environ.get("PIOTR_VOICE_HUB_PIPER_URI", "")
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not URI, reason="PIOTR_VOICE_HUB_PIPER_URI is not set"),
]

TIMEOUT_SECONDS = 60.0
VOICE = "pl_PL-bass-high"
FORMAT = AudioFormat(22050, 2, 1)
SHORT = "Dobry wieczór, co mogę dla ciebie zrobić?"
LONG = (
    "W kuchni pali się jeszcze światło, a w salonie telewizor gra od godziny. Czy mam przypomnieć "
    "o wyłączeniu piekarnika, zanim pójdziesz spać, i nastawić budzik na jutro na wpół do siódmej "
    "rano?"
)


def at_the_cap() -> str:
    sentence = "To jest zdanie testowe numer jeden, o średniej długości. "
    text = (sentence * (MAX_TEXT_CHARS // len(sentence) + 1))[:MAX_TEXT_CHARS]
    return text[: text.rindex(".") + 1]


async def measure(tts: WyomingPiperTts, text: str) -> tuple[float, float, float]:
    """(first audio, total, audio seconds)."""
    start = time.monotonic()
    first = 0.0
    audio = 0
    async with tts.synthesize(text) as speech:
        assert speech.format == FORMAT
        async for pcm in speech:
            if not audio:
                first = time.monotonic() - start
            audio += len(pcm)
    return first, time.monotonic() - start, audio / (FORMAT.rate * FORMAT.width)


def test_client_against_the_service() -> None:
    async def run() -> None:
        tts = WyomingPiperTts(TtsConfig(uri=URI, voice=VOICE, volume=1.0))
        start = time.monotonic()
        await tts.start()
        print(f"\nstart (describe → info): {time.monotonic() - start:.3f} s")
        for name, text in (("short", SHORT), ("long", LONG), ("short", SHORT), ("long", LONG)):
            first, total, seconds = await measure(tts, text)
            print(
                f"{name} ({len(text)} chars): first chunk {first:.3f} s, total {total:.3f} s, "
                f"{seconds:.2f} s of audio"
            )
            assert seconds > len(text) / 40
        text = at_the_cap()
        first, total, seconds = await measure(tts, text)
        print(
            f"cap ({len(text)} chars): first chunk {first:.3f} s, total {total:.3f} s, "
            f"{seconds:.2f} s of audio"
        )
        with pytest.raises(TtsTextError):
            await measure(tts, "x" * (MAX_TEXT_CHARS + 1))
        wrong = WyomingPiperTts(TtsConfig(uri=URI, voice="pl_PL-gosia-medium", volume=1.0))
        with pytest.raises(TtsError, match=r"describes \['pl_PL-bass-high'\]"):
            await wrong.start()

    asyncio.run(asyncio.wait_for(run(), TIMEOUT_SECONDS))
