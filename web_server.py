"""
Qatar Visa Bot - Web Server
FastAPI-based REST API for the web control panel
"""

import asyncio
import json
import uuid
import logging
import tempfile
import shutil
from datetime import datetime, time
from pathlib import Path
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
import uvicorn
from config import config

# Configure logging - write to both console and file
log_format = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# File handler - same as main.py uses
file_handler = logging.FileHandler('visa_bot.log', mode='a', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(log_format)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Also add file handler to root logger for other modules
logging.getLogger().addHandler(file_handler)

# ============================================
# Data Models
# ============================================

class ApplicantCreate(BaseModel):
    passport_number: str = Field(..., min_length=5, max_length=15)
    visa_number: str = Field(..., min_length=3, max_length=20)
    mobile: str = Field(..., min_length=10, max_length=20)
    email: EmailStr
    country: str = "Pakistan"

class ApplicantUpdate(ApplicantCreate):
    pass

class Applicant(ApplicantCreate):
    id: str
    status: str = "pending"
    last_booked: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class TimeSlot(BaseModel):
    start: str = "09:00"
    end: str = "17:00"

class DaySchedule(BaseModel):
    day: str = "Monday"
    slots: List[TimeSlot] = []

class Schedule(BaseModel):
    enabled: bool = True
    days: List[DaySchedule] = []
    # Legacy fields (optional, for backward compatibility)
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class BotStatus(BaseModel):
    running: bool = False
    current_applicant: Optional[str] = None
    logs: List[dict] = []

# ============================================
# Data Storage
# ============================================

DATA_FILE = Path(__file__).parent / "applicants.json"

def load_data() -> dict:
    """Load data from JSON file"""
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {"applicants": [], "schedule": {"enabled": True, "start_time": "22:00", "end_time": "00:00"}}

def save_data(data: dict):
    """Save data to JSON file atomically"""
    try:
        # Write to temp file first, then atomic move
        with tempfile.NamedTemporaryFile('w', dir=DATA_FILE.parent, delete=False, suffix='.tmp', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            temp_path = Path(f.name)
        # Atomic move (on Windows this may not be truly atomic but is still safer)
        shutil.move(str(temp_path), str(DATA_FILE))
    except Exception as e:
        logger.error(f"Failed to save data: {e}")
        # Fallback to direct write if temp file fails
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

# ============================================
# Bot Runner
# ============================================

from proxy_manager import ProxyManager

class BotRunner:
    """Manages bot execution state"""
    
    def __init__(self):
        self.running = False
        self.current_applicant = None
        self.logs = []
        self.task = None
        self._stop_requested = False
        self._browser = None  # Store browser reference for force stop
        self._lock = asyncio.Lock()  # Prevent race conditions
        self._log_cursor = 0  # Track which logs have been sent
        self._proxy_manager: Optional[ProxyManager] = None
    
    def _get_proxy_manager(self) -> Optional[ProxyManager]:
        """Create proxy manager if enabled"""
        if not config.PROXY_ENABLED:
            return None
        
        if not self._proxy_manager:
            self._proxy_manager = ProxyManager(
                username=config.PROXY_USERNAME,
                password=config.PROXY_PASSWORD,
                host=config.PROXY_HOST,
                port=config.PROXY_PORT,
                sticky_duration_mins=config.PROXY_STICKY_MINS,
                max_rotations_per_session=config.PROXY_MAX_ROTATIONS,
            )
        return self._proxy_manager

    def add_log(self, message: str, log_type: str = ""):
        self.logs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "type": log_type
        })
        # Keep only last 100 logs
        if len(self.logs) > 100:
            # Adjust cursor when trimming
            trim_count = len(self.logs) - 100
            self._log_cursor = max(0, self._log_cursor - trim_count)
            self.logs = self.logs[-100:]
        logger.info(f"[Bot] {message}")
    
    def get_logs_since(self, cursor: int = 0) -> tuple:
        """Get logs since cursor, return (logs, new_cursor)"""
        if cursor < 0:
            cursor = 0
        new_logs = self.logs[cursor:]
        return new_logs, len(self.logs)
    
    async def run(self, center: str = "Islamabad"):
        """Run the bot for all pending applicants"""
        # Use lock to prevent race condition
        async with self._lock:
            if self.running:
                return
            self.running = True
        
        self._stop_requested = False
        self.add_log(f"Starting bot for center: {center}")
        
        try:
            data = load_data()
            applicants = [a for a in data["applicants"] if a.get("status") == "pending"]
            
            if not applicants:
                self.add_log("No pending applicants", "error")
                return
            
            for applicant in applicants:
                if self._stop_requested:
                    self.add_log("Bot stopped by user")
                    break
                
                self.current_applicant = applicant["id"]
                self.add_log(f"Processing: {applicant['passport_number']}")
                
                # Update status to processing
                for a in data["applicants"]:
                    if a["id"] == applicant["id"]:
                        a["status"] = "processing"
                        break
                save_data(data)
                
                # Import and run the actual bot
                try:
                    from browser_engine import BrowserEngine
                    from datetime import date
                    from config import config, Applicant as ConfigApplicant
                    
                    # Create applicant object for the bot
                    app_obj = ConfigApplicant(
                        country=applicant["country"],
                        passport_number=applicant["passport_number"],
                        visa_number=applicant["visa_number"],
                        mobile=applicant["mobile"],
                        email=applicant["email"],
                        row_index=0
                    )
                    
                    # Create and start browser directly
                    proxy_mgr = self._get_proxy_manager()
                    self._browser = BrowserEngine(proxy_manager=proxy_mgr)
                    await self._browser.start()
                    
                    if proxy_mgr:
                        ip = await proxy_mgr.verify_ip()
                        self.add_log(f"Using IP: {ip}")
                    
                    # Check stop before booking
                    if self._stop_requested:
                        await self._browser.close()
                        self._browser = None
                        break
                    
                    # Book appointment
                    success = await self._browser.book_appointment(
                        app_obj,
                        config.DATE_RANGE_START,
                        config.DATE_RANGE_END
                    )
                    
                    # Update status based on result
                    data = load_data()  # Reload in case of changes
                    for a in data["applicants"]:
                        if a["id"] == applicant["id"]:
                            a["status"] = "completed" if success else "failed"
                            a["last_booked"] = datetime.now().isoformat() if success else None
                            break
                    save_data(data)
                    
                    if success:
                        self.add_log(f"✓ Success: {applicant['passport_number']}", "success")
                    else:
                        self.add_log(f"✗ Failed: {applicant['passport_number']}", "error")
                    
                    # Cleanup browser
                    await self._browser.close()
                    self._browser = None
                    
                except Exception as e:
                    logger.exception(f"Bot error for {applicant['passport_number']}: {e}")
                    self.add_log(f"Error: {str(e)[:50]}", "error")
                    
                    # Mark as failed
                    for a in data["applicants"]:
                        if a["id"] == applicant["id"]:
                            a["status"] = "failed"
                            break
                    save_data(data)
                
                # Brief pause between applicants
                if not self._stop_requested:
                    await asyncio.sleep(5)
            
            self.add_log("Bot finished")
            
        except Exception as e:
            logger.exception(f"Bot runner error: {e}")
            self.add_log(f"Runner error: {str(e)}", "error")
        finally:
            self.running = False
            self.current_applicant = None
            self._browser = None  # Clear reference on finish
            
            # Log proxy stats
            if self._proxy_manager:
                stats = self._proxy_manager.get_stats()
                self.add_log(f"Proxy stats: {stats['rotation_count']} rotations")
                
            self.add_log("Bot finished or stopped")
    
    def stop(self):
        """Request bot to stop (graceful)"""
        self._stop_requested = True
        self.add_log("Stop requested...")
    
    async def force_stop(self):
        """Force stop - immediately kill browser"""
        self._stop_requested = True
        logger.info("Force stop initiated")
        
        if self._browser:
            try:
                await self._browser.close()
                logger.info("Browser force closed")
            except Exception as e:
                logger.error(f"Error force closing browser: {e}")
            self._browser = None
        
        self.running = False
        self.current_applicant = None
        self.add_log("Bot force stopped", "error")

