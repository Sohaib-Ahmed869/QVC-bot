 
import asyncio
import json
import uuid
import logging
import tempfile
import shutil
import os
from datetime import datetime, time
from pathlib import Path
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
import uvicorn
from config import config

# Configure logging - write to both console and file
log_format = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')

# Configure root logger ONCE to avoid duplicate messages
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Only add handlers if they haven't been added yet
if not root_logger.handlers:
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)
    
    # File handler
    try:
        file_handler = logging.FileHandler('visa_bot.log', mode='a', encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(log_format)
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not create log file: {e}")

# Get module logger (inherits from root, no extra handlers needed)
logger = logging.getLogger(__name__)


# ============================================
# Pydantic Models
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

class BotRunRequest(BaseModel):
    center: str = "Islamabad"
    applicant_ids: List[str] = []  # NEW: Specific applicants to run
    max_parallel: int = 2  # NEW: Max parallel sessions (1-4)

class SessionStatus(BaseModel):
    """Status of a single parallel session"""
    session_id: str
    applicant_id: str
    passport_number: str
    status: str  # starting, logging_in, polling, slot_found, completed, failed, stopped
    ip: Optional[str] = None
    poll_count: int = 0
    started_at: Optional[str] = None
    message: Optional[str] = None

class BotStatus(BaseModel):
    running: bool = False
    sessions: List[SessionStatus] = []
    logs: List[dict] = []
    log_cursor: int = 0
    slot_found: bool = False
    slot_details: Optional[dict] = None


# ============================================
# Data Storage
# ============================================

DATA_FILE = Path(__file__).parent / "applicants.json"

def load_data() -> dict:
    """Load data from JSON file"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load data: {e}")
    return {
        "applicants": [],
        "schedule": {
            "enabled": True,
            "days": []
        },
        "settings": {
            "max_parallel": 2
        }
    }

def save_data(data: dict):
    """Save data to JSON file atomically"""
    try:
        # Write to temp file first, then atomic move
        with tempfile.NamedTemporaryFile(
            'w',
            dir=DATA_FILE.parent,
            delete=False,
            suffix='.tmp',
            encoding='utf-8'
        ) as f:
            json.dump(data, f, indent=2)
            temp_path = Path(f.name)
        
        # Atomic move (replace existing file)
        temp_path.replace(DATA_FILE)
        
    except Exception as e:
        logger.error(f"Failed to save data: {e}")
        # Fallback to direct write if temp file fails
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e2:
            logger.error(f"Fallback save also failed: {e2}")


# ============================================
# Session Data Class
# ============================================

@dataclass
class ParallelSession:
    """Represents a single browser session running for one applicant"""
    session_id: str
    applicant_id: str
    passport_number: str
    status: str = "starting"  # starting, logging_in, polling, slot_found, completed, failed, stopped
    ip: Optional[str] = None
    poll_count: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    message: Optional[str] = None
    task: Optional[asyncio.Task] = None
    browser: Any = None  # BrowserEngine instance
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "applicant_id": self.applicant_id,
            "passport_number": self.passport_number,
            "status": self.status,
            "ip": self.ip,
            "poll_count": self.poll_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "message": self.message
        }


# ============================================
# Parallel Bot Runner
# ============================================

from proxy_manager import ProxyManager

class ParallelBotRunner:
    """
    Manages parallel bot execution for multiple applicants.
    Each applicant runs in its own browser with its own proxy IP.
    """
    
    MAX_PARALLEL_LIMIT = 4  # Hard limit
    
    def __init__(self):
        self.running = False
        self.sessions: Dict[str, ParallelSession] = {}  # session_id -> ParallelSession
        self.logs: List[dict] = []
        self._stop_requested = False
        self._slot_found = False
        self._slot_details: Optional[dict] = None
        self._lock = asyncio.Lock()
        self._log_cursor = 0
        self._proxy_managers: Dict[str, ProxyManager] = {}  # session_id -> ProxyManager
        
    def add_log(self, message: str, log_type: str = "", session_id: str = None, passport: str = None):
        """Add log entry with timestamp and optional session context"""
        prefix = ""
        if passport:
            prefix = f"[{passport}] "
        elif session_id and session_id in self.sessions:
            prefix = f"[{self.sessions[session_id].passport_number}] "
        
        self.logs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": f"{prefix}{message}",
            "type": log_type,
            "session_id": session_id
        })
        # Keep only last 200 logs (more for parallel sessions)
        if len(self.logs) > 200:
            trim_count = len(self.logs) - 200
            self._log_cursor = max(0, self._log_cursor - trim_count)
            self.logs = self.logs[-200:]
        logger.info(f"[Bot] {prefix}{message}")
    
    def get_logs_since(self, cursor: int = 0) -> tuple:
        """Get logs since cursor, return (logs, new_cursor)"""
        if cursor < 0:
            cursor = 0
        new_logs = self.logs[cursor:]
        return new_logs, len(self.logs)
    
    def _create_proxy_manager(self, session_id: str) -> Optional[ProxyManager]:
        """Create a dedicated proxy manager for a session"""
        if not config.PROXY_ENABLED:
            return None
        
        pm = ProxyManager(
            username=config.PROXY_USERNAME,
            password=config.PROXY_PASSWORD,
            host=config.PROXY_HOST,
            port=config.PROXY_PORT,
            sticky_duration_mins=config.PROXY_STICKY_MINS,
            max_rotations_per_session=config.PROXY_MAX_ROTATIONS,
        )
        self._proxy_managers[session_id] = pm
        return pm
    
    def _calculate_remaining_schedule_time(self) -> int:
        """Calculate remaining time in current schedule window (in seconds)."""
        DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        DEFAULT_DURATION = 3600  # 1 hour fallback
        
        try:
            data = load_data()
            schedule = data.get("schedule", {})
            
            if not schedule.get("enabled", False):
                return DEFAULT_DURATION
            
            days = schedule.get("days", [])
            if not days:
                return DEFAULT_DURATION
            
            now = datetime.now()
            current_minutes = now.hour * 60 + now.minute
            current_day_name = DAYS_OF_WEEK[now.weekday()]
            
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
                    
                    # Check if we're currently in this window
                    in_window = False
                    if end_minutes < start_minutes:
                        # Overnight schedule
                        if current_minutes >= start_minutes:
                            remaining_mins = (24 * 60 - current_minutes) + end_minutes
                            in_window = True
                        elif current_minutes < end_minutes:
                            remaining_mins = end_minutes - current_minutes
                            in_window = True
                    else:
                        # Normal schedule
                        if start_minutes <= current_minutes < end_minutes:
                            remaining_mins = end_minutes - current_minutes
                            in_window = True
                    
                    if in_window:
                        remaining_secs = remaining_mins * 60
                        logger.info(f"Schedule window ends at {end_time}, {remaining_mins} min remaining")
                        return max(remaining_secs, 300)  # Minimum 5 minutes
            
            return DEFAULT_DURATION
            
        except Exception as e:
            logger.error(f"Error calculating schedule time: {e}")
            return DEFAULT_DURATION
    
    async def _run_single_session(
        self,
        session: ParallelSession,
        applicant_data: dict,
        center: str,
        proxy_manager: Optional[ProxyManager]
    ):
        """
        Run a single browser session for one applicant.
        This is the worker function that runs in parallel.
        """
        from browser_engine import BrowserEngine
        from config import Applicant as ConfigApplicant
        
        session_id = session.session_id
        passport = session.passport_number
        
        try:
            self.add_log(f"Starting session...", session_id=session_id, passport=passport)
            session.status = "starting"
            
            # Create applicant config object
            app_obj = ConfigApplicant(
                country=applicant_data["country"],
                passport_number=applicant_data["passport_number"],
                visa_number=applicant_data["visa_number"],
                mobile=applicant_data["mobile"],
                email=applicant_data["email"],
                row_index=0
            )
            
            # Rotate proxy to get unique IP
            if proxy_manager:
                await proxy_manager.rotate(reason="new_parallel_session")
            
            # Create browser engine
            browser = BrowserEngine(proxy_manager=proxy_manager)
            session.browser = browser
            
            # Start browser
            await browser.start()
            
            # Verify and log IP
            if proxy_manager:
                ip = await proxy_manager.verify_ip()
                session.ip = ip
                self.add_log(f"Connected with IP: {ip}", session_id=session_id, passport=passport)
            
            # Check if stop requested
            if self._stop_requested or self._slot_found:
                self.add_log("Stop requested, closing session", session_id=session_id, passport=passport)
                await browser.close()
                session.status = "stopped"
                return
            
            # Calculate session duration
            session_duration = min(
                config.SESSION_DURATION_MINUTES * 60,
                self._calculate_remaining_schedule_time()
            )
            
            self.add_log(f"Session duration: {session_duration // 60} min", session_id=session_id, passport=passport)
            
            # Update status
            session.status = "logging_in"
            
            # Define slot found callback
            async def on_slot_found(slot_result):
                """Called when SlotHunter finds a slot"""
                self._slot_found = True
                self._slot_details = {
                    "passport_number": passport,
                    "session_id": session_id,
                    "date": str(slot_result.date),
                    "time": slot_result.time,
                    "center": slot_result.center,
                    "found_at": datetime.now().isoformat()
                }
                self.add_log(f"🎉 SLOT FOUND! Date: {slot_result.date}", "success", session_id=session_id, passport=passport)
            
            # Run the booking pipeline
            success = await browser.book_appointment(
                app_obj,
                config.DATE_RANGE_START,
                config.DATE_RANGE_END,
                max_hunt_duration=session_duration,
                center=center
            )
            
            # Close browser
            await browser.close()
            session.browser = None
            
            if self._slot_found and self._slot_details and self._slot_details.get("session_id") == session_id:
                session.status = "slot_found"
                self.add_log(f"✅ Session completed - SLOT FOUND!", "success", session_id=session_id, passport=passport)
            elif success:
                session.status = "slot_found"
                self._slot_found = True
                self._slot_details = {
                    "passport_number": passport,
                    "session_id": session_id,
                    "found_at": datetime.now().isoformat()
                }
                self.add_log(f"✅ Session completed - SLOT FOUND!", "success", session_id=session_id, passport=passport)
            else:
                session.status = "completed"
                self.add_log(f"Session completed - no slots found", session_id=session_id, passport=passport)
                
        except asyncio.CancelledError:
            self.add_log(f"Session cancelled", session_id=session_id, passport=passport)
            session.status = "stopped"
            if session.browser:
                try:
                    await session.browser.close()
                except:
                    pass
                session.browser = None
                
        except Exception as e:
            logger.exception(f"Session error for {passport}: {e}")
            self.add_log(f"Error: {str(e)[:50]}", "error", session_id=session_id, passport=passport)
            session.status = "failed"
            session.message = str(e)[:100]
            if session.browser:
                try:
                    await session.browser.close()
                except:
                    pass
                session.browser = None
    
    async def run(self, applicant_ids: List[str], center: str = "Islamabad", max_parallel: int = 2):
        """
        Run the bot for selected applicants in parallel.
        
        Args:
            applicant_ids: List of applicant IDs to process
            center: Visa center name
            max_parallel: Maximum number of parallel sessions (1-4)
        """
        async with self._lock:
            if self.running:
                return
            self.running = True
        
        self._stop_requested = False
        self._slot_found = False
        self._slot_details = None
        self.sessions.clear()
        self._proxy_managers.clear()
        
        # Validate max_parallel
        max_parallel = min(max(1, max_parallel), self.MAX_PARALLEL_LIMIT)
        
        self.add_log(f"=" * 50)
        self.add_log(f"PARALLEL BOT STARTED")
        self.add_log(f"Center: {center}")
        self.add_log(f"Applicants: {len(applicant_ids)}")
        self.add_log(f"Max parallel: {max_parallel}")
        self.add_log(f"=" * 50)
        
        try:
            # Load applicant data
            data = load_data()
            applicants_map = {a["id"]: a for a in data["applicants"]}
            
            # Filter to only requested applicants
            selected_applicants = []
            for app_id in applicant_ids:
                if app_id in applicants_map:
                    selected_applicants.append(applicants_map[app_id])
                else:
                    self.add_log(f"Applicant {app_id} not found, skipping", "error")
            
            if not selected_applicants:
                self.add_log("No valid applicants to process", "error")
                return
            
            # Update status to processing
            for app in selected_applicants:
                for a in data["applicants"]:
                    if a["id"] == app["id"]:
                        a["status"] = "processing"
                        break
            save_data(data)
            
            # Create sessions for each applicant (up to max_parallel)
            tasks = []
            
            for i, applicant in enumerate(selected_applicants[:max_parallel]):
                session_id = f"sess_{uuid.uuid4().hex[:8]}"
                
                session = ParallelSession(
                    session_id=session_id,
                    applicant_id=applicant["id"],
                    passport_number=applicant["passport_number"],
                    status="starting",
                    started_at=datetime.now()
                )
                self.sessions[session_id] = session
                
                # Create dedicated proxy manager for this session
                proxy_manager = self._create_proxy_manager(session_id)
                
                # Create task
                task = asyncio.create_task(
                    self._run_single_session(session, applicant, center, proxy_manager)
                )
                session.task = task
                tasks.append(task)
                
                self.add_log(f"Created session {i+1}/{min(len(selected_applicants), max_parallel)}", 
                           session_id=session_id, passport=applicant["passport_number"])
                
                # Small delay between starting sessions to stagger them
                await asyncio.sleep(2)
            
            # Wait for all tasks to complete OR slot found OR stop requested
            self.add_log(f"All {len(tasks)} sessions started, waiting for completion...")
            
            while tasks:
                # Check if slot found - stop all other sessions
                if self._slot_found:
                    self.add_log("🎉 SLOT FOUND - Stopping all sessions!", "success")
                    for session in self.sessions.values():
                        if session.task and not session.task.done():
                            session.task.cancel()
                    break
                
                # Check if stop requested
                if self._stop_requested:
                    self.add_log("Stop requested - cancelling all sessions")
                    for session in self.sessions.values():
                        if session.task and not session.task.done():
                            session.task.cancel()
                    break
                
                # Wait for any task to complete
                done, pending = await asyncio.wait(
                    tasks, 
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Update tasks list
                tasks = list(pending)
                
                # Check completed tasks
                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Task error: {e}")
            
            # Wait for all cancellations to complete
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # Update applicant statuses
            data = load_data()
            for session in self.sessions.values():
                for a in data["applicants"]:
                    if a["id"] == session.applicant_id:
                        if session.status == "slot_found":
                            a["status"] = "slot_found"
                            a["slot_detected_at"] = datetime.now().isoformat()
                        elif session.status == "stopped":
                            a["status"] = "pending"  # Reset to pending if stopped
                        elif session.status == "failed":
                            a["status"] = "failed"
                        else:
                            a["status"] = "no_slot"
                        break
            save_data(data)
            
            # Final summary
            self.add_log(f"=" * 50)
            if self._slot_found:
                self.add_log(f"🎉 SUCCESS! Slot found for {self._slot_details.get('passport_number', 'unknown')}", "success")
                self.add_log(f"Details: {self._slot_details}", "success")
            else:
                self.add_log(f"All sessions completed - no slots found")
            self.add_log(f"=" * 50)
            
        except Exception as e:
            logger.exception(f"Parallel bot runner error: {e}")
            self.add_log(f"Runner error: {str(e)}", "error")
        finally:
            self.running = False
            
            # Cleanup proxy managers
            self._proxy_managers.clear()
            
            # Log final stats
            completed = sum(1 for s in self.sessions.values() if s.status in ["completed", "slot_found"])
            failed = sum(1 for s in self.sessions.values() if s.status == "failed")
            stopped = sum(1 for s in self.sessions.values() if s.status == "stopped")
            self.add_log(f"Final: {completed} completed, {failed} failed, {stopped} stopped")
    
    def stop(self):
        """Request all sessions to stop (graceful)"""
        self._stop_requested = True
        self.add_log("Stop requested for all sessions...")
    
    async def force_stop(self):
        """Force stop - immediately kill all browsers"""
        self._stop_requested = True
        logger.info("Force stop initiated for all sessions")
        
        for session_id, session in self.sessions.items():
            if session.browser:
                try:
                    await session.browser.close()
                    logger.info(f"Browser force closed for session {session_id}")
                except Exception as e:
                    logger.error(f"Error force closing browser {session_id}: {e}")
                session.browser = None
            
            if session.task and not session.task.done():
                session.task.cancel()
            
            session.status = "stopped"
        
        self.running = False
        self.add_log("All sessions force stopped", "error")
    
    def get_status(self) -> dict:
        """Get current status of all sessions"""
        return {
            "running": self.running,
            "sessions": [s.to_dict() for s in self.sessions.values()],
            "slot_found": self._slot_found,
            "slot_details": self._slot_details
        }


# Global bot runner instance (now parallel)
bot_runner = ParallelBotRunner()


# ============================================
# Scheduler (Updated for parallel)
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
                            
                            if end_minutes < start_minutes:
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
                            # Get max parallel from settings
                            settings = data.get("settings", {})
                            max_parallel = settings.get("max_parallel", 2)
                            
                            # Get pending applicant IDs (up to max_parallel)
                            applicant_ids = [a["id"] for a in pending[:max_parallel]]
                            
                            logger.info(f"Scheduled run triggered for {len(applicant_ids)} applicants")
                            asyncio.create_task(bot_runner.run(applicant_ids, max_parallel=max_parallel))
        
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
    asyncio.create_task(check_schedule())
    logger.info("Scheduler started")
    logger.info("Qatar Visa Bot Web Server is ready (Parallel Mode)")
    yield
    logger.info("Shutting down - cleaning up...")
    await bot_runner.force_stop()
    logger.info("Shutdown complete")

app = FastAPI(
    title="Qatar Visa Bot API",
    description="REST API for the visa bot control panel with parallel session support",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Determine web directory location
web_dir = Path(__file__).parent / "web"
if not web_dir.exists():
    web_dir = Path(__file__).parent / "static"

# Serve static files if directory exists
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    logger.info(f"Serving static files from: {web_dir}")
else:
    logger.warning(f"Static files directory not found: {web_dir}")


# ============================================
# API Routes
# ============================================

@app.get("/")
async def root():
    """Serve the main page"""
    index_path = web_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "message": "Qatar Visa Bot API",
        "version": "2.0.0",
        "status": "running",
        "features": ["parallel_sessions"]
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "bot_running": bot_runner.running,
        "active_sessions": len([s for s in bot_runner.sessions.values() if s.status not in ["completed", "failed", "stopped"]]),
        "timestamp": datetime.now().isoformat()
    }

# Serve CSS and JS directly
@app.get("/styles.css")
async def styles():
    css_path = web_dir / "styles.css"
    if css_path.exists():
        return FileResponse(str(css_path), media_type="text/css")
    raise HTTPException(status_code=404, detail="CSS file not found")

@app.get("/app.js")
async def script():
    js_path = web_dir / "app.js"
    if js_path.exists():
        return FileResponse(str(js_path), media_type="application/javascript")
    raise HTTPException(status_code=404, detail="JS file not found")


# --- Applicants ---

@app.get("/api/applicants")
async def list_applicants():
    """Get all applicants"""
    data = load_data()
    return {"applicants": data.get("applicants", [])}

@app.post("/api/applicants", response_model=Applicant)
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
    
    logger.info(f"Created applicant: {new_applicant['passport_number']}")
    return new_applicant

@app.put("/api/applicants/{applicant_id}", response_model=Applicant)
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
                "status": "pending"
            })
            save_data(data)
            logger.info(f"Updated applicant: {applicant_id}")
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
    logger.info(f"Deleted applicant: {applicant_id}")
    return {"message": "Applicant deleted"}

@app.post("/api/applicants/{applicant_id}/reset", response_model=Applicant)
async def reset_applicant(applicant_id: str):
    """Reset an applicant's status to pending"""
    data = load_data()
    
    for a in data["applicants"]:
        if a["id"] == applicant_id:
            a["status"] = "pending"
            a["last_booked"] = None
            save_data(data)
            logger.info(f"Reset applicant: {applicant_id}")
            return a
    
    raise HTTPException(status_code=404, detail="Applicant not found")


# --- Schedule ---

@app.get("/api/schedule", response_model=Schedule)
async def get_schedule():
    """Get current schedule"""
    data = load_data()
    schedule = data.get("schedule", {
        "enabled": True,
        "days": []
    })
    return schedule

@app.post("/api/schedule", response_model=Schedule)
async def update_schedule(schedule: Schedule):
    """Update schedule"""
    data = load_data()
    data["schedule"] = schedule.model_dump()
    save_data(data)
    logger.info("Schedule updated")
    return data["schedule"]


# --- Settings ---

@app.get("/api/settings")
async def get_settings():
    """Get bot settings"""
    data = load_data()
    settings = data.get("settings", {
        "max_parallel": 2
    })
    return settings

@app.post("/api/settings")
async def update_settings(settings: dict):
    """Update bot settings"""
    data = load_data()
    
    # Validate max_parallel
    if "max_parallel" in settings:
        settings["max_parallel"] = min(max(1, int(settings["max_parallel"])), ParallelBotRunner.MAX_PARALLEL_LIMIT)
    
    data["settings"] = {**data.get("settings", {}), **settings}
    save_data(data)
    logger.info(f"Settings updated: {settings}")
    return data["settings"]


# --- Bot Control (Updated for parallel) ---

@app.get("/api/status")
async def get_status(log_cursor: int = 0):
    """Get current bot status with all parallel sessions"""
    logs, new_cursor = bot_runner.get_logs_since(log_cursor)
    
    status = bot_runner.get_status()
    
    # Get current applicant statuses for live updates
    data = load_data()
    applicant_updates = []
    for a in data["applicants"]:
        if a.get("status") in ["processing", "completed", "failed", "slot_found", "no_slot"]:
            applicant_updates.append({
                "id": a["id"],
                "status": a["status"]
            })
    
    return {
        "running": status["running"],
        "sessions": status["sessions"],
        "slot_found": status["slot_found"],
        "slot_details": status["slot_details"],
        "logs": logs,
        "log_cursor": new_cursor,
        "applicants": applicant_updates
    }

@app.post("/api/run")
async def run_bot(background_tasks: BackgroundTasks, request: BotRunRequest):
    """Start the bot for selected applicants in parallel"""
    if bot_runner.running:
        raise HTTPException(status_code=400, detail="Bot is already running")
    
    data = load_data()
    
    # If no specific applicants provided, use all pending
    if not request.applicant_ids:
        pending = [a for a in data["applicants"] if a.get("status") == "pending"]
        if not pending:
            raise HTTPException(status_code=400, detail="No pending applicants")
        request.applicant_ids = [a["id"] for a in pending[:request.max_parallel]]
    
    # Validate applicants exist
    all_ids = {a["id"] for a in data["applicants"]}
    invalid_ids = [aid for aid in request.applicant_ids if aid not in all_ids]
    if invalid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid applicant IDs: {invalid_ids}")
    
    # Validate max_parallel
    max_parallel = min(max(1, request.max_parallel), ParallelBotRunner.MAX_PARALLEL_LIMIT)
    
    # Use all provided applicant_ids (max_parallel controls how many run simultaneously)
    applicant_ids = request.applicant_ids
    
    # Run in background
    background_tasks.add_task(
        bot_runner.run,
        applicant_ids=applicant_ids,
        center=request.center,
        max_parallel=max_parallel
    )
    
    logger.info(f"Bot started for {len(applicant_ids)} applicants (max parallel: {max_parallel})")
    return {
        "message": "Bot started",
        "center": request.center,
        "applicant_ids": applicant_ids,
        "max_parallel": max_parallel
    }

@app.post("/api/stop")
async def stop_bot():
    """Force stop all parallel sessions"""
    await bot_runner.force_stop()
    logger.info("Bot stopped by user")
    return {"message": "All sessions force stopped"}


# ============================================
# Entry Point
# ============================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    print("\n" + "=" * 60)
    print("Qatar Visa Bot - Web Control Panel (Parallel Mode)")
    print("=" * 60)
    print(f"\nServer starting on {host}:{port}")
    print(f"Open in browser: http://localhost:{port}")
    print(f"Max parallel sessions: {ParallelBotRunner.MAX_PARALLEL_LIMIT}")
    print("Press Ctrl+C to stop\n")
    
    uvicorn.run(
        "web_server:app",
        host=host,
        port=port,
        log_level="info",
        access_log=True
    )