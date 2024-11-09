import asyncio
import aiohttp
import os
import sys
from loguru import logger
import json
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

from pipecat.frames.frames import LLMMessagesFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.services.cartesia import CartesiaTTSService
from pipecat.services.deepgram import DeepgramSTTService
from pipecat.services.openai import OpenAILLMContext, OpenAILLMService, OpenAILLMContextFrame
from pipecat.services.tavus import TavusVideoService
from pipecat.transports.services.daily import DailyParams, DailyTransport
from pipecat.audio.vad.silero import SileroVADAnalyzer

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")
firebase_creds = json.loads(os.environ.get('FIREBASE_CREDENTIALS', '{}'))
cred = credentials.Certificate(firebase_creds)
firebase_admin.initialize_app(cred)
db = firestore.client()

# [Previous IntakeProcessor class remains the same]

async def main():
    async with aiohttp.ClientSession() as session:
        # Initialize Tavus
        tavus = TavusVideoService(
            api_key=os.getenv("TAVUS_API_KEY"),
            replica_id=os.getenv("TAVUS_REPLICA_ID"),
            persona_id=os.getenv("TAVUS_PERSONA_ID", "pipecat0"),
            session=session,
        )

        # Get persona name and room URL
        persona_name = await tavus.get_persona_name()
        room_url = await tavus.initialize()
        logger.info(f"Join the video call at: {room_url}")

        # Initialize Daily transport
        transport = DailyTransport(
            room_url=room_url,
            token=None,
            bot_name="Medical Intake Bot",
            params=DailyParams(
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
                vad_audio_passthrough=True,
            ),
        )

        llm = OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY"),
            model="gpt-4o"
        )
        stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
        tts = CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY"),
            voice_id="829ccd10-f8b3-43cd-b8a0-4aeaa81f3b30",  # British Lady
        )

        messages = []
        context = OpenAILLMContext(messages=messages)
        context_aggregator = llm.create_context_aggregator(context)
        intake = IntakeProcessor(context)

        # Register all the intake functions
        llm.register_function("verify_birthday", intake.verify_birthday)
        llm.register_function(
            "list_prescriptions",
            intake.save_data,
            start_callback=intake.start_prescriptions
        )
        llm.register_function(
            "list_allergies",
            intake.save_data,
            start_callback=intake.start_allergies
        )
        llm.register_function(
            "list_conditions",
            intake.save_data,
            start_callback=intake.start_conditions
        )
        llm.register_function(
            "list_visit_reasons",
            intake.save_data,
            start_callback=intake.start_visit_reasons
        )

        pipeline = Pipeline([
            transport.input(),  # Daily WebRTC input
            stt,  # Speech-To-Text
            context_aggregator.user(),  # User responses
            llm,  # LLM
            tts,  # Text-To-Speech
            tavus,  # Tavus video layer
            transport.output(),  # Daily WebRTC output
            context_aggregator.assistant(),  # Assistant responses
        ])

        task = PipelineTask(
            pipeline,
            PipelineParams(
                allow_interruptions=False,
                enable_metrics=True,
                enable_usage_metrics=True,
                report_only_initial_ttfb=True,
            )
        )

        @transport.event_handler("on_participant_joined")
        async def on_participant_joined(
            transport: DailyTransport, participant: Mapping[str, Any]
        ) -> None:
            # Ignore the Tavus replica's microphone
            if participant.get("info", {}).get("userName", "") == persona_name:
                logger.debug(f"Ignoring {participant['id']}'s microphone")
                await transport.update_subscriptions(
                    participant_settings={
                        participant["id"]: {
                            "media": {"microphone": "unsubscribed"},
                        }
                    }
                )

            if participant.get("info", {}).get("userName", "") != persona_name:
                # Clear existing messages and create new context
                context.messages.clear()
                intake = IntakeProcessor(context)
                # Kick off the conversation
                await task.queue_frames([OpenAILLMContextFrame(context)])

        @transport.event_handler("on_participant_left")
        async def on_participant_left(
            transport: DailyTransport, participant: Mapping[str, Any]
        ) -> None:
            if participant.get("info", {}).get("userName", "") != persona_name:
                logger.info(f"Participant left: {participant['id']}")

        runner = PipelineRunner()
        await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main())
