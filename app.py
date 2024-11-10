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

MAX_BOTS_PER_ROOM = 1

# Bot sub-process dict for status reporting and concurrency control
bot_procs = {}

def cleanup():
    # Clean up function, just to be extra safe
    for entry in bot_procs.values():
        proc = entry[0]
        proc.terminate()
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
    print(f"!!! Starting bot process")
    
    try:
        # Start the bot process directly - it will handle room creation via Tavus
        proc = subprocess.Popen(
            ["python3", "bot.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        
        # Store process info
        bot_procs[proc.pid] = (proc, None)  # We don't have the room URL yet
        
        # Return the process ID so the client can check status
        return JSONResponse({
            "status": "started",
            "bot_id": proc.pid,
            "message": "Bot process started. Check status endpoint for room URL."
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start bot process: {e}")

@app.get("/status/{pid}")
def get_status(pid: int):
    # Look up the subprocess
    proc_entry = bot_procs.get(pid)

    # If the subprocess doesn't exist, return an error
    if not proc_entry:
        raise HTTPException(status_code=404, detail=f"Bot with process id: {pid} not found")
    
    proc = proc_entry[0]
    
    # Check if process is still running
    if proc.poll() is None:
        # Process is running
        # Check stdout for room URL
        output, error = "", ""
        try:
            output = proc.stdout.readline()
            if not output:
                error = proc.stderr.readline()
        except:
            pass
            
        # Look for room URL in output
        if "Join the video call at:" in output:
            room_url = output.split("Join the video call at:")[-1].strip()
            bot_procs[pid] = (proc, room_url)  # Update with room URL
            return JSONResponse({
                "bot_id": pid,
                "status": "running",
                "room_url": room_url
            })
        
        return JSONResponse({
            "bot_id": pid,
            "status": "initializing",
            "message": "Bot starting up, room URL not available yet"
        })
    else:
        # Process has ended
        output, error = proc.communicate()
        status_info = {
            "bot_id": pid,
            "status": "finished",
            "exit_code": proc.returncode
        }
        
        if error:
            status_info["error"] = error
            
        return JSONResponse(status_info)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    
    # Use Heroku's PORT environment variable
    port = int(os.getenv("PORT", 8000))
    
    print(f"Server starting on port {port}")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False if os.getenv("ENVIRONMENT") == "production" else True,
    )
