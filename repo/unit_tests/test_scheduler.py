"""Unit tests for scheduler wiring and trigger callbacks."""

import os


class _FakeScheduler:
    def __init__(self, timezone=None):
        self.timezone = timezone
        self.jobs = []
        self.started = False
        self.stopped = False

    def add_job(self, func, trigger, id, **kwargs):
        self.jobs.append({'id': id, 'func': func, 'trigger': trigger, 'kwargs': kwargs})

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        self.stopped = True


def test_scheduler_registers_jobs_and_runs_callbacks(monkeypatch):
    from app import create_app
    import app.scheduler as scheduler
    import app.services.analytics_service as analytics_service
    import app.services.matching_service as matching_service

    os.environ.setdefault('WERKZEUG_RUN_MAIN', 'false')
    app = create_app()
    app.config['TESTING'] = True

    called = {'daily': 0, 'match': 0}

    def _fake_daily_report(conn):
        called['daily'] += 1
        return {'report_date': '2099-01-01'}

    def _fake_auto_match(conn):
        called['match'] += 1
        return {'attempted': 0, 'matched': 0, 'expired': 0}

    monkeypatch.setattr(scheduler, 'BackgroundScheduler', _FakeScheduler)
    monkeypatch.setattr(analytics_service, 'generate_daily_report', _fake_daily_report)
    monkeypatch.setattr(matching_service, 'run_auto_match_cycle', _fake_auto_match)

    scheduler._scheduler = None
    scheduler.start(app)

    assert scheduler._scheduler is not None
    assert scheduler._scheduler.started is True

    jobs = {j['id']: j for j in scheduler._scheduler.jobs}
    assert 'daily_analytics_report' in jobs
    assert 'auto_match_cycle' in jobs

    jobs['daily_analytics_report']['func']()
    jobs['auto_match_cycle']['func']()
    assert called['daily'] == 1
    assert called['match'] == 1

    scheduler.stop()
    assert scheduler._scheduler is None
