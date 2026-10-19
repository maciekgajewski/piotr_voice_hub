"""Optional end-to-end test of `speak` (T-017) against our Piper service (T-015) on piotr's GPU.

The real API, command router, session, TTS client, ESPHome driver and playback HTTP server; a
fake satellite (the driver tests' fake aioesphomeapi client, whose media player fetches the
announced URL over loopback) and a fake STT. Opt-in: runs only with PIOTR_VOICE_HUB_PIPER_URI
set; tools/piper-gpu-test.sh sets it. Never touches a real satellite.
"""

import asyncio
import json
import os
import time
from typing import Any

import pytest
from test_esphome_driver import Clients, config, loud, make, silence
from test_session import FakeSttSession
from websockets.asyncio.client import ClientConnection, connect

from piotr_voice_hub.config import TtsConfig
from piotr_voice_hub.hub.api import ApiServer
from piotr_voice_hub.hub.commands import CommandRouter
from piotr_voice_hub.hub.playback_http import WAV_HEADER_BYTES, PlaybackHttpServer, wav_header
from piotr_voice_hub.hub.session import SatelliteSession
from piotr_voice_hub.tts.wyoming_piper import WyomingPiperTts

URI = os.environ.get("PIOTR_VOICE_HUB_PIPER_URI", "")
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not URI, reason="PIOTR_VOICE_HUB_PIPER_URI is not set"),
]

TIMEOUT_SECONDS = 60.0
VOICE = "pl_PL-bass-high"
REPLY = "Jest piąta po południu, a na zewnątrz świeci słońce."
OK_HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nConnection: close\r\n\r\n"


class AnyUtteranceStt:
    """Every segment is the accepted wake phrase."""

    def create_streaming_session(self, session_id: str, max_audio_seconds: float) -> Any:
        return FakeSttSession([], "Ryszardzie, która godzina?", 0.0)


async def next_of(client: ClientConnection, kind: str) -> dict[str, Any]:
    while (message := json.loads(await client.recv()))["type"] != kind:
        pass
    return message


def test_speak_through_the_api_plays_piper_audio_on_a_fake_satellite() -> None:
    async def run() -> None:
        playback = PlaybackHttpServer("127.0.0.1", 0)
        api = ApiServer("127.0.0.1", 0)
        satellite = config()
        microphone, clients = make(cfg=satellite, playback_server=playback)
        tts = WyomingPiperTts(TtsConfig(uri=URI, voice=VOICE, volume=0.6))
        await tts.start()
        # The raw driver: the session validates the protocol itself.
        session = SatelliteSession(
            satellite, microphone.driver, AnyUtteranceStt(), api.publish, tts=tts
        )
        api.set_command_handler(CommandRouter({"kitchen": session}, 0.6).handle)
        await playback.start()
        await api.start()
        task = asyncio.create_task(session.run())
        try:
            async with connect(f"ws://127.0.0.1:{api.port}/ws") as client:
                await speak_once(client, clients)
        finally:
            task.cancel()
            await asyncio.wait({task})
            await session.shutdown()
            await api.close()
            await playback.close()

    asyncio.run(asyncio.wait_for(run(), TIMEOUT_SECONDS))


async def speak_once(client: ClientConnection, clients: Clients) -> None:
    while (await next_of(client, "satellite"))["data"]["state"] != "idle":
        pass
    await clients.last.audio(silence(0.5) + loud(0.6) + silence(1.0))
    final = await next_of(client, "transcript_final")
    assert final["data"]["text"] == "która godzina"
    command = {"type": "speak", "id": "c1", "satellite": "kitchen", "data": {"text": REPLY}}
    await client.send(json.dumps(command))
    start = time.monotonic()
    ack = await next_of(client, "ack")
    playback_id = ack["data"]["playback_id"]
    assert ack == {
        "type": "ack",
        "reply_to": "c1",
        "satellite": "kitchen",
        "data": {"playback_id": playback_id},
    }
    states = []
    while (message := json.loads(await client.recv()))["type"] != "playback_finished":
        if message["type"] == "satellite":
            states.append(message["data"]["state"])
    finished = time.monotonic() - start
    assert message["data"] == {"playback_id": playback_id, "listening_for_follow_up": False}
    while (await next_of(client, "satellite"))["data"]["state"] != "idle":
        pass
    assert states == ["speaking"]
    device = clients.last
    assert device.media_commands[0] == {"volume": 0.6}
    assert device.media_commands[1]["media_url"].endswith(f"/{playback_id}.wav")
    body = device.fetched[0]
    assert body.startswith(OK_HEAD + wav_header(22050, 2, 1))
    audio_seconds = (len(body) - len(OK_HEAD) - WAV_HEADER_BYTES) / 44100
    print(f"\nspeak: {audio_seconds:.2f} s of audio, ack -> playback_finished {finished:.2f} s")
    assert 2.0 < audio_seconds < 10.0
    assert audio_seconds < finished < audio_seconds + 3.0
