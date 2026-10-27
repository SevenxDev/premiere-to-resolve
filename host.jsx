/* Chemin stable : Premiere renvoie parfois ///Users/... et /Volumes/Users/... . */
function resolveButtonPosix(value) {
    var s = String(value).replace(/^\/+/, '/');
    if (s.indexOf(':') > -1) s = s.replace(/^[^:]*:/, '/').replace(/:/g, '/').replace(/^\/+/, '/');
    if (s.indexOf('/Volumes/') === 0) s = s.slice(8);
    return s;
}
/* Read-only Premiere export. */
function resolveButtonExport(path) {
    function q(s) { return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t') + '"'; }
    try {
        var s = app.project.activeSequence;
        if (!s) throw new Error("Ouvre une séquence dans Premiere.");
        if (!app.project.path) throw new Error("Enregistre le projet Premiere avant le transfert.");
        var f = new File(path);
        if (f.exists) throw new Error("La destination existe déjà.");
        var ok = s.exportAsFinalCutProXML(path);
        f = new File(path);
        if (!ok || !f.exists || f.length === 0) throw new Error("Premiere n'a pas produit de XML.");
        return '{"ok":true,"name":' + q(s.name) + ',"sequenceId":' + q(s.sequenceID) + ',"projectPath":' + q(app.project.path) + '}';
    } catch (e) { return '{"ok":false,"error":' + q(e.message || e) + '}'; }
}
function resolveButtonImport(xmlPath, projectPath, binName, expectedClips) {
    function q(s) { return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t') + '"'; }
    try {
        if (!projectPath || app.project.path !== projectPath) throw new Error("Ouvre le projet Premiere du transfert. Aucun import effectué.");
        var f = new File(xmlPath);
        if (!f.exists || !f.length) throw new Error("XML des rendus absent.");
        var root = app.project.rootItem, i, before = {}, found = [];
        for (i=0; i<root.children.numItems; i++) {
            if (root.children[i].name === binName) throw new Error("Ce retour a déjà commencé. Vérifie son chutier.");
        }
        for (i=0; i<app.project.sequences.numSequences; i++) before[app.project.sequences[i].sequenceID]=true;
        var bin = root.createBin(binName);
        if (!bin) throw new Error("Création du chutier impossible.");
        if (!app.project.importFiles([xmlPath], true, bin, false)) throw new Error("Import non confirmé. Vérifie le chutier de retour.");
        for (i=0; i<app.project.sequences.numSequences; i++) {
            var s = app.project.sequences[i];
            if (!before[s.sequenceID]) found.push(s);
        }
        if (found.length !== 1) throw new Error("Import effectué : une seule nouvelle séquence était attendue. Vérifie le chutier.");
        var seq = found[0], count = 0, folder = resolveButtonPosix(f.parent.fsName) + '/';
        for (var t=0; t<seq.videoTracks.numTracks; t++) {
            for (var c=0; c<seq.videoTracks[t].clips.numItems; c++) {
                var chemin = resolveButtonPosix(seq.videoTracks[t].clips[c].projectItem.getMediaPath());
                if (chemin.indexOf(folder) !== 0 || !new File(chemin).exists) throw new Error("Import créé mais média vidéo absent ou hors du retour. Vérifie la nouvelle séquence.");
                count++;
            }
        }
        if (!count || (expectedClips && count !== expectedClips)) throw new Error("Import créé mais nombre de plans non conforme.");
        seq.name = seq.name + ' - COULEUR';
        app.project.openSequence(seq.sequenceID);
        return '{"ok":true,"name":' + q(seq.name) + ',"sequenceId":' + q(seq.sequenceID) + ',"message":"Séquence couleur importée. Montage original conservé."}';
    } catch (e) { return '{"ok":false,"error":' + q(e.message || e) + '}'; }
}
