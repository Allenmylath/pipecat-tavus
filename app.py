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

def cleanup():
    # Clean up function, just to be extra safe
    for entry in bot_procs.values():
        proc = entry[0]
        proc.terminate()
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
        
        # Get persona name and room URL
        persona_name = await tavus.get_persona_name()
        room_url = await tavus.initialize()
        logger.info(f"Got room URL from Tavus: {room_url}")
        logger.info(f"Persona name: {persona_name}")
        
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

        # Check if there is already an existing process running in this room
        num_bots_in_room = sum(
            1 for proc in bot_procs.values() if proc[1] == room_url and proc[0].poll() is None
        )
        if num_bots_in_room >= MAX_BOTS_PER_ROOM:
            raise HTTPException(status_code=500, detail=f"Max bot limit reached for room: {room_url}")

        # Start the bot process with the room URL
        try:
            cmd = [
                "python3", "-m", "bot",
                "-u", room_url,
            ]
            
            proc = subprocess.Popen(
                cmd,
                bufsize=1,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            
            bot_procs[proc.pid] = (proc, room_url)
            logger.info(f"Started bot process {proc.pid} for room {room_url}")
            
            # Return room URL for redirect
            return RedirectResponse(room_url)
            
        except Exception as e:
            logger.error(f"Failed to start bot process: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to start bot process: {e}")

    except Exception as e:
        logger.error(f"Failed to initialize agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{pid}")
def get_status(pid: int):
    # Look up the subprocess
    proc = bot_procs.get(pid)

    # If the subprocess doesn't exist, return an error
    if not proc:
        raise HTTPException(status_code=404, detail=f"Bot with process id: {pid} not found")

    # Check the status of the subprocess
    if proc[0].poll() is None:
        status = "running"
    else:
        status = "finished"

    return JSONResponse({
        "bot_id": pid,
        "status": status,
        "room_url": proc[1]
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
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False if os.getenv("ENVIRONMENT") == "production" else True,
    )
