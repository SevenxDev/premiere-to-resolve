"""SYNTHETIC tests only: mocked Resolve SDK, no app launched or real import.

The XML preparer is mocked here; its real validation has separate tests.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BRIDGE = Path(__file__).resolve().parents[1] / 'bridge'
sys.path.insert(0, str(BRIDGE))
spec = importlib.util.spec_from_file_location('resolve_send', BRIDGE / 'send.py')
send = importlib.util.module_from_spec(spec)
spec.loader.exec_module(send)


class SendSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name)
        (self.job / 'source.json').write_text(json.dumps({'name': 'Synthetic'}))
        self.name = 'Synthetic - COLOR - ' + self.job.name
        self.timeline = Mock()
        self.timeline.GetTrackCount.return_value = 0
        self.timeline.GetName.return_value = 'Synthetic timeline'
        self.pool = Mock()
        self.pool.ImportTimelineFromFile.return_value = self.timeline
        self.project = Mock()
        self.project.GetName.return_value = self.name
        self.project.GetUniqueId.return_value = 'synthetic-created-id'
        self.project.GetMediaPool.return_value = self.pool
        self.project.SetCurrentTimeline.return_value = True
        self.manager = Mock()
        self.manager.GetCurrentProject.side_effect = lambda: (
            self.project if self.manager.CreateProject.called else None)
        self.manager.CreateProject.return_value = self.project
        self.manager.SaveProject.return_value = True
        self.resolve = Mock()
        self.resolve.GetProjectManager.return_value = self.manager
        self.resolve.GetCurrentPage.return_value = 'color'
        self.resolve.OpenPage.return_value = True
        preparer = patch('prepare_xml.prepare', return_value={'video_clips': 0, 'warnings': []})
        self.prepare = preparer.start()
        self.addCleanup(preparer.stop)

    def run_job(self):
        return send.import_job(self.resolve, self.job)

    def test_success_and_completed_replay_rejected(self):
        result = self.run_job()
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads((self.job / 'result.json').read_text()), result)
        with self.assertRaises(ValueError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_called_once()
        self.assertFalse(list(self.job.glob('.result-*')))

    def test_wrong_active_project_never_imported(self):
        wrong = Mock()
        wrong.GetName.return_value = 'Existing work'
        self.manager.GetCurrentProject.side_effect = [None, wrong]
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_not_called()
        wrong.GetMediaPool.assert_not_called()
        self.manager.SaveProject.assert_not_called()

    def test_same_name_different_identity_rejected(self):
        wrong = Mock()
        wrong.GetName.return_value = self.name
        wrong.GetUniqueId.return_value = 'other-id'
        self.manager.GetCurrentProject.side_effect = [None, wrong]
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_not_called()

    def test_missing_active_project_no_fallback(self):
        self.manager.GetCurrentProject.side_effect = None
        self.manager.GetCurrentProject.return_value = None
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_not_called()

    def test_readiness_timeout_does_not_import(self):
        self.resolve.GetCurrentPage.return_value = ''
        with patch.object(send.time, 'monotonic', side_effect=[0, 31]):
            with self.assertRaises(RuntimeError):
                self.run_job()
        self.pool.ImportTimelineFromFile.assert_not_called()

    def test_switch_during_readiness_rejected(self):
        self.resolve.GetCurrentPage.return_value = ''
        wrong = Mock()
        wrong.GetName.return_value = 'Other'
        self.manager.GetCurrentProject.side_effect = [None, wrong]
        with patch.object(send.time, 'monotonic', side_effect=[0, 1]):
            with self.assertRaises(RuntimeError):
                self.run_job()
        self.pool.ImportTimelineFromFile.assert_not_called()

    def test_false_import_not_retried_in_call_or_later(self):
        self.pool.ImportTimelineFromFile.return_value = None
        with self.assertRaises(RuntimeError):
            self.run_job()
        with self.assertRaises(ValueError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_called_once()
        self.manager.CreateProject.assert_called_once()

    def test_ambiguous_exception_not_retried(self):
        self.pool.ImportTimelineFromFile.side_effect = RuntimeError('SDK disconnected')
        with self.assertRaises(RuntimeError):
            self.run_job()
        with self.assertRaises(ValueError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_called_once()

    def test_switch_after_import_prevents_save(self):
        wrong = Mock()
        wrong.GetName.return_value = 'Other'
        self.manager.GetCurrentProject.side_effect = [None, self.project, wrong]
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_called_once()
        self.project.SetCurrentTimeline.assert_not_called()
        self.manager.SaveProject.assert_not_called()

    def test_failed_save_still_blocks_replay(self):
        self.manager.SaveProject.return_value = False
        with self.assertRaises(RuntimeError):
            self.run_job()
        with self.assertRaises(ValueError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_called_once()

    def test_claim_blocks_concurrent_attempt_before_prepare(self):
        (self.job / 'import-started.json').write_text('claimed')
        with self.assertRaises(ValueError):
            self.run_job()
        self.prepare.assert_not_called()
        self.manager.CreateProject.assert_not_called()

    def test_creation_failure_never_imports(self):
        self.manager.CreateProject.return_value = None
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.pool.ImportTimelineFromFile.assert_not_called()

    def test_readiness_eventually_succeeds_with_single_import(self):
        self.resolve.GetCurrentPage.side_effect = ['', 'color', 'color', 'color']
        with patch.object(send.time, 'sleep') as sleep:
            result = self.run_job()
        self.assertTrue(result['ok'])
        sleep.assert_called_once_with(0.5)
        self.pool.ImportTimelineFromFile.assert_called_once()

    def test_switch_after_selection_prevents_save(self):
        wrong = Mock()
        wrong.GetName.return_value = 'Other'
        self.manager.GetCurrentProject.side_effect = [None, self.project, self.project, wrong]
        with self.assertRaises(RuntimeError):
            self.run_job()
        self.project.SetCurrentTimeline.assert_called_once_with(self.timeline)
        self.manager.SaveProject.assert_not_called()

    def test_atomic_publish_failure_keeps_old_result(self):
        target = self.job / 'result.json'
        target.write_text('{"old": true}')
        with patch.object(send.os, 'replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                send.write_result(target, {'new': True})
        self.assertEqual(json.loads(target.read_text()), {'old': True})
        self.assertFalse(list(self.job.glob('.result-*')))


if __name__ == '__main__':
    unittest.main()
