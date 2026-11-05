/*
 * Location check-in area picker (v4.1.0) for the event form
 * (templates/officer/_checkin_area.html). Tap the map, use this device's
 * location, or pick a saved place; the radius select draws the circle and
 * "Off" removes it. Writes the hidden checkin_latitude / checkin_longitude.
 *
 * 11-05-26: search an address (the server asks OpenStreetMap, src/geocode.py),
 * then drag the pin; the circle's size is a slider (25-500 m) synced with the
 * number field (up to 1000 m), and "Turn off" clears it.
 */
(function () {
  'use strict';
  var root = document.querySelector('[data-checkin-area]');
  if (!root || typeof L === 'undefined') return;

  var latInput = root.querySelector('[data-checkin-lat]');
  var lngInput = root.querySelector('[data-checkin-lng]');
  var radiusSelect = root.querySelector('[data-checkin-radius]');
  var placeSelect = root.querySelector('[data-checkin-place]');
  var hereButton = root.querySelector('[data-checkin-here]');
  var status = root.querySelector('[data-checkin-status]');
  var slider = root.querySelector('[data-checkin-slider]');
  var offButton = root.querySelector('[data-checkin-off]');
  var searchBox = root.querySelector('[data-checkin-search]');
  var places = JSON.parse(document.getElementById('checkin-places').textContent || '[]');

  L.Icon.Default.imagePath = root.getAttribute('data-leaflet-images');
  var map = L.map(root.querySelector('[data-checkin-map]'), { scrollWheelZoom: false });
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  var marker = null;
  var circle = null;

  function radius() {
    return parseInt(radiusSelect.value, 10) || 0;
  }

  function say(text) {
    status.textContent = text;
  }

  function draw() {
    var lat = parseFloat(latInput.value);
    var lng = parseFloat(lngInput.value);
    if (circle) { map.removeLayer(circle); circle = null; }
    if (isNaN(lat) || isNaN(lng)) {
      if (marker) { map.removeLayer(marker); marker = null; }
      say(radius() ? 'Tap the map to place the check-in spot.' : 'Off. Search an address, tap the map, or move the slider to turn it on.');
      return;
    }
    var point = [lat, lng];
    if (!marker) {
      marker = L.marker(point, { draggable: true }).addTo(map);
      marker.on('dragend', function () { set(marker.getLatLng().lat, marker.getLatLng().lng); });
    } else {
      marker.setLatLng(point);
    }
    if (radius()) {
      circle = L.circle(point, { radius: radius(), color: '#003da5', weight: 2, fillOpacity: 0.15 }).addTo(map);
      say('Members inside the ' + radius() + ' m circle are checked in. Drag the pin to adjust; use the slider to resize.');
    } else {
      say('Off. Search an address, tap the map, or move the slider to turn it on.');
    }
  }

  function set(lat, lng, zoom) {
    latInput.value = lat.toFixed(6);
    lngInput.value = lng.toFixed(6);
    if (!radius()) { radiusSelect.value = '100'; syncSlider(); }
    draw();
    if (zoom) { map.setView([lat, lng], zoom); fit(); }
  }

  // Show the whole circle (a 250 m circle is wider than the map at street zoom).
  function fit() {
    if (circle) map.fitBounds(circle.getBounds(), { padding: [24, 24], maxZoom: 18 });
  }

  map.on('click', function (e) {
    // From far out, zoom in on the pin so it can be placed precisely.
    set(e.latlng.lat, e.latlng.lng, map.getZoom() < 16 ? 17 : null);
  });
  function syncSlider() {
    if (slider && radius()) slider.value = String(Math.min(radius(), parseInt(slider.max, 10)));
  }
  radiusSelect.addEventListener('change', function () { syncSlider(); draw(); fit(); });
  radiusSelect.addEventListener('input', function () { syncSlider(); draw(); });
  if (slider) {
    slider.addEventListener('input', function () { radiusSelect.value = slider.value; draw(); });
    slider.addEventListener('change', fit);
  }
  if (offButton) {
    offButton.addEventListener('click', function () { radiusSelect.value = ''; draw(); });
  }
  syncSlider();

  // ── Address search ──
  if (searchBox) {
    var addressInput = searchBox.querySelector('[data-checkin-address]');
    var findButton = searchBox.querySelector('[data-checkin-find]');
    var results = searchBox.querySelector('[data-checkin-results]');

    function showResults(rows) {
      results.innerHTML = '';
      rows.forEach(function (r) {
        var li = document.createElement('li');
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200';
        b.textContent = r.label;
        b.addEventListener('click', function () {
          results.classList.add('hidden');
          set(r.lat, r.lng, 18);
          say('Pin dropped at ' + r.label.split(',').slice(0, 2).join(',') + '. Drag it to the exact spot and size the circle.');
        });
        li.appendChild(b);
        results.appendChild(li);
      });
      results.classList.toggle('hidden', !rows.length);
    }

    function find() {
      var q = addressInput.value.trim();
      if (q.length < 3) { say('Type an address or place name.'); return; }
      say('Searching…');
      findButton.disabled = true;
      fetch(searchBox.getAttribute('data-url') + '?q=' + encodeURIComponent(q), {
        credentials: 'same-origin', headers: { 'Accept': 'application/json' },
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.error) { say(data.error); showResults([]); return; }
        if (!data.results.length) { say('No match. Try adding the city, or tap the map.'); showResults([]); return; }
        if (data.results.length === 1) {
          showResults([]);
          set(data.results[0].lat, data.results[0].lng, 18);
          say('Pin dropped at ' + data.results[0].label.split(',').slice(0, 2).join(',') + '. Drag it to the exact spot and size the circle.');
        } else {
          say('Pick the right one:');
          showResults(data.results);
        }
      }).catch(function () {
        say('Couldn’t search right now. Tap the map instead.');
      }).then(function () { findButton.disabled = false; });
    }

    findButton.addEventListener('click', find);
    // Enter searches; it must not submit the event form.
    addressInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); find(); }
    });
  }

  if (placeSelect) {
    placeSelect.addEventListener('change', function () {
      var chosen = places.filter(function (p) { return String(p.id) === placeSelect.value; })[0];
      if (!chosen) return;
      radiusSelect.value = String(chosen.radius);
      syncSlider();
      set(chosen.lat, chosen.lng, 18);
    });
  }

  hereButton.addEventListener('click', function () {
    if (!navigator.geolocation) { say('This browser can’t share its location.'); return; }
    say('Finding you…');
    navigator.geolocation.getCurrentPosition(
      function (pos) { set(pos.coords.latitude, pos.coords.longitude, 18); },
      function () { say('Couldn’t get your location. Tap the map instead.'); },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  });

  var startLat = parseFloat(latInput.value);
  var startLng = parseFloat(lngInput.value);
  if (!isNaN(startLat) && !isNaN(startLng)) {
    map.setView([startLat, startLng], 18);
    draw(); fit();
  } else if (places.length) {
    map.setView([places[0].lat, places[0].lng], 16);
  } else {
    map.setView([39.8283, -98.5795], 4);   // the US until a pin goes down
  }
  draw();

  // The panel may have been hidden while the map was built.
  var toggle = document.getElementById('attendance-toggle-btn');
  if (toggle) toggle.addEventListener('click', function () { setTimeout(function () { map.invalidateSize(); }, 50); });
})();
