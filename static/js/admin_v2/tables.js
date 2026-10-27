/* static/js/admin_v2/tables.js — v3.58.1.
 * Phone layout for admin-v2 tables. Thirteen admin pages are wide tables
 * that a phone could only show two or three columns of, sideways-scrolling
 * for the rest. This copies each column's heading onto its cells as
 * data-label; under 640px the CSS (.admin-v2-content table in
 * static/css/tailwind-input.css) turns every row into a card and shows the
 * label beside each value. Wider screens keep the table as it is.
 * Tables without a <thead>, and rows with colspan (empty states), are left alone.
 */
(function () {
    document.querySelectorAll('.admin-v2-content table').forEach(function (table) {
        var head = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
        if (!head) return;
        var labels = Array.prototype.map.call(head.cells, function (th) { return th.textContent.trim(); });
        table.classList.add('admin-v2-stack');
        Array.prototype.forEach.call(table.tBodies, function (body) {
            Array.prototype.forEach.call(body.rows, function (row) {
                if (row.cells.length !== labels.length) { row.classList.add('admin-v2-stack-full'); return; }
                Array.prototype.forEach.call(row.cells, function (cell, i) {
                    if (labels[i] && !cell.hasAttribute('data-label')) cell.setAttribute('data-label', labels[i]);
                });
            });
        });
    });
})();
