#!/usr/bin/env python3
"""Local Resolve bridge. Never alters the Premiere project or an existing Resolve project."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile


def write_result(path, result):
    """Publish complete JSON atomically on the same filesystem."""
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                     dir=path.parent, prefix='.result-',
                                     delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def require_target(manager, project, name):
    """Never replace the created project handle with an arbitrary active one."""
    current = manager.GetCurrentProject()
    if not current or current.GetName() != name or project.GetName() != name:
        raise RuntimeError('Le projet DaVinci actif a changé. Transfert arrêté pour protéger les autres projets.')
    # Some older SDKs do not expose GetUniqueId. Names are unique in the
    # current project folder; use the stronger identity check when available.
    expected_id = getattr(project, 'GetUniqueId', None)
    current_id = getattr(current, 'GetUniqueId', None)
    if callable(expected_id) and callable(current_id):
        expected, actual = expected_id(), current_id()
        if not expected or not actual or expected != actual:
            raise RuntimeError('Identité du projet DaVinci non confirmée. Transfert arrêté.')

SDK = Path('/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules')


def connect(wait=60):
    if sys.platform != 'darwin':
        raise RuntimeError('Cette V0 fonctionne sur macOS uniquement.')
    if not SDK.is_dir():
        raise RuntimeError('Le module de pilotage DaVinci est absent. Installe DaVinci Resolve Studio.')
    sys.path.insert(0, str(SDK))
    try:
        import DaVinciResolveScript as api
    except (ImportError, OSError) as exc:
        raise RuntimeError('Module DaVinci incompatible avec ce Python : ' + str(exc)) from exc
    subprocess.run(['open', '-a', 'DaVinci Resolve'], check=True, capture_output=True)
    deadline = time.monotonic() + wait
    while True:
        resolve = api.scriptapp('Resolve')
        if resolve:
            return resolve
        if time.monotonic() >= deadline:
            raise RuntimeError('DaVinci ne permet pas le pilotage local. Dans Resolve Studio : Préférences > Système > Général > External scripting using : Local. La version gratuite ne permet pas ce transfert automatique. Aucun projet modifié.')
        time.sleep(1)


def import_job(resolve, job):
    from prepare_xml import prepare
    job = Path(job).resolve()
    if not job.is_dir():
        raise ValueError('Dossier de transfert absent.')
    result_path = job / 'result.json'
    if result_path.exists():
        raise ValueError('Transfert déjà traité. Relance depuis le bouton pour créer une nouvelle copie.')
    # Durable, exclusive claim: a crash or ambiguous SDK response must never
    # cause another import of this job. Keep this marker on failure too.
    try:
        with (job / 'import-started.json').open('x', encoding='utf-8') as handle:
            json.dump({'state': 'claimed'}, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ValueError('Transfert déjà commencé. Vérifie le projet couleur ; aucun nouvel import lancé.') from exc
    info = json.loads((job / 'source.json').read_text(encoding='utf-8'))
    output = job / 'color.xml'
    report = prepare(job / 'original.xml', output)
    (job / 'conform.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    manager = resolve.GetProjectManager()
    current = manager.GetCurrentProject()
    if current:
        untitled = str(current.GetName() or '').lower().startswith(('untitled', 'projet sans titre'))
        has_work = current.GetTimelineCount() > 0 or bool(current.GetMediaPool().GetRootFolder().GetClipList())
        if untitled and has_work:
            raise RuntimeError('Le projet DaVinci sans titre contient du travail. Enregistre-le sous un nom, puis relance le bouton.')
        if not untitled and not manager.SaveProject():
            raise RuntimeError('Impossible de sauvegarder le projet DaVinci ouvert. Import arrêté pour le protéger.')
    name = str(info.get('name', 'Séquence'))[:100] + ' - COLOR - ' + job.name
    project = manager.CreateProject(name)
    if not project:
        raise RuntimeError('DaVinci ne peut pas créer le projet couleur. Aucun projet existant écrasé.')
    deadline = time.monotonic() + 30
    while not resolve.GetCurrentPage() and time.monotonic() < deadline:
        require_target(manager, project, name)
        time.sleep(0.5)
    if not resolve.GetCurrentPage():
        raise RuntimeError('DaVinci n’est pas prêt. Aucun import lancé.')
    require_target(manager, project, name)
    # A false/exception response may still have imported a timeline. Do not
    # retry this non-idempotent operation, even after restarting the bridge.
    timeline = project.GetMediaPool().ImportTimelineFromFile(str(output), {
        'timelineName': name,
        'importSourceClips': True,
    })
    if not timeline:
        raise RuntimeError('DaVinci n’a pas confirmé l’import XML. Vérifie le projet couleur avant de recommencer.')
    require_target(manager, project, name)
    if not project.SetCurrentTimeline(timeline):
        raise RuntimeError('Timeline importée, mais DaVinci ne peut pas la sélectionner.')
    require_target(manager, project, name)
    if not manager.SaveProject():
        raise RuntimeError('Timeline importée, mais sa sauvegarde a échoué.')
    require_target(manager, project, name)
    if not resolve.OpenPage('color'):
        raise RuntimeError('Timeline importée et sauvegardée. Ouvre la page Couleur dans DaVinci.')
    actual = sum(len(timeline.GetItemListInTrack('video', track) or [])
                 for track in range(1, timeline.GetTrackCount('video') + 1))
    warnings = list(report.get('warnings', []))
    if actual != report['video_clips']:
        warnings.append('Attention : nombre de plans différent après import. Vérifie le raccord avec Premiere.')
    missing = []
    for track in range(1, timeline.GetTrackCount('video') + 1):
        for item in timeline.GetItemListInTrack('video', track) or []:
            media = item.GetMediaPoolItem()
            file_path = media.GetClipProperty('File Path') if media else None
            if not file_path or not Path(file_path).is_file():
                missing.append(item.GetName())
    if missing:
        warnings.append('Médias non vérifiés dans DaVinci : ' + ', '.join(missing[:10]))
    ok = actual == report['video_clips'] and not missing
    result = {'ok': ok, 'project': project.GetName(), 'timeline': timeline.GetName(),
              'page': resolve.GetCurrentPage(), 'video_clips': actual, 'warnings': warnings,
              'message': str(actual) + ' plans importés. La page Couleur est ouverte.', 'report': report}
    if not ok:
        result['error'] = 'Import effectué mais contrôle de conformité incomplet. ' + ' '.join(warnings)
    write_result(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--job', type=Path)
    args = parser.parse_args()
    try:
        resolve = connect()
        if args.check:
            result = {'ok': True, 'product': resolve.GetProductName(), 'version': resolve.GetVersionString()}
        elif args.job:
            result = import_job(resolve, args.job)
        else:
            raise ValueError('Choisis --check ou --job.')
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    sys.exit(main())
