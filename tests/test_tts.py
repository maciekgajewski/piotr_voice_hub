"""Unit tests of the Wyoming Piper client (T-016) against a fake Wyoming server on loopback."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event, async_read_event, async_write_event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.tts import Synthesize

from piotr_voice_hub.config import TtsConfig
from piotr_voice_hub.tts.interfaces import AudioFormat, TtsError, TtsTextError
from piotr_voice_hub.tts.wyoming_piper import MAX_TEXT_CHARS, WyomingPiperTts, check_text

VOICE = "pl_PL-bass-high"
FORMAT = AudioFormat(22050, 2, 1)
TEST_TIMEOUT = 5.0
SHORT_BOUND = 0.2

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


class FakePiper:
    """One handler per connection; records each connection's first event."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[Event] = []
        self.closed = asyncio.Event()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            event = await async_read_event(reader)
            assert event is not None
            self.requests.append(event)
            await self.handler(reader, writer)
            # Wait for the client to close its side.
            await reader.read()
        finally:
            writer.close()
            self.closed.set()


async def run_with(
    handler: Handler, test: Callable[[WyomingPiperTts, FakePiper], Awaitable[None]], **bounds: float
) -> FakePiper:
    fake = FakePiper(handler)
    server = await asyncio.start_server(fake._serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    tts = WyomingPiperTts(
        TtsConfig(uri=f"tcp://127.0.0.1:{port}", voice=VOICE, volume=1.0), **bounds
    )
    try:
        async with asyncio.timeout(TEST_TIMEOUT):
            await test(tts, fake)
    finally:
        server.close()
        await server.wait_closed()
    return fake


def run(
    handler: Handler, test: Callable[[WyomingPiperTts, FakePiper], Awaitable[None]], **bounds: float
) -> FakePiper:
    return asyncio.run(run_with(handler, test, **bounds))


def info_event(voices: list[tuple[str, bool]]) -> Event:
    attribution = Attribution(name="test", url="")
    tts_voices = [
        TtsVoice(
            name=n,
            attribution=attribution,
            installed=i,
            description=n,
            version=None,
            languages=["pl_PL"],
        )
        for n, i in voices
    ]
    program = TtsProgram(
        name="piper",
        attribution=attribution,
        installed=True,
        description="",
        version=None,
        voices=tts_voices,
    )
    return Info(tts=[program]).event()


def chunk(pcm: bytes, audio_format: AudioFormat = FORMAT) -> Event:
    return AudioChunk(audio_format.rate, audio_format.width, audio_format.channels, pcm).event()


START = AudioStart(FORMAT.rate, FORMAT.width, FORMAT.channels).event()


def sends(*events: Event) -> Handler:
    async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        for event in events:
            await async_write_event(event, writer)

    return handler


def sends_then_closes(*events: Event) -> Handler:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        for event in events:
            await async_write_event(event, writer)
        writer.close()

    return handler


async def collect(tts: WyomingPiperTts, text: str) -> tuple[AudioFormat, list[bytes]]:
    async with tts.synthesize(text) as speech:
        return speech.format, [pcm async for pcm in speech]


# --- start(): the startup reachability check ---------------------------------------------


def test_start_ok_sends_describe(caplog: pytest.LogCaptureFixture) -> None:
    async def test(tts: WyomingPiperTts, _fake: FakePiper) -> None:
        with caplog.at_level(logging.INFO):
            await tts.start()

    fake = run(sends(info_event([("other", True), (VOICE, True)])), test)
    assert [e.type for e in fake.requests] == [Describe().event().type]
    assert "Piper reachable" in caplog.text


@pytest.mark.parametrize(
    ("voices", "message"),
    [
        ([("other", True)], "does not list voice"),
        ([(VOICE, False)], "does not list voice"),
        ([], "does not list voice"),
    ],
)
def test_start_fails_without_the_installed_voice(
    voices: list[tuple[str, bool]], message: str
) -> None:
    async def test(tts: WyomingPiperTts, _fake: FakePiper) -> None:
        with pytest.raises(TtsError, match=message):
            await tts.start()

    run(sends(info_event(voices)), test)


def test_start_fails_without_a_tts_program() -> None:
    async def test(tts: WyomingPiperTts, _fake: FakePiper) -> None:
        with pytest.raises(TtsError, match="does not list voice"):
            await tts.start()

    run(sends(Info().event()), test)


def test_start_fails_on_no_answer() -> None:
    async def test(tts: WyomingPiperTts, _fake: FakePiper) -> None:
        with pytest.raises(TtsError, match="no info within 0.2 s"):
            await tts.start()

    run(sends(), test, info_timeout=SHORT_BOUND)


def test_start_fails_on_close_and_other_events() -> None:
    async def closes(tts: WyomingPiperTts, _fake: FakePiper) -> None:
        with pytest.raises(TtsError, match="connection closed"):
            await tts.start()

    async def other(tts: WyomingPiperTts, _fake: FakePiper) -> None:
        with pytest.raises(TtsError, match="unexpected audio-start event"):
            await tts.start()

    run(sends_then_closes(), closes)
    run(sends(START), other)


def test_start_fails_on_refused_connection() -> None:
    async def test() -> None:
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        tts = WyomingPiperTts(TtsConfig(uri=f"tcp://127.0.0.1:{port}", voice=VOICE, volume=1.0))
        with pytest.raises(TtsError, match=f"connect to 127.0.0.1:{port} failed"):
            await tts.start()

    asyncio.run(test())


def test_connect_timeout() -> None:
    # 192.0.2.0/24 is TEST-NET-1: never routed, so the connect hangs (or fails at once where
    # there is no route; both are a TtsError naming the connect).
    tts = WyomingPiperTts(
        TtsConfig(uri="tcp://192.0.2.1:10201", voice=VOICE, volume=1.0), connect_timeout=SHORT_BOUND
    )
    with pytest.raises(TtsError, match="connect to 192.0.2.1:10201"):
        asyncio.run(tts.start())


# --- synthesize() ----------------------------------------------------------------------


def test_synthesize_streams_format_and_chunks(caplog: pytest.LogCaptureFixture) -> None:
    pcm = [b"\x01\x00" * 1024, b"\x02\x00" * 1024, b"\x03\x00" * 10]

    async def test(tts: WyomingPiperTts, fake: FakePiper) -> None:
        with caplog.at_level(logging.INFO):
            audio_format, chunks = await collect(tts, "  Dzień dobry.\n")
        assert audio_format == FORMAT
        assert chunks == pcm
        await fake.closed.wait()

    fake = run(sends(START, *(chunk(p) for p in pcm), AudioStop().event()), test)
    request = Synthesize.from_event(fake.requests[0])
    assert request.text == "Dzień dobry."
    assert request.voice is not None and request.voice.name == VOICE
    assert "synthesized 12 characters: 0.09 s of audio" in caplog.text


def test_synthesize_with_no_chunks() -> None:
    async def test(tts: WyomingPiperTts, _fake: FakePiper) -> None:
        assert await collect(tts, "x") == (FORMAT, [])

    run(sends(START, AudioStop().event()), test)


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([Error(text="boom", code="x").event()], r"service error: boom \(code x\)"),
        ([START, chunk(b"\0\0"), Error(text="late").event()], "service error: late"),
        ([START, chunk(b"\0\0")], "connection closed"),
        ([], "connection closed"),
        ([AudioStop().event()], "expected audio-start, got audio-stop"),
        ([chunk(b"\0\0")], "expected audio-start, got audio-chunk"),
        ([START, chunk(b"\0\0", AudioFormat(16000, 2, 1))], "differs from the start"),
        ([START, Event(type="transcript", data={"text": "?"})], "unexpected transcript event"),
        ([START, Event(type="audio-chunk", data={"rate": 22050})], "malformed audio-chunk event"),
        ([Event(type="audio-start", data={})], "malformed audio-start event"),
        ([AudioStart(0, 2, 1).event()], "invalid audio format"),
    ],
)
def test_synthesize_fails_loudly(events: list[Event], message: str) -> None:
    async def test(tts: WyomingPiperTts, fake: FakePiper) -> None:
        with pytest.raises(TtsError, match=message):
            await collect(tts, "Dzień dobry.")
        await fake.closed.wait()

    run(sends_then_closes(*events), test)


