"""Assist satellite for Auvra: the SmartGhar integration as the Wyoming client.

Adapted from Home Assistant core, homeassistant/components/wyoming/assist_satellite.py
(Copyright the Home Assistant Authors, Apache License 2.0; see NOTICE in the
repository root). Changes from the original:

  - the satellite is a CoordinatorEntity on the existing Auvra device instead of
    a device of its own, and takes its host and port from the SmartGhar entry;
  - mute is the unit's own `mic_mute` (a switch in this integration), so the
    Wyoming link is never paused and there is no SatelliteDevice bookkeeping;
  - noise suppression / auto gain / mic volume are not ported (nothing in
    HA 2026.10.4 reads them);
  - the connect loop is gated: it does not run while a stock Wyoming config
    entry still drives the same unit (two clients cannot share one satellite).

The Wyoming event flow was verified against HA 2026.10.4 and wyoming 1.10.2 in
smartghar-airplay-speaker docs/notes/voice-progress.md.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
import wave
from collections.abc import AsyncGenerator
from typing import Any, Final

from homeassistant.components import assist_pipeline, ffmpeg, intent, tts
from homeassistant.components.assist_pipeline import PipelineEvent
from homeassistant.components.assist_satellite import (
    AssistSatelliteAnnouncement,
    AssistSatelliteConfiguration,
    AssistSatelliteEntity,
    AssistSatelliteEntityDescription,
    AssistSatelliteEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.ulid import ulid_now
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.ping import Ping, Pong
from wyoming.pipeline import PipelineStage, RunPipeline
from wyoming.satellite import RunSatellite
from wyoming.snd import Played
from wyoming.timer import TimerCancelled, TimerFinished, TimerStarted, TimerUpdated
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.vad import VoiceStarted, VoiceStopped
from wyoming.wake import Detect, Detection

from .const import DOMAIN
from .coordinator import SmartGharCoordinator
from .device_info import hub_device_info
from .voice_logic import voice_port, voice_supported
from .voice_migration import async_sync_stock_wyoming
from .voice_options import voice_satellite_enabled

_LOGGER = logging.getLogger(__name__)

SAMPLE_WIDTH: Final = 2
SAMPLE_CHANNELS: Final = 1

_SAMPLES_PER_CHUNK: Final = 1024
_RECONNECT_SECONDS: Final = 10
_RESTART_SECONDS: Final = 3
_PING_TIMEOUT: Final = 5
_PING_SEND_DELAY: Final = 2
_TTS_SAMPLE_RATE: Final = 22050
_AUDIO_CHUNK_BYTES: Final = 2048  # 1024 samples
_TTS_TIMEOUT_EXTRA: Final = 1.0

# Wyoming stage -> Assist stage
_STAGES: dict[PipelineStage, assist_pipeline.PipelineStage] = {
    PipelineStage.WAKE: assist_pipeline.PipelineStage.WAKE_WORD,
    PipelineStage.ASR: assist_pipeline.PipelineStage.STT,
    PipelineStage.HANDLE: assist_pipeline.PipelineStage.INTENT,
    PipelineStage.TTS: assist_pipeline.PipelineStage.TTS,
}


def voice_prefix(hub_id: str) -> str:
    """unique-id prefix shared by the satellite and its pipeline / VAD selects."""
    return f"smartghar_{hub_id}_voice"


def pipeline_changed_signal(hub_id: str) -> str:
    return f"{DOMAIN}_voice_pipeline_changed_{hub_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the satellite to an Auvra that reports voice (and option allows it)."""
    coordinator: SmartGharCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not voice_supported(coordinator.voice) or not voice_satellite_enabled(entry):
        return
    async_add_entities([SmartGharAssistSatellite(hass, entry, coordinator)])


