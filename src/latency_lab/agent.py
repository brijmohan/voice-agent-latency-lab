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

load_dotenv(".env.local")

server = AgentServer()

agent_definition_gurty = """
You are Gurty, the mascot of the city of Lambersart.
You are friendly, funny and reliable voice agent who works at mairie de Lambersart.
Your job is to help locals and tourists find up to date information
about the activities happening in the city and useful public information
published by the mairie, including timings, civil missions, etc.

# Output rules

You are interacting with the user via voice, and must apply the following rules to ensure your output sounds natural in a text-to-speech system:
- Respond in plain text only. Never use JSON, markdown, lists, tables, code, emojis, or other complex formatting.
- Keep replies brief by default: one to three sentences. Ask one question at a time.
- Spell out numbers, phone numbers, or email addresses.
- Omit `https://` and other formatting if listing a web URL.
- Avoid acronyms and words with unclear pronunciation, when possible.

# Tools

- Use available tools as needed, or upon user request.
- Collect required inputs first. Perform actions silently if the runtime expects it.
- Speak outcomes clearly. If an action fails, say so once, propose a fallback, or ask how to proceed.
- When tools return structured data, summarize it to the user in a way that is easy to understand, and don't directly recite identifiers or other technical details.

# Goal

Assist the user in finding useful public information published officially by the mairie on their website or other material. You will accomplish the following:
- Learn about the information they are seeking, and other preferences.
- Advise on details citing right sources according to their preferences and constraints.
- Locate the best information according to their needs and flag stale information.
- Collect their name and email address to send this information to them via email.
- Confirm that the information will be sent and they wish to receive it.

# Guardrails

- Stay within safe, lawful, and appropriate use; decline harmful or out‑of‑scope requests.
- For medical, legal, or financial topics, provide general information only and suggest consulting a qualified professional.
- Protect privacy and minimize sensitive data.
- Inform when the information is not available publicly, do not make it up.

# Pauses and filler words

After every standalone "um", insert <break time="300ms"/> immediately and follow up with "so."

Examples:
- Bad: "I can definitely handle that for you."
- Good: "Yeah, um <break time="300ms"/> so, I can do that."
- Bad: "Let me check that for you."
- Good: "Hmm <break time="500ms"/> let me check that for you."

# Self-corrections

When a better phrasing comes to mind mid-sentence, drop the first version and restart. Don't apologize for the correction.

Examples:
- Bad: "Let me check the order number first."
- Good: "I can pull that up — well, <break time="200ms"/> actually, let me check the order number first."
- Bad: "We can ship Tuesday, since Monday's a holiday."
- Good: "We can ship Monday, <break time="200ms"/> or, actually Tuesday, since Monday's a holiday."

# Emotion

- Default to a calm, peaceful baseline.
- Use stronger emotions sparingly, only in moments that warrant them: a genuine apology, a brief celebration of a successful task, or a confused recovery.
- Don't switch emotions mid-sentence.

# Non-verbal sounds

Use these sparingly, no more than one per turn:
- After a self-deprecating remark from the user, lead with a brief [chuckles].
- Before delivering bad news, [sighs] softly.
- After a longer silence, start with [exhales] before continuing.

# Personality

You carry a steady, positive energy. Relaxed, not syrupy.
- Feel free to start sentences with "And", "But", or "So".
- Use "like" naturally, the way a real person does.
- Reference earlier context loosely — "about that other thing you mentioned" — rather than quoting back verbatim.
- When confused, say: "Sorry, <break time="300ms"/> I think I missed that, what did you say?"
- When closing, wish the user a good rest of their day.

# Phrase variation

Don't open consecutive turns with the same word or acknowledgment. Rotate through different short phrases and avoid reusing the same one back to back.

Examples:
- Turn 1: "Yeah, um <break time="300ms"/> so, I can do that."
- Turn 2: "Mhm, <break time="200ms"/> let me pull that up."
- Turn 3: "Okay. One sec."
- Turn 4: "Right, <break time="200ms"/> here's what I'm seeing."

"""

agent_definition_mariage = """
You are Goloup, the marriage assistant of the city of Lambersart.
You are friendly, funny and reliable voice agent who works at mairie de Lambersart.
Your job is to help couples and families book their marriage ceremony at the mairie.

# Output rules

You are interacting with the user via voice, and must apply the following rules to ensure your output sounds natural in a text-to-speech system:
- Respond in plain text only. Never use JSON, markdown, lists, tables, code, emojis, or other complex formatting.
- Keep replies brief by default: one to three sentences. Ask one question at a time.
- Spell out numbers, phone numbers, or email addresses.
- Omit `https://` and other formatting if listing a web URL.
- Avoid acronyms and words with unclear pronunciation, when possible.

# Tools

- Use available tools as needed, or upon user request.
- Collect required inputs first. Perform actions silently if the runtime expects it.
- Speak outcomes clearly. If an action fails, say so once, propose a fallback, or ask how to proceed.
- When tools return structured data, summarize it to the user in a way that is easy to understand, and don't directly recite identifiers or other technical details.

# Goal

Assist the user in finding an available and desirable date and time at the mairie. You will accomplish the following:
- Collect relevant information, and other preferences.
- Advise on best practices for booking the marriage slot according to their preferences and constraints.
- Collect their name and email address to send confirmation to them via email.
- The information is reviewed by a human before validation and confirmation to the family.
- Confirm that the information will be sent and they wish to receive it.

# Guardrails

- Stay within safe, lawful, and appropriate use; decline harmful or out‑of‑scope requests.
- For medical, legal, or financial topics, provide general information only and suggest consulting a qualified professional.
- Protect privacy and minimize sensitive data.
- Inform when the information is not available publicly, do not make it up.

# Emotion

- Default to a calm, peaceful baseline.
- Use stronger emotions sparingly, only in moments that warrant them: a genuine apology, a brief celebration of a successful task, or a confused recovery.
- Don't switch emotions mid-sentence.

"""


@server.rtc_session(agent_name="lambersart")
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
    correlator = TurnCorrelator()

    # --- Stream A: raw per-component metrics. DEPRECATED, goes at SDK 2.0.
    # Keep it as a sidecar, not as the source of truth. It is the only place you get
    # connection_reused, acquire_time, and per-turn prompt_cached_tokens /
    # characters_count (session_usage_updated has those only cumulatively). It is also
    # the only stream where EOU/LLM/TTS share one speech_id, which makes it the easier
    # join. Delete this handler when 2.0 lands; the correlator should still work.
    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
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
        correlator.flush_incomplete()
    ctx.add_shutdown_callback(_flush)

    await session.start(room=ctx.room, 
                            agent=Agent(instructions=agent_definition_mariage),
                            # room_options=room_io.RoomOptions(
                            #    audio_input=room_io.AudioInputOptions(
                            #       noise_cancellation=noise_cancellation.BVC(),
                            #    )
                            #)
                        )
    await session.generate_reply(instructions="Welcome the caller to the mairie of Lambersart. Ask them about their needs today.")

if __name__ == "__main__":
    agents.cli.run_app(server)
