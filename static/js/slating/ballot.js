/* static/js/slating/ballot.js — the slating ballots (templates/slating/vote.html,
 * vote_individual.html), 11-05-26.
 *
 * 1. Cast Vote (and the passkey button) stay disabled until the ballot is
 *    complete: a slate choice, and for Reject at least one position; on an
 *    individual ballot, every position. A line above the button says what's
 *    missing. The confirm before casting is the form's data-confirm
 *    (static/js/base/data-actions.js); the server still checks everything.
 * 2. The slate-changed banner: every 30 s, voting_status's slate_fingerprint
 *    against the one the page was rendered with (a hash of the ballot rows,
 *    src/view/slating/api.py ballot_fingerprint). A status change reloads.
 * No inline script: prod CSP has no 'unsafe-inline' (src/middleware/security.py).
 */
(function () {
    'use strict';

    function slateProblem(form) {
        var choice = form.querySelector('input[name="vote_choice"]:checked');
        if (!choice) return 'Choose how you vote to continue.';
        if (choice.value === 'reject' &&
            !form.querySelector('input[name="rejected_positions"]:checked')) {
            return 'Choose at least one position you object to.';
        }
        return '';
    }

    function individualProblem(form) {
        var groups = {};
        form.querySelectorAll('input[type="radio"]').forEach(function (r) {
            if (!(r.name in groups)) groups[r.name] = false;
            if (r.checked) groups[r.name] = true;
        });
        var left = Object.keys(groups).filter(function (k) { return !groups[k]; }).length;
        if (!left) return '';
        return 'Vote on every position to continue (' + left + ' left).';
    }

    document.querySelectorAll('form.js-ballot-form').forEach(function (form) {
        var isSlate = !!form.querySelector('input[name="vote_choice"]');
        var submit = form.querySelector('[type="submit"]');
        var passkey = form.querySelector('.js-vote-passkey');
        var hint = form.querySelector('.js-ballot-hint');

        function update() {
            if (form.dataset.submitted === '1') return;   // in flight: leave the guard's disabled button alone
            var problem = isSlate ? slateProblem(form) : individualProblem(form);
            if (submit) submit.disabled = !!problem;
            if (passkey && !passkey.textContent.match(/Waiting/)) passkey.disabled = !!problem;
            if (hint) {
                hint.textContent = problem;
                hint.classList.toggle('hidden', !problem);
            }
        }
        form.addEventListener('change', update);
        // The vote cards' click-through and the reject list's reset fire change; this catches the rest.
        form.addEventListener('click', function () { setTimeout(update, 0); });
        update();
    });

    var banner = document.getElementById('slate-update-banner');
    if (banner && banner.dataset.statusUrl) {
        var shown = false;
        var check = function () {
            fetch(banner.dataset.statusUrl, { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) return;
                    if (data.status !== banner.dataset.status) {   // e.g. voting paused or closed
                        window.location.reload();
                        return;
                    }
                    if (!shown && data.slate_fingerprint &&
                        data.slate_fingerprint !== banner.dataset.slateFingerprint) {
                        shown = true;
                        banner.classList.remove('hidden');
                    }
                })
                .catch(function () {});
        };
        setInterval(check, 30000);
    }
})();
