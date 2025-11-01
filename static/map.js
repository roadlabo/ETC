/* global L */
(() => {
    const map = L.map('map', {
        zoomControl: true,
    }).setView([35.681236, 139.767125], 6);

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
    }).addTo(map);

    const layerGroup = L.layerGroup().addTo(map);
    const caption = document.getElementById('caption');

    let lastFile = null;

    async function fetchJson(url, options) {
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error(`Request failed: ${response.status}`);
        }
        return response.json();
    }

    function clearMap() {
        layerGroup.clearLayers();
    }

    function drawData(data) {
        clearMap();

        const markers = [];
        data.points.forEach(([lon, lat, flag]) => {
            let style;
            if (flag === 0) {
                style = { radius: 7, color: 'red', weight: 2, fillColor: 'white', fillOpacity: 1 };
            } else if (flag === 1) {
                style = { radius: 7, color: 'blue', weight: 0, fillColor: 'blue', fillOpacity: 1 };
            } else {
                style = { radius: 3, color: 'black', weight: 0, fillColor: 'black', fillOpacity: 1 };
            }

            const marker = L.circleMarker([lat, lon], style);
            marker.addTo(layerGroup);
            markers.push(marker);
        });

        data.segments.forEach((segment) => {
            L.polyline(segment, { color: 'black', weight: 2, opacity: 0.9 }).addTo(layerGroup);
        });

        if (markers.length > 0 || data.segments.length > 0) {
            const bounds = L.latLngBounds([]);
            markers.forEach((marker) => bounds.extend(marker.getLatLng()));
            data.segments.forEach((segment) => {
                segment.forEach((latlng) => bounds.extend(latlng));
            });
            if (bounds.isValid()) {
                map.fitBounds(bounds.pad(0.1));
            }
        } else {
            map.setView([35.681236, 139.767125], 6);
        }

        const fileLabel = data.file || '---';
        caption.textContent = `${fileLabel} / ${data.count}点`;
    }

    async function update() {
        try {
            const current = await fetchJson('/api/current');
            if (!current || !current.file) {
                caption.textContent = 'No file selected';
                clearMap();
                lastFile = null;
                return;
            }

            if (current.file !== lastFile) {
                lastFile = current.file;
                const data = await fetchJson('/api/data');
                drawData(data);
            }
        } catch (error) {
            console.error(error);
            caption.textContent = `Error: ${error.message}`;
            clearMap();
            lastFile = null;
        }
    }

    update();
    setInterval(update, 1000);
})();
