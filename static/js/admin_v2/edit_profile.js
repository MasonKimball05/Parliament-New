/* static/js/admin_v2/edit_profile.js — Edit profile in the console look
 * (admin_v2/console/edit_user_profile.html).
 *
 * The pledge-class sync from components/pledge_class_sync.html, without the
 * inline script: the classes come from the #pledge-class-options datalist
 * (value = semester label, data-greek = Greek name). Picking or typing a known
 * semester fills the Greek name; typing a known Greek name fills an empty
 * semester. The server canonicalizes on save either way.
 */
(function () {
    'use strict';
    var list = document.getElementById('pledge-class-options');
    if (!list) return;
    var byLabel = {}, byGreek = {};
    Array.prototype.forEach.call(list.querySelectorAll('option[data-greek]'), function (o) {
        var c = { label: o.value, greek: o.getAttribute('data-greek') };
        byLabel[c.label.toLowerCase()] = c;
        byGreek[c.greek.toLowerCase()] = c;
    });
    document.querySelectorAll('input[name="pledge_class"]').forEach(function (pc) {
        var form = pc.closest('form');
        var greek = form && form.querySelector('input[name="pledge_class_greek"]');
        if (!greek) return;
        function fromSemester() {
            var c = byLabel[pc.value.trim().toLowerCase()];
            if (c) { pc.value = c.label; greek.value = c.greek; }
        }
        pc.addEventListener('change', fromSemester);
        pc.addEventListener('input', fromSemester);
        greek.addEventListener('change', function () {
            var c = byGreek[greek.value.trim().toLowerCase()];
            if (c) { greek.value = c.greek; if (!pc.value.trim()) pc.value = c.label; }
        });
    });
})();
