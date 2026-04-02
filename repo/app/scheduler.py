"""
Background scheduler — daily report generation at 2:00 AM local time.

Uses APScheduler's BackgroundScheduler with a CronTrigger.
The scheduler is started once inside create_app() and runs in a daemon thread.

Local timezone is derived from the OS without any third-party dependency:
    datetime.datetime.now().astimezone().tzinfo
This gives a fixed-offset timezone reflecting the current UTC offset.
For DST-aware scheduling, set the TZ environment variable on the host.
"""

import logging
import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start(app) -> None:
    """
    Start the background scheduler attached to `app`.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _scheduler
    if _scheduler is not None:
        return

    # Derive local timezone from the OS (no pytz / zoneinfo required).
    local_tz = datetime.datetime.now().astimezone().tzinfo

    _scheduler = BackgroundScheduler(timezone=local_tz)

    def _run_daily_report():
        """Executed in the scheduler thread at 2:00 AM local time."""
        with app.app_context():
            try:
                from app.models import db
                from app.services.analytics_service import generate_daily_report
                with db() as conn:
                    result = generate_daily_report(conn)   # defaults to yesterday
                logger.info('Daily report generated: %s', result['report_date'])
            except Exception:
                logger.exception('Daily report generation failed.')

    _scheduler.add_job(
        _run_daily_report,
        CronTrigger(hour=2, minute=0),
        id='daily_analytics_report',
        replace_existing=True,
        misfire_grace_time=3600,   # run even if server was down, up to 1 h late
    )

    def _run_auto_match():
        """Run one auto-match governance cycle (expire + match waiting entries)."""
        with app.app_context():
            try:
                from app.models import db
                from app.services.matching_service import run_auto_match_cycle
                with db() as conn:
                    summary = run_auto_match_cycle(conn)
                if summary['attempted'] or summary['expired'] or summary['matched']:
                    logger.debug(
                        'Auto-match cycle: attempted=%d matched=%d expired=%d',
                        summary['attempted'], summary['matched'], summary['expired']
                    )
            except Exception:
                logger.exception('Auto-match cycle failed.')

    _scheduler.add_job(
        _run_auto_match,
        IntervalTrigger(seconds=10),
        id='auto_match_cycle',
        replace_existing=True,
        max_instances=1,           # prevent overlapping runs
        coalesce=True,             # skip missed runs rather than queuing them
    )

    _scheduler.start()
    logger.info(
        'Scheduler started — daily report at 02:00, auto-match every 10 s.'
    )


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
