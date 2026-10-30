/* static/js/admin_v2/trends.js — v4.1.0. The admin Trends page.
 * One single-series bar chart per <canvas data-trend="id">, data from the
 * json_script #trend-data. Colors: one hue, validated with the dataviz
 * palette checks (light #2a56b8 on white, dark #4a72c9 on gray-800); the
 * site's own primary-600 #003da5 is a step too dark for marks on white. Grid
 * and axes recessive; hover tooltip per bar; numbers also in the table.
 */
(function () {
    var el = document.getElementById('trend-data');
    if (!el || typeof Chart === 'undefined') return;
    var data = JSON.parse(el.textContent);
    var dark = document.documentElement.classList.contains('dark');
    var bar = dark ? '#4a72c9' : '#2a56b8';
    var ink = dark ? '#9ca3af' : '#6b7280';
    var grid = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

    data.series.forEach(function (s) {
        var canvas = document.querySelector('canvas[data-trend="' + s.id + '"]');
        if (!canvas) return;
        new Chart(canvas, {
            type: 'bar',
            data: { labels: data.labels, datasets: [{ label: s.title, data: s.values, backgroundColor: bar,
                borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'bottom', maxBarThickness: 18 }] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { displayColors: false, callbacks: {
                        title: function (items) { return 'Week of ' + items[0].label; },
                        label: function (item) { return item.formattedValue + ' ' + s.unit; } } },
                },
                scales: {
                    x: { grid: { display: false }, ticks: { color: ink, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 } },
                    y: { beginAtZero: true, grid: { color: grid }, border: { display: false },
                         ticks: { color: ink, precision: 0, maxTicksLimit: 4 } },
                },
            },
        });
    });
})();
