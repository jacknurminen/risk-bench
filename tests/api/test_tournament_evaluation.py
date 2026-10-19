import json
import threading

import pytest
from fastapi.testclient import TestClient

from risk_bench import launch
from risk_bench.api import app as api_app
from risk_bench.api.processes import GameProcessManager
from risk_bench.config import Settings
from risk_bench.runner.evaluation import EvaluatedGame
from risk_bench.runner.evaluation_versions import list_versions
from risk_bench.runner.tournament_evaluation import create_batch, get_batch, list_batches, run_batch
from risk_bench.viewer.tournament import build_tournament_view_model
from tests.api.test_tournament_queue import FakeContext


class Coder:
    spec = 'test:coder:medium'

    def evaluate_game(self, players, spans, game_overview):
        return EvaluatedGame(self.spec, ({'type': 'eval_member_status', 'status': 'complete', 'memos': []},))


@pytest.fixture
def tournament(tmp_path, monkeypatch):
    monkeypatch.setattr('risk_bench.specs.build_evaluators', lambda **kwargs: (Coder(),))
    directory = tmp_path / 'tournament'
    ids = ['t-g1', 't-g2', 't-g3']
    manifest = {'id': directory.name, 'game_ids': ids, 'players': ['random'] * 3, 'parallel_games': 2,
                'created_at': '2026-10-11T00:00:00Z'}
    for game_id in ids:
        journal = directory / game_id / 'game.jsonl'
        launch.run_new_game(['random'] * 3, 19, Settings(), [], journal, 1, False, [])
        launch.run_evaluation(journal, [Coder.spec], Settings())
    (directory / 'tournament.json').write_text(json.dumps(manifest))
    return directory, manifest


def test_batch_limits_concurrency_preserves_versions_and_continues_after_failure(tournament, monkeypatch):
    directory, manifest = tournament
    path = create_batch(directory, manifest, [Coder.spec], False)
    baseline = next(b for b in list_batches(directory) if b['method'] == 'snapshot')
    originals = {game: job['versions'][0] for game, job in baseline['games'].items()}
    first_two = threading.Barrier(2)
    mutex = threading.Lock()
    active = peak = 0
    original = launch.run_evaluation

    def evaluate(journal, panel, settings, **kwargs):
        nonlocal active, peak
        with mutex:
            active += 1
            peak = max(peak, active)
        try:
            if journal.parent.name != 't-g3':
                first_two.wait(timeout=5)
            if journal.parent.name == 't-g2':
                raise RuntimeError('test provider failure')
            return original(journal, panel, settings, **kwargs)
        finally:
            with mutex:
                active -= 1

    monkeypatch.setattr(launch, 'run_evaluation', evaluate)
    run_batch(path, Settings())
    assert peak == 2
    batch = get_batch(directory, path.stem)
    assert [j['status'] for j in batch['games'].values()] == ['complete', 'failed', 'complete']
    selected = build_tournament_view_model(directory, path.stem)
    assert 'test provider failure' in selected['research']['availability'][1]['status']
    assert selected['research']['availability'][1]['version'] is None  # never substitute the previous run
    assert selected['research']['availability'][0]['version'] == path.stem
    for game_id, old_id in originals.items():
        assert old_id in [version.run_id for version in list_versions(directory / game_id / 'game.jsonl')]
    old = build_tournament_view_model(directory, baseline['id'])
    assert [row['version'] for row in old['research']['availability']] == list(originals.values())
    # An individual rerun must not change the collection pinned by the tournament selector.
    original(directory / 't-g1' / 'game.jsonl', [Coder.spec], Settings())
    assert build_tournament_view_model(directory, path.stem)['research']['availability'][0]['version'] == path.stem


def test_round_replay_batch_assigns_its_version_and_always_starts_fresh(tournament, monkeypatch):
    from tests.runner.test_live_evaluation import DeterministicEvaluator

    directory, manifest = tournament
    monkeypatch.setattr('risk_bench.specs.build_evaluators', lambda **kwargs: (DeterministicEvaluator(),))
    path = create_batch(directory, manifest, [DeterministicEvaluator.spec], True)
    run_batch(path, Settings())
    batch = get_batch(directory, path.stem)
    assert all(job['status'] == 'complete' for job in batch['games'].values())
    view = build_tournament_view_model(directory, path.stem)
    assert all(row['version'] == path.stem and row['method'] == 'live_rounds'
               for row in view['research']['availability'])