def test_synthesize_bounds_the_first_audio() -> None:
    async def test(tts: WyomingPiperTts, fake: FakePiper) -> None:
        with pytest.raises(TtsError, match="no audio-start within 0.2 s"):
            await collect(tts, "Dzień dobry.")
        await fake.closed.wait()

    run(sends(), test, first_audio_timeout=SHORT_BOUND)


def test_synthesize_bounds_the_gap_between_chunks() -> None:
    async def test(tts: WyomingPiperTts, fake: FakePiper) -> None:
        received: list[bytes] = []
        with pytest.raises(TtsError, match="no next audio event within 0.2 s"):
            async with tts.synthesize("Dzień dobry.") as speech:
                async for pcm in speech:
                    received.append(pcm)
        assert received == [b"\0\0"]
        await fake.closed.wait()

    run(sends(START, chunk(b"\0\0")), test, next_event_timeout=SHORT_BOUND)


def test_leaving_early_closes_the_connection() -> None:
    async def test(tts: WyomingPiperTts, fake: FakePiper) -> None:
        async with tts.synthesize("Dzień dobry.") as speech:
            async for _pcm in speech:
                break
        await fake.closed.wait()

    run(sends(START, chunk(b"\0\0"), chunk(b"\0\0")), test)


def test_consumer_failure_closes_the_connection() -> None:
    async def test(tts: WyomingPiperTts, fake: FakePiper) -> None:
        with pytest.raises(LookupError):
            async with tts.synthesize("Dzień dobry."):
                raise LookupError
        await fake.closed.wait()

    run(sends(START), test)


# --- the text cap ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty text"),
        (" \n\t", "empty text"),
        ("x" * (MAX_TEXT_CHARS + 1), "text is 1001 characters, the limit is 1000"),
    ],
)
def test_rejected_text_never_connects(text: str, message: str) -> None:
    async def test(tts: WyomingPiperTts, fake: FakePiper) -> None:
        with pytest.raises(TtsTextError, match=message):
            await collect(tts, text)
        await asyncio.sleep(0.05)
        assert fake.requests == []

    run(sends(), test)


def test_text_at_the_cap_is_accepted() -> None:
    assert check_text(" " + "x" * MAX_TEXT_CHARS + " ") == "x" * MAX_TEXT_CHARS
    assert issubclass(TtsTextError, TtsError)


def test_invalid_uri_is_rejected() -> None:
    with pytest.raises(ValueError, match="expected tcp://host:port"):
        WyomingPiperTts(TtsConfig(uri="tcp://host", voice=VOICE, volume=1.0))
