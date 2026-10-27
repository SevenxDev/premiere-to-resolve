"""Synthetic fixtures only: no real Premiere project is read or modified."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

MODULE = Path(__file__).resolve().parents[1] / 'bridge' / 'prepare_xml.py'
spec = importlib.util.spec_from_file_location('prepare_xml', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prepare, PreparationError = module.prepare, module.PreparationError


class SyntheticXMLTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.media = self.folder / 'été plan 中文.mov'
        self.media.write_bytes(b'SYNTHETIC MEDIA PLACEHOLDER; NOT A REAL VIDEO')
        self.source = self.folder / 'source.xml'
        self.dest = self.folder / 'copy.xml'

    def fixture(self, effects='', extra='', url=None, audio=True):
        url = url or self.media.as_uri().replace('file:///', 'file://localhost/')
        # Deliberately forward-reference the file definition from video.
        audio_xml = f'<audio><track><clipitem id="audio"><file id="f"><pathurl>{url}</pathurl></file></clipitem></track></audio>' if audio else ''
        definition = '' if audio else f'<file id="f"><pathurl>{url}</pathurl></file>'
        xml = f'''<?xml version="1.0"?>
<!DOCTYPE xmeml>
<xmeml version="4"><sequence><name>SYNTHETIC FIXTURE</name>
<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
<timecode><string>01:00:00:00</string></timecode><media>
<video><track><clipitem id="v"><name>Video</name><start>10</start><end>50</end><in>5</in><out>45</out><file id="f"/>{effects}</clipitem>{extra}</track></video>
{audio_xml}</media>{definition}</sequence></xmeml>'''
        self.source.write_text(xml, encoding='utf-8')
        return xml.encode('utf-8')

    def effect(self, name):
        return f'<filter><effect><name>{name}</name><effectid>{name}</effectid></effect></filter>'

    def test_colour_removed_others_preserved_and_original_bytes_intact(self):
        effects = ''.join(self.effect(x) for x in ['Lumetri Color', 'Color Corrector 3-way', 'Apply LUT', 'Motion', 'Opacity', 'Gaussian Blur', 'Color Matte'])
        original = self.fixture(effects, '<generatoritem><name>Title</name><start>0</start><end>10</end></generatoritem>')
        before = ET.fromstring(original)
        report = prepare(self.source, self.dest)
        after = ET.parse(self.dest).getroot()
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(report['video_clips'], 1)
        self.assertEqual(len(report['removed_color_filters']), 3)
        self.assertEqual(len(report['removed_generators']), 1)
        self.assertEqual(report['media_paths'], [str(self.media)])
        self.assertEqual([e.findtext('name') for e in after.findall('.//effect')], ['Motion', 'Opacity', 'Gaussian Blur', 'Color Matte'])
        for xpath in ['./sequence/media/audio', './sequence/rate', './sequence/timecode']:
            self.assertEqual(ET.tostring(before.find(xpath)), ET.tostring(after.find(xpath)))
        clip = after.find('.//video/track/clipitem')
        self.assertEqual([clip.findtext(x) for x in ['start', 'end', 'in', 'out']], ['10', '50', '5', '45'])
        self.assertFalse(after.findall('.//generatoritem'))

    def test_local_unicode_path_and_forward_reference(self):
        self.fixture()
        self.assertEqual(prepare(self.source, self.dest)['media_paths'], [str(self.media)])

    def test_missing_media(self):
        self.fixture(url=(self.folder / 'absent.mov').as_uri())
        with self.assertRaisesRegex(PreparationError, 'manquant'):
            prepare(self.source, self.dest)
        self.assertFalse(self.dest.exists())

    def test_unresolved_file_reference(self):
        self.fixture()
        text = self.source.read_text().replace('<file id="f"/>', '<file id="unknown"/>')
        self.source.write_text(text)
        with self.assertRaisesRegex(PreparationError, 'Référence'):
            prepare(self.source, self.dest)

    def test_nested_multicam_and_time_remap_rejected(self):
        for extra, effects in [('<sequence/>', ''), ('<multiclip/>', ''), ('', self.effect('Time Remap')), ('', self.effect('timeremap')), ('', self.effect('Speed'))]:
            with self.subTest(extra=extra, effects=effects):
                self.fixture(effects, extra)
                with self.assertRaises(PreparationError):
                    prepare(self.source, self.dest)
                self.assertFalse(self.dest.exists())

    def test_source_and_existing_output_not_overwritten(self):
        original = self.fixture()
        with self.assertRaises(PreparationError):
            prepare(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.dest.write_bytes(b'EXISTING SYNTHETIC OUTPUT')
        with self.assertRaises(PreparationError):
            prepare(self.source, self.dest)
        self.assertEqual(self.dest.read_bytes(), b'EXISTING SYNTHETIC OUTPUT')

    def test_source_symlink_not_overwritten(self):
        self.fixture()
        self.dest.symlink_to(self.source)
        with self.assertRaises(PreparationError):
            prepare(self.source, self.dest)

    def test_entity_declarations_rejected_even_utf16(self):
        xml = self.fixture().decode().replace('<!DOCTYPE xmeml>', '<!DOCTYPE xmeml [<!ENTITY injected "bad">]>')
        for encoding in ['utf-8', 'utf-16']:
            with self.subTest(encoding=encoding):
                self.source.write_bytes(xml.encode(encoding))
                with self.assertRaisesRegex(PreparationError, 'entités'):
                    prepare(self.source, self.dest)

    def test_wrong_root_missing_sequence_and_no_video(self):
        for xml in ['<wrong><sequence/></wrong>', '<xmeml/>', '<xmeml><sequence><media><video/></media></sequence></xmeml>']:
            with self.subTest(xml=xml):
                self.source.write_text(xml)
                with self.assertRaises(PreparationError):
                    prepare(self.source, self.dest)

    def test_remote_media_rejected(self):
        self.fixture(url='file://remotehost/share/file.mov')
        with self.assertRaisesRegex(PreparationError, 'non local'):
            prepare(self.source, self.dest)

    def test_video_only_full_file_definition(self):
        self.fixture(audio=False)
        self.assertEqual(prepare(self.source, self.dest)['video_clips'], 1)


if __name__ == '__main__':
    unittest.main()
