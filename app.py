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
import threading
from queue import Queue
import asyncio
from tavus import TavusVideoService

MAX_BOTS_PER_ROOM = 5

# Bot sub-process dict for status reporting and concurrency control
bot_procs = {}
tavus_session = None

# Configure logging
logger.remove()
logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>SERVER</cyan>: {message}")

def log_stream(stream, prefix):
    """Read and log a stream (stdout/stderr) with a prefix"""
    for line in iter(stream.readline, ''):
        if line:
            logger.info(f"{prefix}: {line.strip()}")

def cleanup():
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
        tavus = TavusVideoService(
            api_key=os.getenv("TAVUS_API_KEY"),
            replica_id=os.getenv("TAVUS_REPLICA_ID"),
            persona_id=os.getenv("TAVUS_PERSONA_ID", "p2fbd605"),
            session=tavus_session,
        )
        
        persona_name = await tavus.get_persona_name()
        room_url = await tavus.initialize()
        logger.info(f"Got room URL from Tavus: {room_url}")
        logger.info(f"Persona name: {persona_name}")
        
        return room_url, persona_name
    except Exception as e:
        logger.error(f"Error getting Tavus room: {e}")
        raise

async def run_bot_process(room_url):
    """Run bot process and capture output"""
    try:
        # Set up environment with debug logging
        env = os.environ.copy()
        env['LOGURU_LEVEL'] = 'DEBUG'
        env['PYTHONUNBUFFERED'] = '1'  # Force unbuffered output

        # Prepare command
        cmd = [
            sys.executable,  # Use same Python interpreter
            "-u",  # Unbuffered output
            "-m", "bot",
            "-u", room_url,
        ]

        logger.info(f"Starting bot with command: {' '.join(cmd)}")

        # Start process with pipe for output
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,  # Return strings instead of bytes
            bufsize=1,  # Line buffered
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env
        )

        # Start threads to monitor output
        stdout_thread = threading.Thread(
            target=log_stream, 
            args=(process.stdout, "BOT-OUT"),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=log_stream, 
            args=(process.stderr, "BOT-ERR"),
            daemon=True
        )

        stdout_thread.start()
        stderr_thread.start()

        return process

    except Exception as e:
        logger.error(f"Error starting bot process: {e}")
        raise

@app.get("/")
async def start_agent(request: Request):
    logger.info("Starting agent initialization")
    
    try:
        room_url, persona_name = await get_tavus_room()
        
        if not room_url:
            raise HTTPException(
                status_code=500,
                detail="Failed to get room URL from Tavus"
            )

        # Check for existing bots
        num_bots_in_room = sum(
            1 for proc in bot_procs.values() if proc[1] == room_url and proc[0].poll() is None
        )
        if num_bots_in_room >= MAX_BOTS_PER_ROOM:
            raise HTTPException(status_code=500, detail=f"Max bot limit reached for room: {room_url}")

        # Start bot process
        proc = await run_bot_process(room_url)
        
        # Store process info
        bot_procs[proc.pid] = (proc, room_url)
        logger.info(f"Bot process started with PID {proc.pid}")

        return RedirectResponse(room_url)

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

    status_info = {
        "bot_id": pid,
        "room_url": room_url,
    }

    if returncode is None:
        status_info["status"] = "running"
    else:
        status_info["status"] = f"finished (code {returncode})"

    return JSONResponse(status_info)

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
