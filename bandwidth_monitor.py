
import asyncio
import json
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    import nodriver as uc
    from nodriver import cdp
    HAS_NODRIVER = True
except ImportError:
    HAS_NODRIVER = False
    print("Warning: nodriver not installed, bandwidth monitoring will be limited")

logger = logging.getLogger(__name__)


@dataclass
class RequestLog:
    """Single request/response log entry"""
    timestamp: datetime
    url: str
    method: str
    request_size: int  # bytes
    response_size: int  # bytes
    resource_type: str  # document, script, image, xhr, etc.
    category: str  # login, polling, navigation, etc.


@dataclass
class SessionStats:
    """Stats for one applicant session"""
    applicant_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    requests: List[RequestLog] = field(default_factory=list)
    
    @property
    def total_downloaded(self) -> int:
        return sum(r.response_size for r in self.requests)
    
    @property
    def total_uploaded(self) -> int:
        return sum(r.request_size for r in self.requests)
    
    @property
    def total_bandwidth(self) -> int:
        return self.total_downloaded + self.total_uploaded
    
    @property
    def request_count(self) -> int:
        return len(self.requests)
    
    def by_category(self) -> Dict[str, dict]:
        """Bandwidth grouped by category"""
        categories = {}
        for req in self.requests:
            if req.category not in categories:
                categories[req.category] = {"count": 0, "bytes": 0}
            categories[req.category]["count"] += 1
            categories[req.category]["bytes"] += req.response_size + req.request_size
        return categories
    
    def by_resource_type(self) -> Dict[str, dict]:
        """Bandwidth grouped by resource type"""
        types = {}
        for req in self.requests:
            if req.resource_type not in types:
                types[req.resource_type] = {"count": 0, "bytes": 0}
            types[req.resource_type]["count"] += 1
            types[req.resource_type]["bytes"] += req.response_size + req.request_size
        return types


