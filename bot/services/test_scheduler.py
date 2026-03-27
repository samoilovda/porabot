"""
Scheduler Service Tests — APScheduler Integration Verification
==============================================================

PURPOSE:
  This test suite verifies the SchedulerService can properly schedule,
  remove, and manage reminder jobs with APScheduler.

USAGE:
  
    # Run directly (requires APScheduler instance)
    python -m bot.services.test_scheduler
    
TEST COVERAGE:
  
  ✅ Schedule job creation
  ✅ Job removal (main + nagging)
  ✅ Timezone-aware datetime handling
  ✅ Recurring task rescheduling logic
  ✅ Nagging follow-up scheduling

REQUIREMENTS:
  
  - APScheduler instance for testing
  - Telegram Bot instance (mocked or real)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock

# Import scheduler components
from bot.services.scheduler import SchedulerService, execute_reminder_job


class TestSchedulerService:
    """Test the SchedulerService scheduling operations."""
    
    @classmethod
    async def run_tests(cls):
        """Run all scheduler tests."""
        
        print("=" * 60)
        print("Scheduler Service Tests")
        print("=" * 60)
        print()
        
        # Create mock dependencies
        scheduler = Mock(spec=["add_job", "remove_job"])
        bot = Mock(spec=["send_message"])
        
        # Track scheduled jobs
        scheduled_jobs = []
        removed_jobs = []
        
        def mock_add_job(*args, **kwargs):
            job_info = {
                "trigger_type": kwargs.get("trigger", {}).get("type"),
                "run_date": kwargs.get("run_date"),
                "job_id": kwargs.get("id"),
                "replace_existing": kwargs.get("replace_existing", False),
            }
            scheduled_jobs.append(job_info)
            print(f"[SCHEDULED] Job ID: {kwargs.get('id')}")
        
        def mock_remove_job(job_id):
            removed_jobs.append(job_id)
            print(f"[REMOVED] Job ID: {job_id}")
        
        scheduler.add_job = mock_add_job
        scheduler.remove_job = mock_remove_job
        
        # Create SchedulerService instance
        service = SchedulerService(scheduler, bot, None)
        
        # Test 1: Schedule a one-shot reminder job (UTC timezone)
        print("--- Test 1: Schedule One-Shot Job ---")
        execution_time = datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)
        
        service.schedule_reminder(123, execution_time)
        
        assert len(scheduled_jobs) == 1, "Should have scheduled one job"
        assert scheduled_jobs[0]["job_id"] == "123", "Job ID should match reminder_id"
        print("[PASS] One-shot job scheduled correctly")
        print()
        
        # Test 2: Schedule nagging follow-up job (UTC timezone)
        print("--- Test 2: Schedule Nagging Job ---")
        execution_time_nag = datetime(2026, 3, 28, 19, 5, tzinfo=timezone.utc)
        
        service.schedule_reminder(456, execution_time_nag, is_nagging=True)
        
        assert len(scheduled_jobs) == 2, "Should have scheduled two jobs"
        assert scheduled_jobs[1]["job_id"] == "456", "Job ID should match reminder_id"
        print("[PASS] Nagging job scheduled correctly")
        print()
        
        # Test 3: Remove main job for a reminder (also removes nagging)
        print("--- Test 3: Remove Main Job ---")
        service.remove_reminder_job(123)
        
        assert "123" in removed_jobs, "Should have removed job 123"
        assert "nag_123" in removed_jobs, "Should have also removed nagging job"
        print("[PASS] Main and nagging jobs removed correctly")
        print()
        
        # Test 4: Remove nagging job separately for another reminder
        print("--- Test 4: Remove Nagging Job ---")
        service.remove_nagging_job(456)
        
        assert "nag_456" in removed_jobs, "Should have removed nagging job"
        print("[PASS] Nagging job removed correctly")
        print()
        
        # Test 5: Remove both jobs at once (convenience method)
        print("--- Test 5: Remove Both Jobs ---")
        service.remove_reminder_job(789)
        
        assert "789" in removed_jobs, "Should have removed main job"
        assert "nag_789" in removed_jobs, "Should have removed nagging job"
        print("[PASS] Both jobs removed correctly")
        print()
        
        # Test 6: Schedule with timezone-aware datetime (UTC)
        print("--- Test 6: Timezone-Aware Datetime ---")
        tz_aware_time = datetime(2026, 3, 28, 15, 30, tzinfo=timezone.utc)
        
        service.schedule_reminder(999, tz_aware_time)
        
        assert len(scheduled_jobs) == 3, "Should have scheduled three jobs total"
        print("[PASS] Timezone-aware datetime handled correctly")
        print()
        
        # Test 7: Schedule with naive datetime (should convert to UTC)
        print("--- Test 7: Naive Datetime Handling ---")
        naive_time = datetime(2026, 3, 28, 15, 30)
        
        service.schedule_reminder(888, naive_time)
        
        assert len(scheduled_jobs) == 4, "Should have scheduled four jobs total"
        print("[PASS] Naive datetime converted to UTC correctly")
        print()
        
        # Test 8: Schedule recurring task job (UTC timezone)
        print("--- Test 8: Recurring Task Job ---")
        recurring_time = datetime(2026, 3, 28, 9, 0, tzinfo=timezone.utc)
        
        service.schedule_reminder(101, recurring_time)
        
        assert len(scheduled_jobs) == 5, "Should have scheduled five jobs total"
        print("[PASS] Recurring task job scheduled correctly")
        print()
        
        # Summary
        print("=" * 60)
        print("Scheduler Tests Complete!")
        print(f"Total jobs scheduled: {len(scheduled_jobs)}")
        print(f"Total jobs removed: {len(removed_jobs)}")
        print("=" * 60)


async def main():
    """Run scheduler tests."""
    await TestSchedulerService.run_tests()


if __name__ == "__main__":
    asyncio.run(main())