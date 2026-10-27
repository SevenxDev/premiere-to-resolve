#!/usr/bin/env python3
"""Render an explicit Premiere preset; never export the timeline as a fallback."""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

from send import connect, write_result


def identity(obj):
    uid = getattr(obj, 'GetUniqueId', None)
    return (obj.GetName(), uid() if callable(uid) else None)


def validate_xml(output):
    output = Path(output).resolve()
    xmls = list(output.rglob('*.xml'))
    if len(xmls) != 1:
        raise RuntimeError('Le preset doit produire exactement un XML Premiere réel.')
    root = ET.parse(xmls[0]).getroot()
    definitions = {}
    for node in root.iter('file'):
        if node.get('id') and node.findtext('pathurl'):
            key = node.get('id')
            if key in definitions and definitions[key] != node.findtext('pathurl'):
                raise RuntimeError('Référence média XML ambiguë.')
            definitions[key] = node.findtext('pathurl')
    paths = set()
    clips = root.findall('.//media/video/track/clipitem')
    if not clips:
        raise RuntimeError('XML sans clips vidéo vérifiables.')
    for clip in clips:
        node = clip.find('file')
        url = (node.findtext('pathurl') or definitions.get(node.get('id'))) if node is not None else None
        if not url:
            raise RuntimeError('Référence vidéo XML non résolue.')
        parsed = urlparse(url)
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            raise RuntimeError('Le XML doit référencer des médias locaux rendus.')
        path = Path(unquote(parsed.path)).resolve()
        if not path.is_relative_to(output) or not path.is_file() or path.suffix.lower() != '.mov':
            raise RuntimeError('Média XML absent, source ou hors du dossier de retour.')
        paths.add(path)
    rendered = {p.resolve() for p in output.rglob('*') if p.is_file() and p.suffix.lower() == '.mov'}
    if not rendered or paths != rendered:
        raise RuntimeError('Tous les médias vidéo rendus doivent être référencés dans le XML.')
    return xmls[0].resolve()


def _return_job(resolve, job, preset, timeout=1800, poll_interval=1, emit=None):
    if not preset or 'premiere' not in preset.lower():
        raise ValueError('Un preset Premiere explicite est obligatoire.')
    if not 0 < timeout <= 1800:
        raise ValueError('Le délai doit être compris entre zéro et 30 minutes.')
    emit = emit or (lambda event: print(json.dumps(event), flush=True))
    job = Path(job).resolve()
    previous = json.loads((job / 'result.json').read_text(encoding='utf-8'))
    if previous.get('ok') is not True or not previous.get('project') or not previous.get('timeline'):
        raise RuntimeError('Import initial non confirmé ; retour refusé.')
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    if not project or not timeline or project.GetName() != previous['project'] or timeline.GetName() != previous['timeline']:
        raise RuntimeError('Le projet ou la timeline active ne correspond pas au transfert.')
    expected = (identity(project), identity(timeline))

    def check():
        current = manager.GetCurrentProject()
        active = current.GetCurrentTimeline() if current else None
        if not current or not active or (identity(current), identity(active)) != expected:
            raise RuntimeError('Identité projet/timeline modifiée ; retour interrompu sans arrêter les rendus.')

    check()
    if project.IsRenderingInProgress():
        raise RuntimeError('Un autre rendu est en cours ; retour refusé.')
    presets = project.GetRenderPresetList() or []
    if isinstance(presets, dict):
        presets = list(presets.keys()) + list(presets.values())
    if preset not in presets:
        raise RuntimeError('Preset Premiere indisponible : ' + preset)
    codecs = project.GetRenderCodecs('mov') or {}
    prores = [(key, label) for key, label in codecs.items() if 'prores' in str(label).lower()]
    if not prores:
        raise RuntimeError('Codec MOV ProRes indisponible.')
    codec = next((label for key, label in prores if '422' in str(label) and 'hq' in str(label).lower()), prores[0][1])
    output = Path(tempfile.mkdtemp(prefix='return-', dir=job))
    write_result(output / 'attempt.json', {'preset': preset, 'state': 'preparing'})
    check()
    if project.IsRenderingInProgress():
        raise RuntimeError('Un autre rendu vient de démarrer ; retour refusé.')
    if not project.LoadRenderPreset(preset):
        raise RuntimeError('Chargement du preset Premiere refusé.')
    if not project.SetCurrentRenderMode(0):
        raise RuntimeError('Mode individual clips refusé.')
    if not project.SetCurrentRenderFormatAndCodec('mov', codec):
        raise RuntimeError('Réglage MOV ProRes refusé.')
    # Resolve 20.2 rejects UniqueFilenameStyle/ReplaceExistingFilesInPlace
    # for the built-in Premiere preset. The exclusive empty directory prevents
    # overwriting existing media; validate the actual XML and renders below.
    settings = {'SelectAllFrames': True, 'TargetDir': str(output), 'ExportVideo': True,
                'ExportAudio': True}
    if not project.SetRenderSettings(settings):
        raise RuntimeError('Réglages de retour refusés.')
    check()
    if project.IsRenderingInProgress():
        raise RuntimeError('Un autre rendu vient de démarrer ; retour refusé.')
    render_id = project.AddRenderJob()
    if not render_id:
        raise RuntimeError('Création du rendu refusée.')
    check()
    if project.IsRenderingInProgress():
        raise RuntimeError('Un autre rendu vient de démarrer ; retour refusé.')
    if not project.StartRendering([render_id], isInteractiveMode=False):
        raise RuntimeError('Démarrage du seul rendu créé refusé.')
    deadline = time.monotonic() + timeout
    while True:
        check()
        status = project.GetRenderJobStatus(render_id) or {}
        state = str(status.get('JobStatus', ''))
        emit({'type': 'progress', 'jobId': render_id, 'status': state,
              'progress': status.get('CompletionPercentage', 0)})
        if state.casefold() in ('complete', 'completed', 'terminer', 'terminé', 'terminée') and status.get('CompletionPercentage') == 100 and not project.IsRenderingInProgress():
            break
        if state.casefold() in ('failed', 'cancelled', 'canceled', 'échec', 'échoué', 'annulé', 'annuler'):
            raise RuntimeError('Le rendu a échoué : ' + state)
        if time.monotonic() >= deadline:
            raise RuntimeError('Délai de 30 minutes atteint ; aucun rendu arrêté.')
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    check()
    xml = validate_xml(output)
    check()
    count = len(ET.parse(xml).getroot().findall('.//media/video/track/clipitem'))
    if previous.get('video_clips') and count != previous['video_clips']:
        raise RuntimeError('Le nombre de plans au retour diffère de l’aller.')
    result = {'videoClips': count, 'ok': True, 'ready_for_import': True, 'xmlPath': str(xml),
              'outputDir': str(output), 'project': previous['project'],
              'timeline': previous['timeline'], 'renderJobId': render_id, 'preset': preset}
    write_result(job / 'return.json', result)
    return result


def return_job(resolve, job, preset, timeout=1800, poll_interval=1, emit=None):
    # Kernel releases lock after crashes. Resolve rendering state is checked too.
    with (Path(job) / '.return.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Un retour est déjà en cours.') from exc
        return _return_job(resolve, job, preset, timeout, poll_interval, emit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True, type=Path)
    parser.add_argument('--preset', default='Premiere XML')
    args = parser.parse_args()
    try:
        result = return_job(connect(), args.job, args.preset)
    except Exception as exc:
        result = {'ok': False, 'ready_for_import': False, 'error': str(exc)}
        if args.job.is_dir():
            write_result(args.job / 'return.json', result)
    print(json.dumps(result), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
