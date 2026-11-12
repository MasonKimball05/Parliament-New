/* static/js/slating/interview-notes.js — the record-interview page (11-12-26).
 *
 * Mason lost pages of notes when his connection dropped mid-interview and
 * only found out on Save. Now:
 *   - Every change is copied to this device (localStorage) as you type, and
 *     put back when the page is opened again if it's newer than what the
 *     server has ("Use the saved version instead" undoes that). The copy is
 *     removed once the server has the same text, so confidential notes don't
 *     sit on a shared computer, and anything older than 14 days is dropped.
 *   - Every 30 seconds, while something changed and the browser is online,
 *     the form is saved to the server as a draft (slating_interview_draft:
 *     it doesn't mark the interview completed). If someone else saved this
 *     interview since the page loaded, background saving stops instead of
 *     overwriting them; the copy on this device stays.
 *   - Save Interview Notes first checks the server can be reached. If not,
 *     it doesn't submit (which is how the notes were lost) and says so.
 * Without JavaScript the form posts exactly as before.
 * No inline script: prod CSP has no 'unsafe-inline' (src/middleware/security.py).
 */
(function () {
    'use strict';
    var form = document.getElementById('interview-notes-form');
    if (!form) return;

    var KEY = 'parliament:interview-notes:' + form.dataset.user + ':' + form.dataset.interview;
    var MAX_AGE = 14 * 24 * 60 * 60 * 1000;
    var SYNC_EVERY = 30 * 1000;
    var draftUrl = form.dataset.draftUrl;
    var base = form.dataset.updated || '';
    var statusEl = document.getElementById('js-notes-status');
    var banner = document.getElementById('js-notes-restored');
    var bannerText = document.getElementById('js-notes-restored-text');
    var useServer = document.getElementById('js-notes-use-server');
    var conflict = false;
    var submitting = false;

    function read() {
        var positions = Array.prototype.slice.call(form.querySelectorAll('input[name="recommended_positions"]:checked'))
            .map(function (el) { return el.value; }).sort();
        return {
            notes: form.elements.notes.value,
            strengths: form.elements.strengths.value,
            concerns: form.elements.concerns.value,
            recommendation: form.elements.recommendation.value,
            positions: positions
        };
    }

    function apply(v) {
        form.elements.notes.value = v.notes || '';
        form.elements.strengths.value = v.strengths || '';
        form.elements.concerns.value = v.concerns || '';
        form.elements.recommendation.value = v.recommendation || '';
        Array.prototype.forEach.call(form.querySelectorAll('input[name="recommended_positions"]'), function (el) {
            el.checked = (v.positions || []).indexOf(el.value) !== -1;
        });
    }

    function key(v) { return JSON.stringify(v); }

    function load() {
        try { return JSON.parse(window.localStorage.getItem(KEY) || 'null'); } catch (e) { return null; }
    }
    function store(v) {
        try { window.localStorage.setItem(KEY, JSON.stringify({ values: v, at: Date.now() })); return true; }
        catch (e) { return false; }
    }
    function forget() {
        try { window.localStorage.removeItem(KEY); } catch (e) { /* storage blocked: nothing to remove */ }
    }

    var TONES = {
        '': 'text-xs text-right text-gray-500 dark:text-gray-400',
        ok: 'text-xs text-right text-green-700 dark:text-green-400',
        warn: 'text-xs text-right font-medium text-amber-700 dark:text-amber-300'
    };
    function setStatus(text, tone) {
        if (!statusEl) return;
        statusEl.textContent = text;
        statusEl.className = TONES[tone || ''] || TONES[''];
    }

    function timeLabel(ms) {
        return new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }

    var serverCopy = read();
    var lastSynced = key(serverCopy);

    // A copy left on this device: put it back if it isn't what the server has.
    var saved = load();
    if (saved && saved.values) {
        if (Date.now() - (saved.at || 0) > MAX_AGE || key(saved.values) === lastSynced) {
            forget();
        } else {
            apply(saved.values);
            if (banner) {
                bannerText.textContent = 'Restored notes you hadn’t saved yet, kept on this device at ' +
                    timeLabel(saved.at) + '.';
                banner.classList.remove('hidden');
            }
            setStatus('Not saved to the server yet. Kept on this device.', 'warn');
        }
    }
    if (useServer) {
        useServer.addEventListener('click', function () {
            apply(serverCopy);
            forget();
            banner.classList.add('hidden');
            setStatus('');
        });
    }

    var typing = null;
    function changed() {
        clearTimeout(typing);
        typing = setTimeout(function () {
            var v = read();
            if (key(v) === lastSynced) { forget(); return; }
            if (store(v)) {
                setStatus(conflict ? 'Kept on this device (not saved to the server: see above).'
                                   : 'Kept on this device. Saving to the server shortly…', conflict ? 'warn' : '');
            }
        }, 400);
    }
    form.addEventListener('input', changed);
    form.addEventListener('change', changed);

    function body() {
        var data = new FormData(form);
        data.append('base_updated', base);
        return data;
    }

    // Save to the server. Resolves 'saved' | 'conflict' | 'offline' | 'error'.
    function sync() {
        var v = read();
        var sent = key(v);
        return fetch(draftUrl, {
            method: 'POST', body: body(), credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                if (r.ok) {
                    base = data.updated || base;
                    lastSynced = sent;
                    if (key(read()) === sent) forget();
                    setStatus('Draft saved to the server at ' + (data.saved_at || timeLabel(Date.now())) + '.', 'ok');
                    return 'saved';
                }
                if (r.status === 409) {
                    conflict = true;
                    store(read());
                    setStatus((data.error || 'Someone else saved this interview.') +
                        ' Your notes are kept on this device; copy anything you need before reloading.', 'warn');
                    return 'conflict';
                }
                setStatus('Couldn’t save a draft (' + (data.error || r.status) + '). Your notes are kept on this device.', 'warn');
                return 'error';
            });
        }).catch(function () {
            store(read());
            setStatus('Offline. Your notes are kept on this device and will be saved when you’re back online.', 'warn');
            return 'offline';
        });
    }

    setInterval(function () {
        if (conflict || submitting || navigator.onLine === false) return;
        if (key(read()) !== lastSynced) sync();
    }, SYNC_EVERY);
    window.addEventListener('online', function () {
        if (!conflict && key(read()) !== lastSynced) sync();
    });
    window.addEventListener('offline', function () {
        if (key(read()) !== lastSynced) store(read());
        setStatus('Offline. Your notes are kept on this device.', 'warn');
    });

    // Save Interview Notes: make sure the server can be reached first.
    form.addEventListener('submit', function (e) {
        if (submitting) return;
        e.preventDefault();
        store(read());
        var button = form.querySelector('button[type="submit"]');
        if (button) button.disabled = true;
        var check = conflict ? fetch(draftUrl, { method: 'HEAD', credentials: 'same-origin' }).then(function () { return 'reachable'; }, function () { return 'offline'; })
                             : sync();
        check.then(function (result) {
            if (result === 'offline') {
                if (button) button.disabled = false;
                setStatus('Can’t reach the server, so nothing was submitted. Your notes are kept on this device: ' +
                          'try again when you’re back online.', 'warn');
                return;
            }
            submitting = true;
            form.submit();
        });
    });
})();
