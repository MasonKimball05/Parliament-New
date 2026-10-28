/*
 * Location check-in area picker (v3.60.0) for the event form
 * (templates/officer/_checkin_area.html). Tap the map, use this device's
 * location, or pick a saved place; the radius select draws the circle and
 * "Off" removes it. Writes the hidden checkin_latitude / checkin_longitude.
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
      say(radius() ? 'Tap the map to place the check-in spot.' : 'Off. Pick a radius to turn it on.');
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
      say('Members inside the circle are checked in. Drag the pin to adjust.');
    } else {
      say('Off. Pick a radius to turn it on.');
    }
  }

  function set(lat, lng, zoom) {
    latInput.value = lat.toFixed(6);
    lngInput.value = lng.toFixed(6);
    if (!radius()) radiusSelect.value = '100';
    draw();
    if (zoom) map.setView([lat, lng], zoom);
  }

  map.on('click', function (e) {
    // From far out, zoom in on the pin so it can be placed precisely.
    set(e.latlng.lat, e.latlng.lng, map.getZoom() < 16 ? 17 : null);
  });
  radiusSelect.addEventListener('change', draw);

  if (placeSelect) {
    placeSelect.addEventListener('change', function () {
      var chosen = places.filter(function (p) { return String(p.id) === placeSelect.value; })[0];
      if (!chosen) return;
      radiusSelect.value = String(chosen.radius);
      if (radiusSelect.value !== String(chosen.radius)) radiusSelect.value = '100';
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
