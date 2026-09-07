import time

from correlator import TurnCorrelator
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    MetricsCollectedEvent,
    SessionUsageUpdatedEvent,
    TurnHandlingOptions,
    inference,
)
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.events import (
    # .speech_handle -> .id (== speech_id), .interrupted,
    #                   .num_steps, .chat_items
    ConversationItemAddedEvent,  # .item -> ChatMessage, .item.metrics: MetricsReport
    SpeechCreatedEvent,  # .user_initiated: bool, .source: 'say'|'generate_reply',
)
from livekit.plugins import cartesia, deepgram, google, silero
from sink import JsonlMetricSink

load_dotenv(".env.local")

server = AgentServer()

# The scenario is deliberately a narrow slot-filling task, not a general assistant.
# A fixed script against a bounded task is what makes turns comparable between runs, which
# is the whole premise of the endpointing sweep. An open-domain agent produces conversations
# of varying length and shape and the comparison stops meaning anything.
#
# Voice prompt, not a chat prompt: no markdown, no lists, no symbols. Every character is
# spoken aloud.
#
# NOTE: an earlier version of this prompt carried the voice-realism sections from the
# LiveKit prompting guide (filler words, self-corrections, phrase variation). Those emit
# SSML tags such as <break time="300ms"/> into the text stream, which land in
# TTSMetrics.characters_count and, if the provider honours them, in the audio itself. They
# appeared in 100% of assistant turns. Removed, because they contaminate the thing being
# measured. Worth re-adding deliberately as a labelled variant to measure what realism costs.

AGENT_INSTRUCTIONS = """
You are Robin, the appointments assistant for the city hall of Northgate, a fictional town.
You are warm, calm and precise. Your job is to help callers book a civil ceremony slot.

# Output rules

You are interacting with the user via voice, and must apply the following rules to ensure your output sounds natural in a text-to-speech system:
- Respond in plain text only. Never use JSON, markdown, lists, tables, code, emojis, or other complex formatting.
- Keep replies brief by default: one to three sentences. Ask one question at a time.
- Spell out numbers, phone numbers, or email addresses.
- Omit `https://` and other formatting if listing a web URL.
- Avoid acronyms and words with unclear pronunciation, when possible.

# Goal

Help the caller reserve a ceremony slot. You will accomplish the following:
- Collect the full names of both parties.
- Agree a date and a time from the available slots below.
- Collect one contact email address for the confirmation.
- Tell the caller a member of staff reviews every request before it is confirmed.
- Read the details back and confirm the caller is happy with them.

# Available slots

These are the only slots available. Do not invent others.
- Saturday the fourteenth of June, at ten in the morning or at two in the afternoon.
- Saturday the twenty first of June, at eleven in the morning.
- Friday the twenty seventh of June, at three in the afternoon.

If the caller asks for a date that is not on this list, say so plainly and offer the closest one.

# Guardrails

- Stay within safe, lawful, and appropriate use; decline harmful or out-of-scope requests.
- For medical, legal, or financial topics, provide general information only and suggest consulting a qualified professional.
- Protect privacy and minimize sensitive data.
- Say when something is not available to you rather than making it up.

# Emotion

- Default to a calm, peaceful baseline.
- Use stronger emotions sparingly, only in moments that warrant them: a genuine apology, a brief celebration of a successful task, or a confused recovery.
- Don't switch emotions mid-sentence.

"""

