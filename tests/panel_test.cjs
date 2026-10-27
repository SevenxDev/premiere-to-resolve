'use strict';
/* Tests du panneau : DOM, historique, enchaînement aller et gestion d'erreur.
   Aucun accès réseau ni Premiere : evalScript et child_process sont remplacés. */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');

const ICI = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(path.join(ICI, 'index.html'), 'utf8');
const SOURCE = fs.readFileSync(path.join(ICI, 'panel.js'), 'utf8');

function element(id) {
    return {
        id, value: '', hidden: false, disabled: false, children: [],
        listeners: {}, classList: { classes: new Set(), toggle(c, on) { on ? this.classes.add(c) : this.classes.delete(c); } },
        get textContent() { return this._texte || ''; },
        set textContent(v) { this._texte = v; if (v === '') this.children = []; },
        appendChild(c) { this.children.push(c); },
        removeAttribute() { },
        addEventListener(type, fn) { this.listeners[type] = fn; },
        click() { return this.listeners.click ? this.listeners.click() : null; }
    };
}

function harness(options = {}) {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vdv-panel-'));
    const base = path.join(home, 'Movies', 'Vers DaVinci');
    const env = { home, base, jobs: {}, spawned: [], calls: [], scripts: [], status: '', error: false, exec: null };
    const make = (name, source, result, ageMs) => {
        const dir = path.join(base, name);
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(path.join(dir, 'source.json'), JSON.stringify(source), 'utf8');
        if (result) {
            fs.writeFileSync(path.join(dir, 'result.json'), JSON.stringify(result), 'utf8');
            const when = new Date(Date.now() - (ageMs || 0));
            fs.utimesSync(path.join(dir, 'result.json'), when, when);
        }
        env.jobs[name] = dir;
        return dir;
    };
    if (!options.vide) {
        make('COLOR-neuf', { ok: true, name: 'SÉQ A', projectPath: '/p/a.prproj' }, { ok: true, message: 'ok' }, 0);
        make('COLOR-ancien', { ok: true, name: 'SÉQ B' }, { ok: true, message: 'ok' }, 60000);
        make('COLOR-casse', { ok: true, name: 'SÉQ C' }, null, 0);
    }
    fs.mkdirSync(path.join(base, 'AUTRE'), { recursive: true });

    const elements = {};
    for (const id of ['status', 'state', 'return', 'send', 'history', 'refresh', 'folder', 'progress', 'details']) elements[id] = element(id);
    elements.state.classList.toggle = function (c, on) { env.error = !!on; this.classes[c] = on; };

    const document = {
        getElementById: id => elements[id] || null,
        createElement: () => element('option')
    };
    const nodeRequire = name => {
        if (name === 'os') return { homedir: () => env.home };
        if (name === 'child_process') return {
            spawn: (bin, args) => {
                const child = new EventEmitter();
                child.stdout = new EventEmitter();
                child.stderr = new EventEmitter();
                child.killed = false;
                child.kill = () => { child.killed = true; };
                env.calls.push({ bin, args: args.slice() });
                if (args[2] === '--job') {
                    fs.writeFileSync(path.join(args[3], 'result.json'), JSON.stringify({ ok: true, name: 'SÉQ A' }), 'utf8');
                }
                const script = env.scripts.shift() || 'ok';
                setImmediate(() => {
                    if (script === 'ok') {
                        child.stdout.emit('data', JSON.stringify({ ok: true, message: args.includes('--check') ? 'Resolve prêt.' : 'Import réussi.' }) + '\n');
                        child.emit('close', 0);
                    } else if (script === 'progress') {
                        child.stdout.emit('data', JSON.stringify({ type: 'progress', progress: 42 }) + '\n');
                        setImmediate(() => {
                            child.stdout.emit('data', JSON.stringify({ ok: true, ready_for_import: true, outputDir: '/o', xmlPath: '/o/r.xml', videoClips: 14, message: 'Rendu terminé.' }) + '\n');
                            child.emit('close', 0);
                        });
                    } else if (script === 'echec') {
                        child.stdout.emit('data', JSON.stringify({ ok: false, error: 'Resolve a refusé.' }) + '\n');
                        child.emit('close', 1);
                    } else {
                        child.emit('close', 3);
                    }
                });
                return child;
            },
            execFile: (bin, args, cb) => { env.exec = args; cb(null); }
        };
        return require(name);
    };
    const window = {
        cep_node: { require: nodeRequire },
        __adobe_cep__: {
            evalScript(code, cb) {
                const call = code.slice(code.lastIndexOf('\n') + 1);
                env.lastScript = call;
                const reponse = call.startsWith('resolveButtonExport')
                    ? { ok: true, name: 'SÉQ A', sequenceId: 'seq-1', projectPath: '/p/a.prproj' }
                    : { ok: true, name: 'SÉQ A - COULEUR', sequenceId: 'seq-2', message: 'Séquence couleur importée. Montage original conservé.' };
                setImmediate(() => cb(JSON.stringify(reponse)));
            }
        }
    };
    const contexte = require('node:vm').createContext({
        window, document, location: { pathname: path.join(ICI, 'index.html') },
        require: nodeRequire, console, process: { env: {} }, decodeURIComponent, setTimeout, clearTimeout, setImmediate, Date, JSON, Promise, Number, Math, Error, String, Object, Array
    });
    require('node:vm').runInContext(SOURCE, contexte, { filename: 'panel.js' });
    return { env, elements, base, home, window, run: () => require('node:vm').runInContext('history()', contexte) };
}

