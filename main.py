import asyncio
import argparse
import sys
from datetime import date, datetime
from pathlib import Path
import logging
from config import config, Applicant
from data_handler import DataHandler, create_template
from browser_engine import BrowserEngine
from bandwidth_monitor import bandwidth_monitor
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, mode='a', encoding='utf-8')
    ]
)

for lib in ["websockets", "asyncio", "nodriver", "urllib3", "uc"]:
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("main")

class VisaBot:
    def __init__(
        self,
        excel_path: str, 
        start_date: date,
        end_date: date,
        headless: bool = False
    ):
        self.excel_path = excel_path
        self.start_date = start_date
        self.end_date = end_date
        self.headless = headless
        
        self.data_handler: DataHandler = None
        self.browser: BrowserEngine = None
        
        self.results = {
            "success": [],
            "failed": [],
            "skipped": []
        }
    
    async def initialize(self):
        
        logger.info("=" * 60)
        logger.info("Qatar Visa Appointment Bot")
        logger.info("=" * 60)
        logger.info(f"Date range: {self.start_date} to {self.end_date}")
        logger.info(f"Excel file: {self.excel_path}")
        logger.info(f"Headless: {self.headless}")
        
        # Load applicant data
        self.data_handler = DataHandler(self.excel_path)
        applicants = self.data_handler.load()
        logger.info(f"Loaded {len(applicants)} applicants")
        
        # Update config
        config.HEADLESS = self.headless
        
        # Initialize browser
        self.browser = BrowserEngine()
        await self.browser.start()
        
        return applicants
    
    async def process_applicant(self, applicant: Applicant) -> bool:
        """Process a single applicant"""
        logger.info("-" * 40)
        logger.info(f"Processing: {applicant.passport_number}")
        logger.info(f"Email: {applicant.email}")
        logger.info("-" * 40)
        
        success = False
        bandwidth_monitor.start_session(applicant.passport_number)
        try:
            # Attempt booking
            # This handles Login -> Details -> Slot Hunting (via SlotHunter) -> Confirmation
            success = await self.browser.book_appointment(
                applicant,
                self.start_date,
                self.end_date
            )
            
            if success:
                self.results["success"].append(applicant)
                logger.info(f"✓ SUCCESS: {applicant.passport_number}")
            else:
                self.results["failed"].append(applicant)
                logger.error(f"✗ FAILED: {applicant.passport_number}")
                
                # Take screenshot for debugging
                await self.browser.screenshot(
                    f"failed_{applicant.passport_number}.png"
                )
        except Exception as e:
            logger.exception(f"Exception processing {applicant.passport_number}: {e}")
            self.results["failed"].append(applicant)
        finally:
            bandwidth_monitor.print_report()
            bandwidth_monitor.end_session()
        
        return success
    
    async def run(self):
        """Main execution loop"""
        applicants = await self.initialize()
        
        if not applicants:
            logger.error("No applicants to process")
            return
        
        for idx, applicant in enumerate(applicants, 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"Applicant {idx}/{len(applicants)}")
            logger.info(f"{'='*60}")
            
            try:
                success = await self.process_applicant(applicant)
                
                if success:
                    logger.info(f"✓ Booking completed for {applicant.passport_number}")
                else:
                    logger.warning(f"✗ Booking failed for {applicant.passport_number}")
                
                # Brief pause between applicants
                if idx < len(applicants):
                    logger.info("Waiting before next applicant...")
                    await asyncio.sleep(5)
                    
                    # Refresh browser for next applicant
                    try:
                        await self.browser.page.get(config.BASE_URL)
                        await asyncio.sleep(2)
                    except Exception as e:
                        logger.error(f"Failed to refresh page: {e}")
                    
            except Exception as e:
                logger.exception(f"Critical error: {e}")
                self.results["failed"].append(applicant)
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print execution summary"""
        logger.info("\n" + "=" * 60)
        logger.info("EXECUTION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Successful: {len(self.results['success'])}")
        for a in self.results['success']:
            logger.info(f"  ✓ {a.passport_number} - {a.email}")
        
        logger.info(f"Failed: {len(self.results['failed'])}")
        for a in self.results['failed']:
            logger.info(f"  ✗ {a.passport_number} - {a.email}")
        
        logger.info(f"Skipped: {len(self.results['skipped'])}")
        logger.info("=" * 60)
    
    async def cleanup(self):
        """Clean up resources"""
        if self.browser:
            await self.browser.close()
        if self.data_handler:
            self.data_handler.close()
        logger.info("Cleanup complete")


def parse_date(date_str: str) -> date:
    """Parse date from string"""
    for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"]:
        try:
            return datetime.strptime(date_str, fmt).date()
        except:
            continue
    raise ValueError(f"Invalid date format: {date_str}")


def main():
    parser = argparse.ArgumentParser(
        description="Qatar Visa Center Appointment Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --start 2025-02-01 --end 2025-03-31
  python main.py --excel my_applicants.xlsx --headless
  python main.py --create-template
        """
    )
    
    parser.add_argument(
        "--excel", "-e",
        default="applicants.xlsx",
        help="Path to Excel file with applicant data"
    )
    parser.add_argument(
        "--start", "-s",
        default=None,
        help="Start date for slot search (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end", "-n",
        default=None,
        help="End date for slot search (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode"
    )
    parser.add_argument(
        "--create-template",
        action="store_true",
        help="Create sample Excel template and exit"
    )
    
    args = parser.parse_args()
    
    # Create template if requested
    if args.create_template:
        template_path = create_template()
        print(f"Template created: {template_path}")
        return
    
    # Parse dates
    start_date = parse_date(args.start) if args.start else config.DATE_RANGE_START
    end_date = parse_date(args.end) if args.end else config.DATE_RANGE_END
    
    # Validate
    if start_date > end_date:
        print("Error: Start date must be before end date")
        sys.exit(1)
    
    if not Path(args.excel).exists():
        print(f"Error: Excel file not found: {args.excel}")
        print("Run with --create-template to create a sample file")
        sys.exit(1)
    
    # Run bot
    bot = VisaBot(
        excel_path=args.excel,
        start_date=start_date,
        end_date=end_date,
        headless=args.headless
    )
    
    async def execute():
        try:
            await bot.run()
        finally:
            await bot.cleanup()
    
    asyncio.run(execute())


if __name__ == "__main__":
    main()