@server.rtc_session(agent_name="latency-lab")
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en", endpointing_ms=25),
        # llm=inference.LLM(model="google/gemma-4-31b-it"),
        llm=google.LLM(
            # model="gemma-4-31b-it", 
            model="gemini-3.5-flash-lite",
            temperature=0.1,
            ),
        tts=cartesia.TTS(model="sonic-3", voice="f786b574-daa5-4673-aa0c-cbe3e8534c02"),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(turn_detection=inference.TurnDetector()),
    )

    # One correlator per session. Not module-level: AgentServer forks a process per
    # session today, but relying on that is how you get cross-session bleed later.
    #
    # llm_timeout is the per-attempt APIConnectOptions.timeout this LLM is actually running
    # with. google.LLM takes DEFAULT_API_CONNECT_OPTIONS, which is timeout=10.0. Passing it
    # lets the correlator separate a retry from a slow model: a single attempt cannot exceed
    # its own timeout, so ttft above it means at least one attempt timed out. Change this if
    # you pass conn_options above, and drop it entirely if you stop being sure.
    correlator = TurnCorrelator(llm_timeout=10.0)

    # --- Stream A: raw per-component metrics. DEPRECATED, goes at SDK 2.0.
    # Keep it as a sidecar, not as the source of truth. It is the only place you get
    # connection_reused, acquire_time, and per-turn prompt_cached_tokens /
    # characters_count (session_usage_updated has those only cumulatively). It is also
    # the only stream where EOU/LLM/TTS share one speech_id, which makes it the easier
    # join. Delete this handler when 2.0 lands; the correlator should still work.
    # Persist every raw fragment before correlating it, so a published table can be
    # re-derived months later without re-running the session. Not print(): console mode owns
    # stdout and swallows it, which is how the 7 Sep run lost all of its timings.
    sink = JsonlMetricSink(f"data/metrics/{time.strftime('%Y%m%d-%H%M%S')}.jsonl")

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        sink.write(ev.metrics)
        correlator.on_metric(ev.metrics)

    # --- Stream B: speech lifecycle. Tells you deterministically whether a speech was
    # agent-initiated, and maps assistant ChatMessage ids back to a speech_id via
    # speech_handle.chat_items (populated as the reply is generated, so read it late).
    @session.on("speech_created")
    def _on_speech_created(ev: SpeechCreatedEvent) -> None:
        correlator.on_speech_created(
            speech_id=ev.speech_handle.id,
            user_initiated=ev.user_initiated,
            source=ev.source,
            handle=ev.speech_handle,
        )

    # --- Stream C: the framework's own per-turn report. SUPPORTED path, so this is
    # the one to build on. provider_request_ids is the best retry signal available:
    # more than one id on a turn suggests the request was retried.
    # NOTE: MetricsReport is split across TWO messages. The user ChatMessage carries
    # transcription_delay / end_of_turn_delay / on_user_turn_completed_delay; the
    # assistant ChatMessage carries llm_node_ttft / llm_node_ttfs / tts_node_ttfb /
    # playback_latency / e2e_latency / provider_request_ids. Different ids. Pairing
    # them is your job, not the framework's.
    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        if not isinstance(ev.item, ChatMessage):
            return
        correlator.on_chat_message(
            message_id=ev.item.id,
            role=ev.item.role,
            metrics=ev.item.metrics or {},
            interrupted=ev.item.interrupted,
        )

    @session.on("session_usage_updated")
    def _on_usage(ev: SessionUsageUpdatedEvent) -> None:
        for usage in ev.usage.model_usage:
            print(f"{usage.provider}/{usage.model}: {usage}")

    async def _flush(*_):
        sink.close()
        print(f"[correlator] raw fragments written to {sink.path} ({sink.written})")
        correlator.flush_incomplete()
    ctx.add_shutdown_callback(_flush)

    await session.start(room=ctx.room, 
                            agent=Agent(instructions=AGENT_INSTRUCTIONS),
                            # room_options=room_io.RoomOptions(
                            #    audio_input=room_io.AudioInputOptions(
                            #       noise_cancellation=noise_cancellation.BVC(),
                            #    )
                            #)
                        )
    # Agent-initiated, so there is no preceding end-of-utterance and TTFA is undefined
    # for this turn. The correlator excludes it and counts it separately.
    await session.generate_reply(
        instructions="Greet the caller and ask how you can help with their booking."
    )

if __name__ == "__main__":
    agents.cli.run_app(server)
