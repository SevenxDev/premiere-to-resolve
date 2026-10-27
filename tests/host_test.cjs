'use strict';
/* Tests de host.jsx avec un faux Premiere : aucun accès réel à l'application. */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'host.jsx'), 'utf8');
const PROJET = '/Users/example/Movies/PROJET.prproj';
const DOSSIER = '/Users/example/Movies/Vers DaVinci/COLOR-1/return-abc';
const XML = DOSSIER + '/retour.xml';
const MEDIA = [DOSSIER + '/V1-0001.mov', DOSSIER + '/V1-0002.mov'];

function sequence(nom, id, medias) {
    const clips = medias.map(fichier => ({ projectItem: { getMediaPath: () => fichier } }));
    return { name: nom, sequenceID: id, videoTracks: Object.assign({ numTracks: 1, 0: { clips: Object.assign({ numItems: clips.length }, clips) } }) };
}

function premieren(options = {}) {
    const cadre = {
        fichiers: new Set(options.fichiers === undefined ? [XML].concat(MEDIA) : options.fichiers),
        taille: options.taille === undefined ? 1234 : options.taille,
        noms: options.noms || [],
        appels: [],
        ouverts: []
    };
    const racine = {
        children: Object.assign({ numItems: cadre.noms.length }, cadre.noms.map(n => ({ name: n }))),
        createBin(nom) { cadre.bin = nom; return { name: nom }; }
    };
    const projet = {
        path: options.projetPath === undefined ? PROJET : options.projetPath,
        activeSequence: options.sequence === undefined ? sequence('SÉQ', 'seq-0', MEDIA) : options.sequence,
        sequences: Object.assign({ numSequences: 1, 0: sequence('SÉQ', 'seq-0', MEDIA) }),
        rootItem: racine,
        importFiles(fichiers, sansUI, chutier, images) {
            cadre.appels.push({ fichiers, sansUI, chutier, images });
            if (options.import === false) return false;
            const nouvelles = options.nouvelles === undefined
                ? [sequence('SÉQ (Resolve)', 'seq-1', options.medias || MEDIA.map(m => '///' + m.slice(1)))]
                : options.nouvelles;
            nouvelles.forEach(s => { projet.sequences[projet.sequences.numSequences++] = s; });
            return true;
        },
        openSequence(id) { cadre.ouverts.push(id); return true; }
    };
    if (projet.activeSequence) projet.activeSequence.exportAsFinalCutProXML = chemin => {
        if (options.export === false) return false;
        cadre.fichiers.add(chemin);
        return true;
    };
    const app = { project: projet };
    const File = function File(chemin) {
        this.path = chemin;
        this.fsName = chemin;
        this.exists = cadre.fichiers.has(String(chemin).replace(/^\/+/, '/'));
        this.length = this.exists ? cadre.taille : 0;
        this.parent = { fsName: String(chemin).replace(/\/[^/]*$/, '') };
    };
    const contexte = vm.createContext({ app, File, Error, String, JSON, Number, Object, Array, unescape, escape });
    vm.runInContext(SOURCE, contexte, { filename: 'host.jsx' });
    return {
        appel(expression) { return JSON.parse(vm.runInContext(expression, contexte)); },
        exporter(chemin) { return this.appel('resolveButtonExport(' + JSON.stringify(chemin) + ')'); },
        importer(xml, projet, chutier, attendus) {
            return this.appel('resolveButtonImport(' + [xml, projet, chutier, attendus].map(JSON.stringify).join(',') + ')');
        },
        normaliser(valeur) { return vm.runInContext('resolveButtonPosix(' + JSON.stringify(valeur) + ')', contexte); },
        cadre, projet, dossier: DOSSIER
    };
}

test('les chemins Premiere sont ramenés à une forme stable', () => {
    const p = premieren();
    assert.strictEqual(p.normaliser('///Users/example/Movies/A.mov'), '/Users/example/Movies/A.mov');
    assert.strictEqual(p.normaliser('/Volumes/Users/example/Movies/A.mov'), '/Users/example/Movies/A.mov');
    assert.strictEqual(p.normaliser('Macintosh HD:Users:example:Movies:A.mov'), '/Users/example/Movies/A.mov');
    assert.strictEqual(p.normaliser('/Users/example/Movies/A.mov'), '/Users/example/Movies/A.mov');
});

