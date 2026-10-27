import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
import return_grade as rg


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name).resolve()  # macOS: /var is a symlink to /private/var
        (self.job / 'result.json').write_text(json.dumps({'ok': True, 'project': 'P', 'timeline': 'T'}))
        self.timeline = Mock()
        self.timeline.GetName.return_value = 'T'
        self.timeline.GetUniqueId.return_value = 'tid'
        self.project = Mock()
        p = self.project
        p.GetName.return_value = 'P'
        p.GetUniqueId.return_value = 'pid'
        p.GetCurrentTimeline.return_value = self.timeline
        p.IsRenderingInProgress.return_value = False
        p.GetRenderPresetList.return_value = ['Premiere XML']
        p.GetRenderCodecs.return_value = {'Apple ProRes 422 HQ': 'ProRes422HQ'}
        p.AddRenderJob.return_value = 'only-created'
        p.GetRenderJobStatus.return_value = {'JobStatus': 'Complete', 'CompletionPercentage': 100}
        p.StartRendering.side_effect = self.render
        self.resolve = Mock()
        self.manager = self.resolve.GetProjectManager.return_value
        self.manager.GetCurrentProject.return_value = p
        self.events = []

    def render(self, *args, **kwargs):
        out = Path(self.project.SetRenderSettings.call_args.args[0]['TargetDir'])
        self.make_xml(out)
        return True

    def make_xml(self, out, source=None, extra=False, missing=False):
        media = out / 'clip.mov'
        if not missing:
            media.write_bytes(b'synthetic video')
        if extra:
            (out / 'unreferenced.mov').write_bytes(b'synthetic video')
        url = (source or media).as_uri()
        (out / 'Premiere.xml').write_text('<xmeml><sequence><media><video><track>'
            '<clipitem><file id="f1"/></clipitem></track></video></media></sequence>'
            '<file id="f1"><pathurl>' + url + '</pathurl></file></xmeml>')

    def run_job(self, **kwargs):
        return rg.return_job(self.resolve, self.job, 'Premiere XML', emit=self.events.append, **kwargs)

    def test_success_file_id_reference_and_only_job_started(self):
        result = self.run_job()
        self.assertTrue(result['ready_for_import'])
        self.assertEqual(json.loads((self.job / 'return.json').read_text()), result)
        self.project.StartRendering.assert_called_once_with(['only-created'], isInteractiveMode=False)
        self.project.SetCurrentRenderMode.assert_called_once_with(0)
        self.project.SetCurrentRenderFormatAndCodec.assert_called_once_with('mov', 'ProRes422HQ')
        settings = self.project.SetRenderSettings.call_args.args[0]
        self.assertTrue(Path(settings['TargetDir']).is_relative_to(self.job))
        self.assertNotIn('ReplaceExistingFilesInPlace', settings)
        self.assertEqual(self.events[0]['progress'], 100)
        self.timeline.Export.assert_not_called()
        self.project.StopRendering.assert_not_called()

    def test_missing_preset(self):
        self.project.GetRenderPresetList.return_value = []
        with self.assertRaisesRegex(RuntimeError, 'indisponible'):
            self.run_job()
        self.project.LoadRenderPreset.assert_not_called()

    def test_other_render_running(self):
        self.project.IsRenderingInProgress.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'autre rendu'):
            self.run_job()
        self.project.AddRenderJob.assert_not_called()

    def test_identity_mismatch(self):
        self.timeline.GetName.return_value = 'other'
        with self.assertRaisesRegex(RuntimeError, 'correspond'):
            self.run_job()

    def test_identity_changes_during_poll(self):
        def status(_):
            self.timeline.GetUniqueId.return_value = 'changed'
            return {'JobStatus': 'Rendering'}
        self.project.GetRenderJobStatus.side_effect = status
        with self.assertRaisesRegex(RuntimeError, 'Identité'):
            self.run_job(poll_interval=0)
        self.project.StopRendering.assert_not_called()

    def test_timeout_does_not_stop_render(self):
        self.project.GetRenderJobStatus.return_value = {'JobStatus': 'Rendering'}
        with patch.object(rg.time, 'monotonic', side_effect=[0, 1801]):
            with self.assertRaisesRegex(RuntimeError, 'Délai'):
                self.run_job()
        self.project.StopRendering.assert_not_called()

    def test_failed_render(self):
        self.project.GetRenderJobStatus.return_value = {'JobStatus': 'Failed'}
        with self.assertRaisesRegex(RuntimeError, 'échoué'):
            self.run_job()
        self.assertFalse((self.job / 'return.json').exists())

    def test_xml_required_no_fallback(self):
        self.project.StartRendering.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'XML'):
            self.run_job()
        self.timeline.Export.assert_not_called()

    def test_bad_original_result(self):
        (self.job / 'result.json').write_text('{"ok": false}')
        with self.assertRaisesRegex(RuntimeError, 'initial'):
            self.run_job()

    def test_missing_codec(self):
        self.project.GetRenderCodecs.return_value = {'H264': 'h264'}
        with self.assertRaisesRegex(RuntimeError, 'ProRes indisponible'):
            self.run_job()

    def test_xml_rejects_sources_missing_and_unreferenced_media(self):
        for kind in ('source', 'missing', 'extra'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.job) as name:
                out = Path(name)
                source = self.job / 'source.mov'
                source.write_bytes(b'original')
                self.make_xml(out, source=source if kind == 'source' else None,
                              missing=kind == 'missing', extra=kind == 'extra')
                with self.assertRaises(RuntimeError):
                    rg.validate_xml(out)

    def test_concurrent_return_refused(self):
        with (self.job / '.return.lock').open('a') as lock:
            rg.fcntl.flock(lock, rg.fcntl.LOCK_EX | rg.fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'déjà en cours'):
                self.run_job()
        self.project.StartRendering.assert_not_called()

    def test_new_version_uses_different_directory(self):
        one = self.run_job()
        two = self.run_job()
        self.assertNotEqual(one['outputDir'], two['outputDir'])

    def test_french_complete_status(self):
        self.project.GetRenderJobStatus.return_value = {'JobStatus': 'Terminer', 'CompletionPercentage': 100}
        self.assertTrue(self.run_job()['ready_for_import'])


if __name__ == '__main__':
    unittest.main()