class SmartGharAssistSatellite(
    CoordinatorEntity[SmartGharCoordinator], AssistSatelliteEntity
):
    """Assist satellite for an Auvra, on the Auvra's own device."""

    entity_description = AssistSatelliteEntityDescription(key="assist_satellite")
    _attr_has_entity_name = True
    _attr_translation_key = "assist_satellite"
    _attr_name = None
    _attr_supported_features = AssistSatelliteEntityFeature.ANNOUNCE

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: SmartGharCoordinator,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        AssistSatelliteEntity.__init__(self)
        self._entry = entry
        self._prefix = voice_prefix(coordinator.hub_id)
        self._attr_unique_id = f"{self._prefix}_satellite"

        self.is_running = False
        self._run_task: asyncio.Task | None = None
        self._conflict = False

        self._client: AsyncTcpClient | None = None
        self._chunk_converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self._is_pipeline_running = False
        self._pipeline_error = False
        self._pipeline_ended_event = asyncio.Event()
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        # For announcements
        self._ffmpeg_manager: ffmpeg.FFmpegManager | None = None
        self._played_event_received: asyncio.Event | None = None

        # Randomly set on each pipeline loop run; makes the TTS timeout
        # act only on the run it was started for.
        self._run_loop_id: str | None = None

        # TTS streaming
        self._tts_stream_token: str | None = None
        self._is_tts_streaming = False

    # ------------------------------------------------------------ properties

    @property
    def device_info(self):
        return hub_device_info(self.coordinator)

    @property
    def available(self) -> bool:
        voice = self.coordinator.voice
        return (
            super().available
            and not self._conflict
            and bool(voice.get("enabled"))
        )

    @property
    def _host(self) -> str:
        return self.coordinator.client.host

    @property
    def _port(self) -> int:
        return voice_port(self.coordinator.voice)

    def _lookup(self, platform: str, suffix: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            platform, DOMAIN, f"{self._prefix}-{suffix}"
        )

    @property
    def pipeline_entity_id(self) -> str | None:
        """Entity ID of the pipeline select (made by this integration)."""
        return self._lookup("select", "pipeline")

    @property
    def vad_sensitivity_entity_id(self) -> str | None:
        return self._lookup("select", "vad_sensitivity")

    @property
    def tts_options(self) -> dict[str, Any] | None:
        return {
            tts.ATTR_PREFERRED_FORMAT: "wav",
            tts.ATTR_PREFERRED_SAMPLE_RATE: _TTS_SAMPLE_RATE,
            tts.ATTR_PREFERRED_SAMPLE_CHANNELS: SAMPLE_CHANNELS,
            tts.ATTR_PREFERRED_SAMPLE_BYTES: SAMPLE_WIDTH,
        }

    # ------------------------------------------------------------ lifecycle

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                pipeline_changed_signal(self.coordinator.hub_id),
                self._pipeline_changed,
            )
        )
        self._sync_conflict()
        if not self._conflict:
            self.start_satellite()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self.stop_satellite()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_conflict()
        super()._handle_coordinator_update()

    @callback
    def _sync_conflict(self) -> None:
        """Gate the connection on there being no stock Wyoming client."""
        conflict = async_sync_stock_wyoming(
            self.hass,
            self._entry,
            self.coordinator.client.host,
            self._port,
            self.coordinator.info.get("hub_name") or "",
        )
        was = self._conflict
        self._conflict = conflict
        if conflict and not was:
            _LOGGER.warning(
                "A stock Wyoming entry also drives %s; the SmartGhar satellite is "
                "paused until it is removed (see Repairs)", self._host,
            )
            self.stop_satellite()
        elif was and not conflict and self.hass is not None and self.entity_id:
            _LOGGER.info("Stock Wyoming entry gone; starting the satellite")
            self.start_satellite()

    @callback
    def async_get_configuration(self) -> AssistSatelliteConfiguration:
        raise NotImplementedError

    async def async_set_configuration(self, config: AssistSatelliteConfiguration) -> None:
        raise NotImplementedError

    def start_satellite(self) -> None:
        """Start the satellite task (once)."""
        self.is_running = True
        if self._run_task is not None and not self._run_task.done():
            return
        self._run_task = self._entry.async_create_background_task(
            self.hass, self.run(), "smartghar assist satellite run"
        )

    def stop_satellite(self) -> None:
        """Signal the satellite task to stop."""
        self._audio_queue.put_nowait(None)
        self.is_running = False

    # --------------------------------------------------- pipeline -> satellite

    @callback
    def on_pipeline_event(self, event: PipelineEvent) -> None:
        """Mirror pipeline progress to the satellite."""
        if event.type == assist_pipeline.PipelineEventType.RUN_END:
            self._is_pipeline_running = False
            self._pipeline_ended_event.set()
            self._tts_stream_token = None
            self._is_tts_streaming = False

        if self._client is None:
            return

        def send(wyoming_event: Event) -> None:
            self._entry.async_create_background_task(
                self.hass,
                self._client.write_event(wyoming_event),  # type: ignore[union-attr]
                f"{self.entity_id} {event.type}",
            )

        data = event.data
        etype = event.type
        types = assist_pipeline.PipelineEventType
        if etype == types.RUN_START:
            if data and (tts_output := data.get("tts_output")):
                self._tts_stream_token = tts_output["token"]
                self._is_tts_streaming = False
        elif etype == types.WAKE_WORD_START:
            send(Detect().event())
        elif etype == types.WAKE_WORD_END:
            if data and (wake_word_output := data.get("wake_word_output")):
                send(
                    Detection(
                        name=wake_word_output["wake_word_id"],
                        timestamp=wake_word_output.get("timestamp"),
                    ).event()
                )
        elif etype == types.STT_START:
            if data:
                send(Transcribe(language=data["metadata"]["language"]).event())
        elif etype == types.STT_VAD_START:
            if data:
                send(VoiceStarted(timestamp=data["timestamp"]).event())
        elif etype == types.STT_VAD_END:
            if data:
                send(VoiceStopped(timestamp=data["timestamp"]).event())
        elif etype == types.STT_END:
            if data:
                send(Transcript(text=data["stt_output"]["text"]).event())
        elif etype == types.INTENT_PROGRESS:
            if (
                data
                and data.get("tts_start_streaming")
                and self._tts_stream_token
                and (stream := tts.async_get_stream(self.hass, self._tts_stream_token))
            ):
                # Start streaming TTS early (before TTS_END).
                self._is_tts_streaming = True
                self._entry.async_create_background_task(
                    self.hass, self._stream_tts(stream), f"{self.entity_id} {etype}"
                )
        elif etype == types.TTS_START:
            if data:
                send(
                    Synthesize(
                        text=data["tts_input"],
                        voice=SynthesizeVoice(
                            name=data.get("voice"), language=data.get("language")
                        ),
                    ).event()
                )
        elif etype == types.TTS_END:
            if (
                data
                and (tts_output := data["tts_output"])
                and not self._is_tts_streaming
                and (stream := tts.async_get_stream(self.hass, tts_output["token"]))
            ):
                self._entry.async_create_background_task(
                    self.hass, self._stream_tts(stream), f"{self.entity_id} {etype}"
                )
        elif etype == types.ERROR:
            # Prevents an "always on" satellite restarting a failing pipeline
            # in a tight loop.
            self._pipeline_error = True
            if data:
                send(Error(text=data["message"], code=data["code"]).event())

    async def async_announce(self, announcement: AssistSatelliteAnnouncement) -> None:
        """Announce media on the satellite; blocks until it has played."""
        if self._client is None:
            raise ConnectionError("Satellite is not connected")

        if self._ffmpeg_manager is None:
            self._ffmpeg_manager = ffmpeg.get_ffmpeg_manager(self.hass)

        if self._played_event_received is None:
            self._played_event_received = asyncio.Event()

        self._played_event_received.clear()
        await self._client.write_event(
            AudioStart(
                rate=_TTS_SAMPLE_RATE,
                width=SAMPLE_WIDTH,
                channels=SAMPLE_CHANNELS,
                timestamp=0,
            ).event()
        )

        timestamp = 0
        try:
            proc = await asyncio.create_subprocess_exec(
                self._ffmpeg_manager.binary,
                "-protocol_whitelist", "http,https,file,tcp,tls",
                "-i", announcement.media_id,
                "-f", "s16le",
                "-ac", str(SAMPLE_CHANNELS),
                "-ar", str(_TTS_SAMPLE_RATE),
                "-nostats", "pipe:",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                close_fds=False,
            )
            assert proc.stdout is not None
            while True:
                chunk_bytes = await proc.stdout.read(_AUDIO_CHUNK_BYTES)
                if not chunk_bytes:
                    break
                chunk = AudioChunk(
                    rate=_TTS_SAMPLE_RATE,
                    width=SAMPLE_WIDTH,
                    channels=SAMPLE_CHANNELS,
                    audio=chunk_bytes,
                    timestamp=timestamp,
                )
                await self._client.write_event(chunk.event())
                timestamp += chunk.milliseconds
        finally:
            await self._client.write_event(AudioStop().event())
            if timestamp > 0:
                audio_seconds = timestamp / 1000
                try:
                    async with asyncio.timeout(audio_seconds + 0.5):
                        await self._played_event_received.wait()
                except TimeoutError:
                    _LOGGER.debug("Did not receive played event for announcement")

    # --------------------------------------------------------- connection loop

    async def run(self) -> None:
        """Run and maintain a connection to the satellite."""
        _LOGGER.debug("Running satellite task")
        unregister_timer_handler = self._register_timer_handler()
        try:
            while self.is_running:
                try:
                    await self._connect_and_loop()
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("%s: %s", err.__class__.__name__, str(err))
                    self._audio_queue.put_nowait(None)
                    await self._disconnect()
                    await self.on_restart()
        finally:
            unregister_timer_handler()
            await self._disconnect()
            _LOGGER.debug("Satellite task stopped")

    def _register_timer_handler(self):
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.coordinator.hub_id)}
        )
        if device is None:
            return lambda: None
        return intent.async_register_timer_handler(
            self.hass, device.id, self._handle_timer
        )

    async def on_restart(self) -> None:
        await asyncio.sleep(_RESTART_SECONDS)

    async def on_reconnect(self) -> None:
        await asyncio.sleep(_RECONNECT_SECONDS)

    @callback
    def _pipeline_changed(self) -> None:
        """The pipeline or VAD select changed: stop any run in progress."""
        self._audio_queue.put_nowait(None)

    async def _connect_and_loop(self) -> None:
        """Connect to the satellite and run pipelines until an error occurs."""
        while self.is_running:
            try:
                await self._connect()
                break
            except (ConnectionError, OSError):
                self._client = None
                await self.on_reconnect()

        if self._client is None or not self.is_running:
            return

        _LOGGER.debug("Connected to satellite")
        await self._client.write_event(RunSatellite().event())
        while self.is_running:
            await self._run_pipeline_loop()

    async def _run_pipeline_loop(self) -> None:
        """Run a pipeline one or more times."""
        assert self._client is not None
        client_info: Info | None = None
        wake_word_phrase: str | None = None
        run_pipeline: RunPipeline | None = None
        send_ping = True
        self._run_loop_id = ulid_now()

        pipeline_ended_task = self._entry.async_create_background_task(
            self.hass, self._pipeline_ended_event.wait(), "satellite pipeline ended"
        )
        client_event_task = self._entry.async_create_background_task(
            self.hass, self._client.read_event(), "satellite event read"
        )
        pending = {pipeline_ended_task, client_event_task}

        # Update info from the satellite
        await self._client.write_event(Describe().event())

        while self.is_running:
            if send_ping:
                send_ping = False
                self._entry.async_create_background_task(
                    self.hass, self._send_delayed_ping(), "ping satellite"
                )

            async with asyncio.timeout(_PING_TIMEOUT):
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )

                if pipeline_ended_task in done:
                    _LOGGER.debug("Pipeline finished")
                    self._pipeline_ended_event.clear()
                    pipeline_ended_task = self._entry.async_create_background_task(
                        self.hass,
                        self._pipeline_ended_event.wait(),
                        "satellite pipeline ended",
                    )
                    pending.add(pipeline_ended_task)
                    wake_word_phrase = None

                    if (
                        run_pipeline is not None
                        and run_pipeline.restart_on_end
                        and not self._pipeline_error
                    ):
                        self._run_pipeline_once(run_pipeline)
                        continue

                if client_event_task not in done:
                    continue

                client_event = client_event_task.result()
                if client_event is None:
                    raise ConnectionResetError("Satellite disconnected")

                if Pong.is_type(client_event.type):
                    send_ping = True
                elif Ping.is_type(client_event.type):
                    ping = Ping.from_event(client_event)
                    await self._client.write_event(Pong(text=ping.text).event())
                elif RunPipeline.is_type(client_event.type):
                    run_pipeline = RunPipeline.from_event(client_event)
                    self._run_pipeline_once(run_pipeline, wake_word_phrase)
                elif AudioChunk.is_type(client_event.type) and (
                    self._is_pipeline_running or wake_word_phrase is not None
                ):
                    if not self._is_pipeline_running:
                        _LOGGER.debug(
                            "Audio after detection without RunPipeline; "
                            "starting a pipeline automatically"
                        )
                        self._run_pipeline_once(
                            RunPipeline(
                                start_stage=PipelineStage.ASR,
                                end_stage=PipelineStage.TTS,
                            ),
                            wake_word_phrase,
                        )
                    chunk = AudioChunk.from_event(client_event)
                    chunk = self._chunk_converter.convert(chunk)
                    self._audio_queue.put_nowait(chunk.audio)
                elif AudioStop.is_type(client_event.type) and self._is_pipeline_running:
                    _LOGGER.debug("Client requested pipeline to stop")
                    self._audio_queue.put_nowait(None)
                elif Info.is_type(client_event.type):
                    client_info = Info.from_event(client_event)
                elif Detection.is_type(client_event.type):
                    detection = Detection.from_event(client_event)
                    wake_word_phrase = detection.name
                    if client_info is not None and client_info.wake is not None:
                        for wake_service in client_info.wake:
                            match = next(
                                (m for m in wake_service.models if m.name == detection.name),
                                None,
                            )
                            if match is not None:
                                wake_word_phrase = match.phrase or match.name
                                break
                elif Played.is_type(client_event.type):
                    self.tts_response_finished()
                    if self._played_event_received is not None:
                        self._played_event_received.set()
                else:
                    _LOGGER.debug("Unexpected event from satellite: %s", client_event)

                client_event_task = self._entry.async_create_background_task(
                    self.hass, self._client.read_event(), "satellite event read"
                )
                pending.add(client_event_task)

    def _run_pipeline_once(
        self, run_pipeline: RunPipeline, wake_word_phrase: str | None = None
    ) -> None:
        """Run a pipeline once."""
        start_stage = _STAGES.get(run_pipeline.start_stage)
        end_stage = _STAGES.get(run_pipeline.end_stage)
        if start_stage is None:
            raise ValueError(f"Invalid start stage: {run_pipeline.start_stage}")
        if end_stage is None:
            raise ValueError(f"Invalid end stage: {run_pipeline.end_stage}")

        # Audio is pushed in through a queue
        self._audio_queue = asyncio.Queue()
        self._is_pipeline_running = True
        self._pipeline_error = False
        self._pipeline_ended_event.clear()
        self._entry.async_create_background_task(
            self.hass,
            self.async_accept_pipeline_from_satellite(
                audio_stream=self._stt_stream(),
                start_stage=start_stage,
                end_stage=end_stage,
                wake_word_phrase=wake_word_phrase,
            ),
            "smartghar satellite pipeline",
        )

    async def _send_delayed_ping(self) -> None:
        try:
            await asyncio.sleep(_PING_SEND_DELAY)
            if self._client is None:
                return
            await self._client.write_event(Ping().event())
        except ConnectionError:
            pass  # handled by the read timeout

    async def _connect(self) -> None:
        await self._disconnect()
        _LOGGER.debug("Connecting to satellite at %s:%s", self._host, self._port)
        self._client = AsyncTcpClient(self._host, self._port)
        await self._client.connect()

    async def _disconnect(self) -> None:
        if self._client is None:
            return
        client, self._client = self._client, None
        with contextlib.suppress(ConnectionError, OSError):
            await client.disconnect()

    async def _stream_tts(self, tts_result: tts.ResultStream) -> None:
        """Stream TTS WAV audio to the satellite in chunks."""
        client = self._client
        if client is None:
            return
        if tts_result.extension != "wav":
            raise ValueError(f"Cannot stream audio format to satellite: {tts_result.extension}")

        total_seconds = 0.0
        start_time = time.monotonic()
        try:
            header_data = b""
            header_complete = False
            sample_rate = sample_width = sample_channels = None
            timestamp = 0

            async for data_chunk in tts_result.async_stream_result():
                if not header_complete:
                    header_data += data_chunk
                    if len(header_data) >= 44 and (
                        audio_info := _try_parse_wav_header(header_data)
                    ):
                        sample_rate, sample_width, sample_channels, data_chunk = audio_info
                        await client.write_event(
                            AudioStart(
                                rate=sample_rate,
                                width=sample_width,
                                channels=sample_channels,
                                timestamp=timestamp,
                            ).event()
                        )
                        header_complete = True
                        if not data_chunk:
                            continue
                    else:
                        continue

                assert sample_rate is not None
                assert sample_width is not None
                assert sample_channels is not None
                idx = 0
                while idx < len(data_chunk):
                    audio_chunk = AudioChunk(
                        rate=sample_rate,
                        width=sample_width,
                        channels=sample_channels,
                        audio=data_chunk[idx : idx + _AUDIO_CHUNK_BYTES],
                        timestamp=timestamp,
                    )
                    await client.write_event(audio_chunk.event())
                    timestamp += audio_chunk.milliseconds
                    total_seconds += audio_chunk.seconds
                    idx += _AUDIO_CHUNK_BYTES

            await client.write_event(AudioStop(timestamp=timestamp).event())
        finally:
            send_duration = time.monotonic() - start_time
            timeout_seconds = max(0, total_seconds - send_duration + _TTS_TIMEOUT_EXTRA)
            self._entry.async_create_background_task(
                self.hass,
                self._tts_timeout(timeout_seconds, self._run_loop_id),
                name="smartghar TTS timeout",
            )

    async def _stt_stream(self) -> AsyncGenerator[bytes]:
        """Yield audio chunks from the queue."""
        while chunk := await self._audio_queue.get():
            yield chunk

    async def _tts_timeout(self, timeout_seconds: float, run_loop_id: str | None) -> None:
        """Force the idle state if the `played` event never arrives."""
        await asyncio.sleep(timeout_seconds + _TTS_TIMEOUT_EXTRA)
        if run_loop_id != self._run_loop_id:
            return
        self.tts_response_finished()

    @callback
    def _handle_timer(
        self, event_type: intent.TimerEventType, timer: intent.TimerInfo
    ) -> None:
        """Forward timer events to the satellite."""
        if self._client is None:
            return
        event: Event | None = None
        if event_type == intent.TimerEventType.STARTED:
            event = TimerStarted(
                id=timer.id,
                total_seconds=timer.seconds,
                name=timer.name,
                start_hours=timer.start_hours,
                start_minutes=timer.start_minutes,
                start_seconds=timer.start_seconds,
            ).event()
        elif event_type == intent.TimerEventType.UPDATED:
            event = TimerUpdated(
                id=timer.id, is_active=timer.is_active, total_seconds=timer.seconds
            ).event()
        elif event_type == intent.TimerEventType.CANCELLED:
            event = TimerCancelled(id=timer.id).event()
        elif event_type == intent.TimerEventType.FINISHED:
            event = TimerFinished(id=timer.id).event()
        if event is not None:
            self._entry.async_create_background_task(
                self.hass, self._client.write_event(event), "smartghar timer event"
            )


def _try_parse_wav_header(header_data: bytes) -> tuple[int, int, int, bytes] | None:
    """Parse a WAV header; (rate, width, channels, audio) or None."""
    try:
        with io.BytesIO(header_data) as wav_io, wave.open(wav_io, "rb") as wav_file:
            return (
                wav_file.getframerate(),
                wav_file.getsampwidth(),
                wav_file.getnchannels(),
                wav_file.readframes(wav_file.getnframes()),
            )
    except wave.Error:
        return None