# Global bot runner instance
bot_runner = BotRunner()

# ============================================
# Scheduler
# ============================================

async def check_schedule():
    """Background task to check if bot should auto-run"""
    DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    
    while True:
        try:
            data = load_data()
            schedule = data.get("schedule", {})
            
            if schedule.get("enabled", False) and not bot_runner.running:
                days = schedule.get("days", [])
                
                if days:
                    now = datetime.now()
                    current_minutes = now.hour * 60 + now.minute
                    # Python: Monday=0, Sunday=6
                    current_day_name = DAYS_OF_WEEK[now.weekday()]
                    
                    in_window = False
                    
                    for day_data in days:
                        day_name = day_data.get("day", "")
                        if day_name != current_day_name and day_name != "Daily":
                            continue
                        
                        for slot in day_data.get("slots", []):
                            start_time = slot.get("start", "09:00")
                            end_time = slot.get("end", "17:00")
                            
                            start_h, start_m = map(int, start_time.split(":"))
                            end_h, end_m = map(int, end_time.split(":"))
                            
                            start_minutes = start_h * 60 + start_m
                            end_minutes = end_h * 60 + end_m
                            
                            # Check if in scheduled window
                            if end_minutes < start_minutes:
                                # Overnight schedule
                                if current_minutes >= start_minutes or current_minutes < end_minutes:
                                    in_window = True
                                    break
                            else:
                                if start_minutes <= current_minutes < end_minutes:
                                    in_window = True
                                    break
                        
                        if in_window:
                            break
                    
                    if in_window:
                        # Check if there are pending applicants
                        pending = [a for a in data["applicants"] if a.get("status") == "pending"]
                        if pending:
                            logger.info("Scheduled run triggered")
                            asyncio.create_task(bot_runner.run())
        
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        
        # Check every minute
        await asyncio.sleep(60)