def test_api_bulk_launch_reserves_games_and_interrupted_batches_survive_restart(tournament, monkeypatch):
    directory, _ = tournament
    manager = GameProcessManager()
    manager._context = FakeContext()
    monkeypatch.setattr(api_app, 'GameProcessManager', lambda: manager)
    with TestClient(api_app.create_app(directory.parent)) as client:
        response = client.post('/api/tournaments/tournament/evaluate', json={'evaluators': [Coder.spec]})
        assert response.status_code == 202, response.text
        run_id = response.json()['version']
        assert response.json()['games'] == 3
        rejected = client.post('/api/tournaments/tournament/evaluate', json={'evaluators': [Coder.spec]})
        assert rejected.status_code == 409
        assert client.post('/api/games/t-g3/evaluate', json={'evaluators': [Coder.spec]}).status_code == 409
        assert client.delete('/api/games/t-g3').status_code == 409
        view = client.get('/api/tournaments/tournament', params={'version': run_id}).json()
        assert view['evaluation_version'] == run_id
        assert all(row['status'] == 'Queued' for row in view['research']['availability'])
        assert client.get('/tournaments/tournament', params={'version': run_id}).status_code == 200
    batch = get_batch(directory, run_id)
    assert all(job['status'] == 'interrupted' for job in batch['games'].values())
    # Simulate abrupt server loss with pending records but no in-memory worker.
    path = create_batch(directory, json.loads((directory / 'tournament.json').read_text()), [Coder.spec], False)
    replacement = GameProcessManager()
    replacement.reconcile_tournament_evaluations(directory)
    assert all(job['status'] == 'interrupted' for job in get_batch(directory, path.stem)['games'].values())
    replacement.shutdown()


def test_bulk_preflight_leaves_every_game_untouched_on_busy_or_missing_game(tournament, monkeypatch):
    directory, _ = tournament
    manager = GameProcessManager()
    manager._context = FakeContext()
    monkeypatch.setattr(api_app, 'GameProcessManager', lambda: manager)
    original = (directory / 't-g1' / 'game.eval.jsonl').read_bytes()
    (directory / 't-g3' / 'game.jsonl').unlink()
    with TestClient(api_app.create_app(directory.parent)) as client:
        rejected = client.post('/api/tournaments/tournament/evaluate', json={'evaluators': [Coder.spec]})
        assert rejected.status_code == 409
        assert client.post('/api/tournaments/tournament/evaluate', json={'evaluators': []}).status_code == 400
        assert not list_batches(directory)
        assert (directory / 't-g1' / 'game.eval.jsonl').read_bytes() == original


def test_unknown_version_never_falls_back_to_latest(tournament):
    directory, _ = tournament
    with pytest.raises(ValueError, match='No such'):
        build_tournament_view_model(directory, '20261011-010101-123456')
    with pytest.raises(ValueError, match='No such'):
        build_tournament_view_model(directory, '../tournament')


def test_interrupted_whole_game_run_retains_version_and_trace_for_later_archival(tournament, monkeypatch):
    directory, _ = tournament
    journal = directory / 't-g1' / 'game.jsonl'
    run_id = '20261011-010101-123456'

    def interrupted(**kwargs):
        raise RuntimeError('worker interrupted')

    with monkeypatch.context() as patch:
        patch.setattr(launch, '_gather_evaluations', interrupted)
        with pytest.raises(RuntimeError, match='worker interrupted'):
            launch.run_evaluation(journal, [Coder.spec], Settings(), run_id=run_id)
    failed = next(version for version in list_versions(journal) if version.run_id == run_id)
    assert failed.trace_path.is_file()
    launch.run_evaluation(journal, [Coder.spec], Settings())
    archived = next(version for version in list_versions(journal) if version.run_id == run_id)
    assert not archived.is_current
    assert archived.trace_path.is_file()
