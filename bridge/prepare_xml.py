"""Prepare a trusted Premiere FCP7 export without touching the source.

Only explicitly identified colour effects are removed. Unsupported conform
features fail rather than silently yielding a different timeline.
"""
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit


class PreparationError(ValueError):
    """An actionable, user-facing conform error."""


def _text(node, name):
    return (node.findtext(name) or '').strip()


def _colour_filter(node):
    effect = node.find('effect')
    if effect is None:
        return False
    # Exact known names/IDs, never a loose match on e.g. 'color matte'.
    identifiers = {_text(effect, field).casefold() for field in ('name', 'effectid')}
    known = {
        'lumetri', 'lumetri color', 'couleur lumetri', 'ae.adbe lumetri',
        'adbe lumetri', 'color correction', 'color corrector',
        'color corrector 3-way', 'three-way color corrector',
        'three way color corrector', 'correction colorimétrique',
        'ae.adbe color correction', 'ae.adbe color corrector',
        'lut', 'apply lut', 'lut loader', 'lumetri look', 'ae.adbe apply color lut',
        'adbe apply color lut', 'colorcorrector', 'colorcorrector3way',
    }
    return bool(identifiers & known)


def prepare(source: Path, destination: Path) -> dict:
    """Validate and write a colour-free copy; return a JSON-compatible report.

    Output is created exclusively: neither the source nor an existing output
    can be overwritten. Timeline positions are never changed.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise PreparationError('Choisissez une copie XML différente de l’original.')
    try:
        original = source.read_bytes()
    except OSError as exc:
        raise PreparationError('Impossible de lire l’export XML Premiere.') from exc
    # Also inspect UTF-16/32 exports before handing them to ElementTree.
    declaration_scan = original.replace(b'\x00', b'')
    if re.search(br'<!\s*ENTITY\b', declaration_scan, re.I) or re.search(
        br'<!\s*DOCTYPE\b[^>]*\[', declaration_scan, re.I
    ):
        raise PreparationError('Les déclarations DTD internes ou d’entités sont interdites. Réexportez depuis Premiere.')
    try:
        root = ET.fromstring(original)
    except ET.ParseError as exc:
        raise PreparationError('XML illisible : réexportez la séquence depuis Premiere.') from exc
    sequences = root.findall('sequence')
    if root.tag != 'xmeml' or len(sequences) != 1:
        raise PreparationError('L’export doit contenir une seule séquence FCP7 sous xmeml.')
    sequence = sequences[0]
    if sequence.findall('.//sequence'):
        raise PreparationError('Séquence imbriquée détectée : aplatissez les séquences dans Premiere puis réexportez.')
    if root.findall('.//multiclip') or root.findall('.//multicam') or any(
        _text(n, 'ismulticlip').lower() in ('true', '1') for n in root.iter()
    ):
        raise PreparationError('Multicam détecté : aplatissez le multicam dans Premiere puis réexportez.')
    for effect in root.iter('effect'):
        labels = ' '.join(_text(effect, field).casefold() for field in ('name', 'effectid'))
        if any(term in labels for term in ('timeremap', 'time remap', 'time-remap', 'time remapping', 'speed', 'retiming', 'remappage temporel', 'vitesse')):
            raise PreparationError('Variation de vitesse détectée : rendez ces plans en médias intermédiaires puis réexportez.')
    if root.findall('.//timeremap') or root.findall('.//timeRemap'):
        raise PreparationError('Variation de vitesse détectée : rendez ces plans en médias intermédiaires puis réexportez.')

    clips = sequence.findall('./media/video/track/clipitem')
    if not clips:
        raise PreparationError('Aucun plan vidéo : exportez une séquence contenant des vidéos.')
    files = {}
    for item in root.iter('file'):
        identifier = item.get('id')
        path = _text(item, 'pathurl')
        if identifier and path:
            if identifier in files and files[identifier] != path:
                raise PreparationError('Références média contradictoires : réexportez depuis Premiere.')
            files[identifier] = path
    media_paths = []
    # Validate audio as well as video, while leaving audio untouched.
    for clip in sequence.findall('.//clipitem'):
        file_node = clip.find('file')
        if file_node is None:
            raise PreparationError('Plan sans média local : aplatissez les séquences et réexportez.')
        url = _text(file_node, 'pathurl') or files.get(file_node.get('id'), '')
        if not url:
            raise PreparationError('Référence média introuvable : réexportez avec les médias reliés.')
        parsed = urlsplit(url)
        if parsed.scheme != 'file' or parsed.netloc.lower() not in ('', 'localhost'):
            raise PreparationError('Média non local : reliez le plan à un fichier local puis réexportez.')
        path = Path(unquote(parsed.path))
        if not path.is_absolute() or not path.is_file():
            raise PreparationError('Média local manquant : ' + str(path) + '. Reliez-le dans Premiere puis réexportez.')
        if str(path) not in media_paths:
            media_paths.append(str(path))

    report = {'video_clips': len(clips), 'removed_color_filters': [],
              'removed_generators': [], 'media_paths': media_paths, 'warnings': []}
    video = sequence.find('./media/video')
    for parent in video.iter():
        for child in list(parent):
            if child.tag == 'filter' and _colour_filter(child):
                effect = child.find('effect')
                report['removed_color_filters'].append({
                    'clip': _text(parent, 'name') or parent.get('id', ''),
                    'name': _text(effect, 'name') or _text(effect, 'effectid')})
                parent.remove(child)
            elif child.tag == 'generatoritem':
                report['removed_generators'].append({
                    'name': _text(child, 'name'), 'start': _text(child, 'start'),
                    'end': _text(child, 'end')})
                parent.remove(child)
    if report['removed_generators']:
        report['warnings'].append('Titres et générateurs vidéo retirés ; leurs intervalles restent inchangés.')
    report['warnings'].append('Les effets non colorimétriques restent dans le XML ; leur compatibilité dans Resolve doit être contrôlée.')
    payload = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    try:
        with destination.open('xb') as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise PreparationError('La copie XML existe déjà. Choisissez un nouveau nom.') from exc
    except OSError as exc:
        raise PreparationError('Impossible d’écrire la copie XML. Vérifiez le dossier de destination.') from exc
    return report