# ============================================
# FastAPI App
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan manager"""
    # Start scheduler on startup
    asyncio.create_task(check_schedule())
    logger.info("Scheduler started")
    yield
    # Graceful shutdown - cleanup browser processes
    logger.info("Shutting down - cleaning up...")
    await bot_runner.force_stop()
    logger.info("Shutdown complete")

app = FastAPI(
    title="Qatar Visa Bot API",
    description="REST API for the visa bot control panel",
    version="1.0.0",
    lifespan=lifespan
)

# Serve static files
web_dir = Path(__file__).parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

# ============================================
# API Routes
# ============================================

@app.get("/")
async def root():
    """Serve the main page"""
    index_path = web_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Qatar Visa Bot API"}

@app.get("/styles.css")
async def styles():
    """Serve CSS"""
    return FileResponse(str(web_dir / "styles.css"), media_type="text/css")

@app.get("/app.js")
async def script():
    """Serve JS"""
    return FileResponse(str(web_dir / "app.js"), media_type="application/javascript")

# --- Applicants ---

@app.get("/api/applicants")
async def list_applicants():
    """Get all applicants"""
    data = load_data()
    return {"applicants": data.get("applicants", [])}

@app.post("/api/applicants")
async def create_applicant(applicant: ApplicantCreate):
    """Create a new applicant"""
    data = load_data()
    
    new_applicant = {
        "id": f"app_{uuid.uuid4().hex[:8]}",
        "country": applicant.country,
        "passport_number": applicant.passport_number.upper(),
        "visa_number": applicant.visa_number.upper(),
        "mobile": applicant.mobile,
        "email": applicant.email.lower(),
        "status": "pending",
        "last_booked": None,
        "created_at": datetime.now().isoformat()
    }
    
    data["applicants"].append(new_applicant)
    save_data(data)
    
    return new_applicant

@app.put("/api/applicants/{applicant_id}")
async def update_applicant(applicant_id: str, applicant: ApplicantUpdate):
    """Update an applicant"""
    data = load_data()
    
    for i, a in enumerate(data["applicants"]):
        if a["id"] == applicant_id:
            data["applicants"][i].update({
                "passport_number": applicant.passport_number.upper(),
                "visa_number": applicant.visa_number.upper(),
                "mobile": applicant.mobile,
                "email": applicant.email.lower(),
                "status": "pending"  # Reset status when updated
            })
            save_data(data)
            return data["applicants"][i]
    
    raise HTTPException(status_code=404, detail="Applicant not found")

@app.delete("/api/applicants/{applicant_id}")
async def delete_applicant(applicant_id: str):
    """Delete an applicant"""
    data = load_data()
    
    original_len = len(data["applicants"])
    data["applicants"] = [a for a in data["applicants"] if a["id"] != applicant_id]
    
    if len(data["applicants"]) == original_len:
        raise HTTPException(status_code=404, detail="Applicant not found")
    
    save_data(data)
    return {"message": "Applicant deleted"}

# --- Schedule ---

@app.get("/api/schedule")
async def get_schedule():
    """Get current schedule"""
    data = load_data()
    return data.get("schedule", {"enabled": True, "start_time": "22:00", "end_time": "00:00"})

@app.post("/api/schedule")
async def update_schedule(schedule: Schedule):
    """Update schedule"""
    data = load_data()
    data["schedule"] = schedule.dict()
    save_data(data)
    return data["schedule"]

# --- Bot Control ---

@app.get("/api/status")
async def get_status(log_cursor: int = 0):
    """Get current bot status with cursor-based log retrieval"""
    logs, new_cursor = bot_runner.get_logs_since(log_cursor)
    
    proxy_stats = None
    if bot_runner._proxy_manager:
        proxy_stats = bot_runner._proxy_manager.get_stats()
    
    return {
        "running": bot_runner.running,
        "current_applicant": bot_runner.current_applicant,
        "logs": logs,
        "log_cursor": new_cursor,
        "proxy": proxy_stats
    }

@app.post("/api/run")
async def run_bot(background_tasks: BackgroundTasks, center: str = "Islamabad"):
    """Start the bot"""
    if bot_runner.running:
        raise HTTPException(status_code=400, detail="Bot is already running")
    
    data = load_data()
    pending = [a for a in data["applicants"] if a.get("status") == "pending"]
    
    if not pending:
        raise HTTPException(status_code=400, detail="No pending applicants")
    
    # Run in background
    background_tasks.add_task(bot_runner.run, center)
    
    return {"message": "Bot started", "center": center}

@app.post("/api/stop")
async def stop_bot():
    """Force stop the bot - immediately kill browser"""
    await bot_runner.force_stop()
    return {"message": "Bot force stopped"}

# Reset applicant status
@app.post("/api/applicants/{applicant_id}/reset")
async def reset_applicant(applicant_id: str):
    """Reset an applicant's status to pending"""
    data = load_data()
    
    for a in data["applicants"]:
        if a["id"] == applicant_id:
            a["status"] = "pending"
            a["last_booked"] = None
            save_data(data)
            return a
    
    raise HTTPException(status_code=404, detail="Applicant not found")

# ============================================
# Entry Point
# ============================================

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Qatar Visa Bot - Web Control Panel")
    print("=" * 50)
    print(f"\nOpen in browser: http://localhost:8000")
    print("Press Ctrl+C to stop\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