class BandwidthMonitor:
   
    def __init__(self, log_file: str = "bandwidth_log.json"):
        self.log_file = Path(log_file)
        self.sessions: List[SessionStats] = []
        self.current_session: Optional[SessionStats] = None
        self._request_map: Dict[str, dict] = {}  # requestId -> request data
        self._current_category: str = "unknown"
        self._enabled: bool = False
        self._page = None
        
    async def attach_to_page(self, page) -> bool:

        if not HAS_NODRIVER:
            logger.error("nodriver not available")
            return False
            
        try:
            self._page = page
            
            # Enable network domain via CDP
            await page.send(cdp.network.enable())
            

            page.add_handler(
                cdp.network.RequestWillBeSent,
                self._on_request_will_be_sent
            )
            page.add_handler(
                cdp.network.ResponseReceived,
                self._on_response_received
            )
            page.add_handler(
                cdp.network.LoadingFinished,
                self._on_loading_finished
            )
            page.add_handler(
                cdp.network.LoadingFailed,
                self._on_loading_failed
            )
            
            self._enabled = True
            logger.info("✓ Bandwidth monitor attached successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to attach bandwidth monitor: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def start_session(self, applicant_id: str):
        """Start tracking a new applicant session"""
        self.current_session = SessionStats(
            applicant_id=applicant_id,
            start_time=datetime.now()
        )
        self._request_map.clear()
        logger.info(f"Started bandwidth tracking for: {applicant_id}")
    
    def end_session(self):
        """End current session and save stats"""
        if self.current_session:
            self.current_session.end_time = datetime.now()
            self.sessions.append(self.current_session)
            self._save_session(self.current_session)
            logger.info(f"Ended bandwidth session: {self.current_session.applicant_id}")
            self.current_session = None
    
    def set_category(self, category: str):
        """Set category for subsequent requests (login, polling, etc.)"""
        self._current_category = category
        logger.debug(f"Request category set to: {category}")

    async def _on_request_will_be_sent(self, event: 'cdp.network.RequestWillBeSent'):
        """Handle outgoing request"""
        if not self.current_session:
            return
        
        try:
            request_id = str(event.request_id)
            request = event.request
            
            # Estimate request size (headers + body)
            headers_size = 0
            if request.headers:
                headers_size = len(str(request.headers))
            
            body_size = 0
            if hasattr(request, 'post_data') and request.post_data:
                body_size = len(request.post_data)
            
            # Get resource type
            resource_type = "Other"
            if event.type_:
                resource_type = str(event.type_.value) if hasattr(event.type_, 'value') else str(event.type_)
            
            self._request_map[request_id] = {
                "url": request.url,
                "method": request.method,
                "request_size": headers_size + body_size,
                "timestamp": datetime.now(),
                "category": self._current_category,
                "type": resource_type
            }
        except Exception as e:
            logger.debug(f"Error in request handler: {e}")
    
    async def _on_response_received(self, event: 'cdp.network.ResponseReceived'):
        """Handle response received"""
        if not self.current_session:
            return
        
        try:
            request_id = str(event.request_id)
            response = event.response
            
            if request_id in self._request_map:
                # Store response headers size
                headers_size = 0
                if response.headers:
                    headers_size = len(str(response.headers))
                
                self._request_map[request_id]["response_headers_size"] = headers_size
                self._request_map[request_id]["status"] = response.status
        except Exception as e:
            logger.debug(f"Error in response handler: {e}")
    
    async def _on_loading_finished(self, event: 'cdp.network.LoadingFinished'):
        """Handle request completion - get final encoded size"""
        if not self.current_session:
            return
        
        try:
            request_id = str(event.request_id)
            encoded_length = event.encoded_data_length or 0
            
            if request_id in self._request_map:
                req_data = self._request_map[request_id]
                
                log_entry = RequestLog(
                    timestamp=req_data["timestamp"],
                    url=req_data["url"],
                    method=req_data["method"],
                    request_size=req_data["request_size"],
                    response_size=encoded_length,
                    resource_type=req_data.get("type", "Other"),
                    category=req_data["category"]
                )
                
                self.current_session.requests.append(log_entry)
                
                # Log significant requests (> 100KB)
                if encoded_length > 100000:
                    logger.debug(
                        f"Large response: {self._truncate_url(req_data['url'])} "
                        f"= {self._format_bytes(encoded_length)}"
                    )
                
                del self._request_map[request_id]
        except Exception as e:
            logger.debug(f"Error in loading finished handler: {e}")
    
    async def _on_loading_failed(self, event: 'cdp.network.LoadingFailed'):
        """Clean up failed requests"""
        try:
            request_id = str(event.request_id)
            if request_id in self._request_map:
                del self._request_map[request_id]
        except Exception as e:
            logger.debug(f"Error in loading failed handler: {e}")
    
    
    def _save_session(self, session: SessionStats):
        """Append session to log file"""
        try:
            data = {
                "applicant_id": session.applicant_id,
                "start_time": session.start_time.isoformat(),
                "end_time": session.end_time.isoformat() if session.end_time else None,
                "total_requests": session.request_count,
                "total_downloaded_bytes": session.total_downloaded,
                "total_uploaded_bytes": session.total_uploaded,
                "total_bandwidth_bytes": session.total_bandwidth,
                "total_bandwidth_mb": round(session.total_bandwidth / (1024 * 1024), 3),
                "by_category": session.by_category(),
                "by_resource_type": session.by_resource_type(),
            }
            
            # Append to JSON lines file
            with open(self.log_file, "a") as f:
                f.write(json.dumps(data) + "\n")
                
            logger.info(f"Session saved to {self.log_file}")
                
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
    
    def print_report(self):
        """Print current session stats to console"""
        if not self.current_session:
            logger.info("No active session to report")
            return
        
        s = self.current_session
        duration = (datetime.now() - s.start_time).total_seconds()
        
        print("\n" + "=" * 60)
        print("BANDWIDTH REPORT")
        print("=" * 60)
        print(f"Applicant: {s.applicant_id}")
        print(f"Duration: {duration:.0f} seconds ({duration/60:.1f} minutes)")
        print(f"Total Requests: {s.request_count}")
        print("-" * 60)
        print(f"Downloaded: {self._format_bytes(s.total_downloaded)}")
        print(f"Uploaded: {self._format_bytes(s.total_uploaded)}")
        print(f"TOTAL: {self._format_bytes(s.total_bandwidth)}")
        print("-" * 60)
        
        if s.by_category():
            print("\nBy Category:")
            for cat, stats in sorted(s.by_category().items(), key=lambda x: -x[1]['bytes']):
                print(f"  {cat}: {stats['count']} requests, {self._format_bytes(stats['bytes'])}")
        
        if s.by_resource_type():
            print("\nBy Resource Type:")
            for rtype, stats in sorted(s.by_resource_type().items(), key=lambda x: -x[1]['bytes']):
                print(f"  {rtype}: {stats['count']} requests, {self._format_bytes(stats['bytes'])}")
        
        print("\n" + "-" * 60)
        print("COST ESTIMATES:")
        mb = s.total_bandwidth / (1024 * 1024)
        gb = mb / 1024
        print(f"  DataImpulse ($1/GB):     ${gb * 1:.4f}")
        print(f"  Smartproxy ($3.5/GB):    ${gb * 3.5:.4f}")
        print(f"  Proxy-Cheap (unlimited): $0.00 (flat rate per IP)")
        print("=" * 60 + "\n")
    
    def get_live_stats(self) -> dict:
        """Get current stats as dict (for periodic logging)"""
        if not self.current_session:
            return {}
        
        s = self.current_session
        return {
            "requests": s.request_count,
            "downloaded_mb": round(s.total_downloaded / (1024 * 1024), 3),
            "uploaded_mb": round(s.total_uploaded / (1024 * 1024), 3),
            "total_mb": round(s.total_bandwidth / (1024 * 1024), 3),
        }
    
    @staticmethod
    def _format_bytes(size: int) -> str:
        """Human readable byte size"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"
    
    @staticmethod
    def _truncate_url(url: str, max_len: int = 50) -> str:
        """Truncate URL for logging"""
        if len(url) <= max_len:
            return url
        return url[:max_len-3] + "..."


# Global instance for easy access
bandwidth_monitor = BandwidthMonitor()


if __name__ == "__main__":
    print("Bandwidth Monitor Module (nodriver compatible)")
    print("-" * 50)
    print("Usage in your bot:")
    print()
    print("  from bandwidth_monitor import bandwidth_monitor")
    print()
    print("  # In browser_engine.py start():")
    print("  await bandwidth_monitor.attach_to_page(self.page)")
    print()
    print("  # In main.py process_applicant():")
    print("  bandwidth_monitor.start_session(applicant.passport_number)")
    print("  # ... run booking ...")
    print("  bandwidth_monitor.print_report()")
    print("  bandwidth_monitor.end_session()")