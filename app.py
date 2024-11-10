#
# Copyright (c) 2024, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import aiohttp
import os
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger
import sys
from tavus import TavusVideoService

MAX_BOTS_PER_ROOM = 1

# Bot sub-process dict for status reporting and concurrency control
bot_procs = {}
tavus_session = None

logger.remove()
logger.add(sys.stderr, level="DEBUG")

class CustomTavusVideoService(TavusVideoService):
    """Extended TavusVideoService with extra logging"""
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, TTSAudioRawFrame):
            logger.info(f"Sending audio frame to Tavus: {len(frame.audio)} bytes, sample rate: {frame.sample_rate}")
        elif isinstance(frame, TTSStartedFrame):
            logger.info(f"TTS Started, frame ID: {frame.id}")
        elif isinstance(frame, TTSStoppedFrame):
            logger.info("TTS Stopped")
        await super().process_frame(frame, direction)

def cleanup():
    # Clean up function, just to be extra safe
    for entry in bot_procs.values():
        proc = entry[0]
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except:
            proc.kill()
            proc.wait()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tavus_session
    tavus_session = aiohttp.ClientSession()
    yield
    await tavus_session.close()
    cleanup()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def get_tavus_room():
    """Initialize Tavus and get room URL"""
    try:
        tavus = CustomTavusVideoService(
            api_key=os.getenv("TAVUS_API_KEY"),
            replica_id=os.getenv("TAVUS_REPLICA_ID"),
            persona_id=os.getenv("TAVUS_PERSONA_ID", "p2fbd605"),
            session=tavus_session,
        )
        
        # Get persona name and room URL
        persona_name = await tavus.get_persona_name()
        room_url = await tavus.initialize()
        logger.info(f"Got room URL from Tavus: {room_url}")
        logger.info(f"Persona name: {persona_name}")
        logger.info(f"Conversation ID: {tavus._conversation_id}")
        
        return room_url, persona_name
    except Exception as e:
        logger.error(f"Error getting Tavus room: {e}")
        raise

@app.get("/")
async def start_agent(request: Request):
    logger.info("Starting agent initialization")
    
    try:
        # Get room URL from Tavus
        room_url, persona_name = await get_tavus_room()
        
        if not room_url:
            raise HTTPException(
                status_code=500,
                detail="Failed to get room URL from Tavus"
            )

        # Check existing processes
        num_bots_in_room = sum(
            1 for proc in bot_procs.values() if proc[1] == room_url and proc[0].poll() is None
        )
        if num_bots_in_room >= MAX_BOTS_PER_ROOM:
            raise HTTPException(status_code=500, detail=f"Max bot limit reached for room: {room_url}")

        # Start the bot process with room URL and detailed logging
        try:
            env = os.environ.copy()
            env['LOGURU_LEVEL'] = 'DEBUG'
            
            cmd = [
                "python3", "-m", "bot",
                "-u", room_url,
                "--debug"  # Add debug flag if your bot supports it
            ]
            
            proc = subprocess.Popen(
                cmd,
                bufsize=1,
                cwd=os.path.dirname(os.path.abspath(__file__)),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            bot_procs[proc.pid] = (proc, room_url)
            logger.info(f"Started bot process {proc.pid} for room {room_url}")
            
            # Start a thread to monitor bot output
            import threading
            def monitor_output():
                while True:
                    output = proc.stdout.readline()
                    if output:
                        logger.info(f"Bot output: {output.strip()}")
                    error = proc.stderr.readline()
                    if error:
                        logger.error(f"Bot error: {error.strip()}")
                    if proc.poll() is not None:
                        break
                        
            threading.Thread(target=monitor_output, daemon=True).start()
            
            return RedirectResponse(room_url)
            
        except Exception as e:
            logger.error(f"Failed to start bot process: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to start bot process: {e}")

    except Exception as e:
        logger.error(f"Failed to initialize agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{pid}")
def get_status(pid: int):
    proc = bot_procs.get(pid)
    if not proc:
        raise HTTPException(status_code=404, detail=f"Bot with process id: {pid} not found")

    process, room_url = proc
    returncode = process.poll()
    
    if returncode is None:
        status = "running"
    else:
        status = f"finished with code {returncode}"
        # Get any remaining output
        stdout, stderr = process.communicate()
        if stderr:
            logger.error(f"Bot final error output: {stderr}")
            status += f" (error: {stderr})"

    return JSONResponse({
        "bot_id": pid,
        "status": status,
        "room_url": room_url
    })

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    
    # Use Heroku's PORT environment variable
    port = int(os.getenv("PORT", 8000))
    
    logger.info(f"Server starting on port {port}")
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=False if os.getenv("ENVIRONMENT") == "production" else True,
    )
