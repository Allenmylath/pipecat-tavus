#
# Copyright (c) 2024, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import aiohttp
import os
import subprocess
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
import threading
from queue import Queue
import sys
from loguru import logger

MAX_BOTS_PER_ROOM = 1
WAIT_TIMEOUT = 30  # Maximum seconds to wait for room URL

# Bot sub-process dict for status reporting and concurrency control
bot_procs = {}
output_queues = {}
room_events = {}

logger.remove()
logger.add(sys.stderr, level="DEBUG")

def read_output(proc, queue, pid):
    """Read process output in a separate thread"""
    while True:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        if line:
            line = line.strip()
            logger.debug(f"Bot output: {line}")
            queue.put(line)
            # Check for room URL
            if "Join the video call at:" in line:
                room_url = line.split("Join the video call at:")[-1].strip()
                bot_procs[pid] = (proc, room_url)
                logger.info(f"Found room URL: {room_url}")
                # Set the event to signal URL is available
                if pid in room_events:
                    room_events[pid].set()

def cleanup():
    # Clean up function, just to be extra safe
    for entry in bot_procs.values():
        proc = entry[0]
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    cleanup()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def start_agent(request: Request):
    logger.info("Starting bot process")
    
    try:
        # Start the bot process with proper output handling
        proc = subprocess.Popen(
            ["python3", "bot.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,  # Line buffered
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        
        pid = proc.pid
        
        # Create output queue and event for this process
        output_queue = Queue()
        output_queues[pid] = output_queue
        room_events[pid] = asyncio.Event()
        
        # Start output reader thread
        thread = threading.Thread(
            target=read_output,
            args=(proc, output_queue, pid),
            daemon=True
        )
        thread.start()
        
        # Store process info
        bot_procs[pid] = (proc, None)
        
        logger.info(f"Bot process started with PID: {pid}")
        
        # Wait for room URL with timeout
        try:
            await asyncio.wait_for(room_events[pid].wait(), timeout=WAIT_TIMEOUT)
            # Get room URL
            _, room_url = bot_procs[pid]
            if room_url:
                logger.info(f"Redirecting to room: {room_url}")
                return RedirectResponse(url=room_url)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for room URL")
            raise HTTPException(
                status_code=500,
                detail="Timeout waiting for room initialization"
            )
        
        raise HTTPException(
            status_code=500,
            detail="Failed to get room URL"
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot process: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup events and queues
        if pid in room_events:
            del room_events[pid]
        if pid in output_queues:
            del output_queues[pid]

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