test('chaque identifiant utilisé par le panneau existe dans la page', () => {
    const utilises = [...SOURCE.matchAll(/\$\('([^']+)'\)/g)].map(m => m[1]);
    const ids = new Set([...HTML.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
    const manquants = [...new Set(utilises)].filter(id => !ids.has(id));
    assert.strictEqual(manquants.join('|'), '', 'identifiants absents de index.html');
});

test('l’historique ne garde que les transferts réussis, du plus récent au plus ancien', () => {
    const h = harness();
    h.run();
    const options = h.elements.history.children.map(o => o.value);
    assert.strictEqual(options.join('|'), [h.env.jobs['COLOR-neuf'], h.env.jobs['COLOR-ancien']].join('|'));
    const textes = h.elements.history.children.map(o => o.textContent);
    assert.ok(textes[1].includes('ancien transfert'), 'transfert sans projet signalé');
    assert.ok(!textes[0].includes('ancien transfert'));
});

test('historique vide : message explicite, bouton retour désactivé', () => {
    const h = harness({ vide: true });
    h.run();
    assert.strictEqual(h.elements.history.children.length, 1);
    assert.strictEqual(h.elements.history.children[0].value, '');
    assert.strictEqual(h.elements.return.disabled, true);
});

test('le retour n’est ouvert que si le transfert connaît son projet Premiere', () => {
    const h = harness();
    h.run();
    h.elements.history.value = h.env.jobs['COLOR-neuf'];
    h.elements.history.listeners.change();
    assert.strictEqual(h.elements.return.disabled, false);
    h.elements.history.value = h.env.jobs['COLOR-ancien'];
    h.elements.history.listeners.change();
    assert.strictEqual(h.elements.return.disabled, true);
});

test('l’envoi exporte, écrit la provenance puis lance le pont', async () => {
    const h = harness();
    h.run();
    await h.elements.send.click();
    assert.ok(h.env.lastScript.startsWith('resolveButtonExport('), 'export demandé à Premiere');
    assert.deepStrictEqual(h.env.calls.map(c => path.basename(c.args[1])), ['send.py', 'send.py']);
    assert.deepStrictEqual(h.env.calls.map(c => c.args[2]), ['--check', '--job']);
    const crees = fs.readdirSync(h.base).filter(n => n.startsWith('COLOR-') && !h.env.jobs[n]);
    assert.strictEqual(crees.length, 1, 'un seul dossier de transfert créé');
    const source = JSON.parse(fs.readFileSync(path.join(h.base, crees[0], 'source.json'), 'utf8'));
    assert.strictEqual(source.projectPath, '/p/a.prproj');
    assert.strictEqual(h.elements.history.value, path.join(h.base, crees[0]));
    assert.strictEqual(h.elements.status.textContent, 'Import réussi.');
    assert.strictEqual(h.env.error, false);
    assert.strictEqual(h.elements.send.disabled, false, 'boutons rendus après le travail');
});

test('une erreur du pont est affichée et colore l’état', async () => {
    const h = harness();
    h.run();
    h.env.scripts.push('ok', 'echec');
    await h.elements.send.click();
    assert.strictEqual(h.elements.status.textContent, 'Resolve a refusé.');
    assert.strictEqual(h.env.error, true);
    assert.strictEqual(h.elements.progress.hidden, true);
});

test('un pont muet ne laisse pas croire à un succès', async () => {
    const h = harness();
    h.run();
    h.env.scripts.push('ok', 'muet');
    await h.elements.send.click();
    assert.match(h.elements.status.textContent, /sans confirmer/);
    assert.strictEqual(h.env.error, true);
});

test('la progression du rendu remonte jusqu’au panneau', async () => {
    const h = harness();
    h.run();
    h.env.scripts.push('ok', 'progress', 'ok');
    await h.elements.send.click();
    assert.strictEqual(h.elements.progress.value, 42);
    assert.strictEqual(h.elements.status.textContent, 'Rendu terminé.');
});

test('le retour réutilise un rendu déjà prêt sans relancer Resolve', async () => {
    const h = harness();
    h.run();
    const dir = h.env.jobs['COLOR-neuf'];
    const sortie = path.join(h.env.home, 'retour-1');
    fs.mkdirSync(sortie, { recursive: true });
    fs.writeFileSync(path.join(dir, 'return.json'), JSON.stringify({ ok: true, ready_for_import: true, outputDir: sortie, xmlPath: path.join(sortie, 'r.xml'), videoClips: 14 }), 'utf8');
    h.elements.history.value = dir;
    h.elements.history.listeners.change();
    await h.elements.return.click();
    assert.strictEqual(h.env.calls.length, 0, 'aucun rendu relancé');
    assert.ok(h.env.lastScript.startsWith('resolveButtonImport('));
    assert.match(h.env.lastScript, /14/, 'nombre de plans transmis au contrôle');
    const marqueur = JSON.parse(fs.readFileSync(path.join(sortie, 'premiere-import.json'), 'utf8'));
    assert.strictEqual(marqueur.name, 'SÉQ A - COULEUR');
    assert.strictEqual(h.elements.status.textContent, 'Séquence couleur importée. Montage original conservé.');
});

test('un retour déjà importé n’est pas rejoué', async () => {
    const h = harness();
    h.run();
    const dir = h.env.jobs['COLOR-neuf'];
    const sortie = path.join(h.env.home, 'retour-2');
    fs.mkdirSync(sortie, { recursive: true });
    fs.writeFileSync(path.join(dir, 'return.json'), JSON.stringify({ ok: true, ready_for_import: true, outputDir: sortie, xmlPath: path.join(sortie, 'r.xml'), videoClips: 14 }), 'utf8');
    fs.writeFileSync(path.join(sortie, 'premiere-import.json'), JSON.stringify({ ok: true }), 'utf8');
    h.elements.history.value = dir;
    h.elements.history.listeners.change();
    h.env.scripts.push('ok');
    await h.elements.return.click();
    assert.strictEqual(h.env.scripts.length, 0, 'le rendu est relancé');
    assert.strictEqual(h.env.calls.length, 1);
    assert.ok(h.env.calls[0].args.includes('--job'));
});

test('le dossier des transferts s’ouvre dans le Finder', async () => {
    const h = harness();
    h.run();
    h.elements.folder.click();
    assert.strictEqual(h.env.exec.join('|'), h.base);
});
