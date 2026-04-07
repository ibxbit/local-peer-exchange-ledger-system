"""Startup integration tests for scheduler boot conditions."""


def test_create_app_starts_scheduler_when_reloader_main(monkeypatch):
    from app import create_app
    import app.scheduler as scheduler

    called = {'count': 0}

    def _fake_start(app):
        called['count'] += 1

    monkeypatch.setattr(scheduler, 'start', _fake_start)
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')

    create_app()
    assert called['count'] == 1


def test_create_app_skips_scheduler_when_reloader_child(monkeypatch):
    from app import create_app
    import app.scheduler as scheduler

    called = {'count': 0}

    def _fake_start(app):
        called['count'] += 1

    monkeypatch.setattr(scheduler, 'start', _fake_start)
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'false')

    create_app()
    assert called['count'] == 0