test('l’export refuse une absence de séquence', () => {
    const p = premieren({ sequence: null });
    const r = p.exporter('/tmp/a.xml');
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /Ouvre une séquence/);
});

test('l’export refuse un projet non enregistré', () => {
    const p = premieren({ projetPath: '' });
    const r = p.exporter('/tmp/a.xml');
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /Enregistre le projet/);
});

test('l’export refuse d’écraser une destination existante', () => {
    const p = premieren({ fichiers: [XML, '/tmp/a.xml'] });
    const r = p.exporter('/tmp/a.xml');
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /existe déjà/);
});

test('l’export renvoie la provenance du projet', () => {
    const p = premieren({ fichiers: [] });
    const r = p.exporter(XML);
    assert.strictEqual(r.ok, true);
    assert.strictEqual(r.projectPath, PROJET);
    assert.strictEqual(r.sequenceId, 'seq-0');
});

test('l’export refuse un XML vide', () => {
    const p = premieren({ fichiers: [], taille: 0 });
    const r = p.exporter(XML);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /n’a pas produit|n'a pas produit/);
});

test('l’import refuse un autre projet Premiere sans rien créer', () => {
    const p = premieren();
    const r = p.importer(XML, '/Users/example/Movies/AUTRE.prproj', 'COULEUR', 2);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /Ouvre le projet Premiere/);
    assert.strictEqual(p.cadre.bin, undefined);
    assert.strictEqual(p.cadre.appels.length, 0);
});

test('l’import refuse un XML absent', () => {
    const p = premieren();
    const r = p.importer('/tmp/absent.xml', PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /XML des rendus absent/);
    assert.strictEqual(p.cadre.appels.length, 0);
});

test('l’import refuse un chutier déjà présent', () => {
    const p = premieren({ noms: ['COULEUR'] });
    const r = p.importer(XML, PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /déjà commencé/);
    assert.strictEqual(p.cadre.appels.length, 0);
});

test('l’import vérifie les plans, renomme et ouvre la séquence', () => {
    const p = premieren();
    const r = p.importer(XML, PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, true);
    assert.strictEqual(p.cadre.bin, 'COULEUR');
    assert.strictEqual(p.cadre.appels.length, 1);
    assert.strictEqual(p.cadre.appels[0].fichiers.join('|'), XML);
    assert.strictEqual(p.cadre.appels[0].sansUI, true);
    assert.strictEqual(p.cadre.appels[0].images, false);
    assert.strictEqual(p.projet.sequences[1].name, 'SÉQ (Resolve) - COULEUR');
    assert.deepStrictEqual(p.cadre.ouverts, ['seq-1']);
});

test('l’import refuse un média resté hors du dossier de retour', () => {
    const p = premieren({ medias: [MEDIA[0], '/Users/example/Movies/RUSHS/A.mov'] });
    const r = p.importer(XML, PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /hors du retour/);
});

test('l’import refuse un média rendu manquant', () => {
    const p = premieren({ fichiers: [XML] });
    const r = p.importer(XML, PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /absent ou hors du retour/);
});

test('l’import refuse un nombre de plans différent de l’aller', () => {
    const p = premieren();
    const r = p.importer(XML, PROJET, 'COULEUR', 3);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /nombre de plans non conforme/);
});

test('l’import refuse deux nouvelles séquences', () => {
    const p = premieren({ nouvelles: [sequence('A', 'seq-1', MEDIA.map(m => '//' + m)), sequence('B', 'seq-2', MEDIA.map(m => '//' + m))] });
    const r = p.importer(XML, PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /une seule nouvelle séquence/);
});

test('l’import accepte les chemins bruts de Premiere', () => {
    const p = premieren({ fichiers: [XML].concat(MEDIA),
        medias: ['///' + MEDIA[0].slice(1), '/Volumes' + MEDIA[1]] });
    const r = p.importer(XML, PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, true);
});

test('l’import signale un refus d’import de Premiere', () => {
    const p = premieren({ import: false });
    const r = p.importer(XML, PROJET, 'COULEUR', 2);
    assert.strictEqual(r.ok, false);
    assert.match(r.error, /Import non confirmé/);
});
