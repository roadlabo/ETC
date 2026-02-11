(function(global){
  if (global.L) return;

  function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }

  function Evented(){ this._events = {}; }
  Evented.prototype.on = function(name, fn){
    (this._events[name] = this._events[name] || []).push(fn);
    return this;
  };
  Evented.prototype._emit = function(name, payload){
    (this._events[name] || []).forEach(function(fn){ try { fn(payload); } catch(_) {} });
  };

  function Layer(){ Evented.call(this); this._map = null; }
  Layer.prototype = Object.create(Evented.prototype);
  Layer.prototype.addTo = function(target){ target.addLayer(this); return this; };

  function LayerGroup(){ Layer.call(this); this._layers = []; }
  LayerGroup.prototype = Object.create(Layer.prototype);
  LayerGroup.prototype.addLayer = function(layer){
    this._layers.push(layer);
    if (this._map) layer.addTo(this._map);
    return this;
  };
  LayerGroup.prototype.clearLayers = function(){
    if (this._map){ this._layers.forEach(function(l){ this._map.removeLayer(l); }, this); }
    this._layers = [];
  };
  LayerGroup.prototype._onAdd = function(map){ this._map = map; this._layers.forEach(function(l){ l.addTo(map); }); };
  LayerGroup.prototype._onRemove = function(){ this._map = null; };

  function Map(id){
    Evented.call(this);
    this._container = typeof id === 'string' ? document.getElementById(id) : id;
    this._container.classList.add('leaflet-container');
    this._layers = [];
    this._center = {lat: 0, lon: 0};
    this._zoom = 17;

    this._tilePane = document.createElement('div'); this._tilePane.className = 'leaflet-tile-pane leaflet-pane';
    this._overlayPane = document.createElement('svg'); this._overlayPane.className = 'leaflet-overlay-pane leaflet-pane';
    this._overlayPane.setAttribute('width', '100%'); this._overlayPane.setAttribute('height', '100%');
    this._markerPane = document.createElement('div'); this._markerPane.className = 'leaflet-marker-pane leaflet-pane';
    this._attr = document.createElement('div'); this._attr.className = 'leaflet-control-attribution leaflet-control';

    this._container.appendChild(this._tilePane);
    this._container.appendChild(this._overlayPane);
    this._container.appendChild(this._markerPane);
    this._container.appendChild(this._attr);
  }
  Map.prototype = Object.create(Evented.prototype);
  Map.prototype.addLayer = function(layer){
    if (this._layers.indexOf(layer) >= 0) return this;
    this._layers.push(layer);
    layer._map = this;
    if (layer._onAdd) layer._onAdd(this);
    if (layer._draw) layer._draw();
    return this;
  };
  Map.prototype.removeLayer = function(layer){
    var i = this._layers.indexOf(layer);
    if (i >= 0) this._layers.splice(i, 1);
    if (layer._onRemove) layer._onRemove(this);
    layer._map = null;
    return this;
  };
  Map.prototype.hasLayer = function(layer){ return this._layers.indexOf(layer) >= 0; };
  Map.prototype.setView = function(latlng, zoom){
    this._center = {lat: latlng[0], lon: latlng[1]};
    this._zoom = zoom || this._zoom;
    this._redraw();
    return this;
  };
  Map.prototype.getContainer = function(){ return this._container; };
  Map.prototype._project = function(lat, lon){
    var rect = this._container.getBoundingClientRect();
    var s = Math.pow(2, this._zoom - 17) * 120000;
    return {
      x: rect.width / 2 + (lon - this._center.lon) * s,
      y: rect.height / 2 - (lat - this._center.lat) * s
    };
  };
  Map.prototype._redraw = function(){
    this._layers.forEach(function(l){ if (l._draw) l._draw(); });
  };

  function TileLayer(url, opts){ Layer.call(this); this._url = url; this._opts = opts || {}; this._img = null; }
  TileLayer.prototype = Object.create(Layer.prototype);
  TileLayer.prototype._onAdd = function(map){
    var img = document.createElement('img');
    img.style.width = '100%'; img.style.height = '100%'; img.style.objectFit = 'cover'; img.style.opacity = '0.9';
    img.src = this._url.replace('{s}','a').replace('{z}', String(map._zoom)).replace('{x}','0').replace('{y}','0');
    var self = this;
    img.onload = function(){ self._emit('tileload'); };
    img.onerror = function(){ self._emit('tileerror'); };
    this._img = img;
    map._tilePane.appendChild(img);
    if (this._opts.attribution) map._attr.textContent = this._opts.attribution.replace(/<[^>]+>/g, '');
  };
  TileLayer.prototype._onRemove = function(map){ if (this._img && this._img.parentNode) this._img.parentNode.removeChild(this._img); };

  function Polyline(points, opts){ Layer.call(this); this._points = points; this._opts = opts || {}; this._el = null; }
  Polyline.prototype = Object.create(Layer.prototype);
  Polyline.prototype._onAdd = function(map){
    this._el = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    this._el.setAttribute('fill', 'none');
    this._el.setAttribute('stroke', this._opts.color || '#3388ff');
    this._el.setAttribute('stroke-width', this._opts.weight || 3);
    if (this._opts.dashArray) this._el.setAttribute('stroke-dasharray', this._opts.dashArray);
    map._overlayPane.appendChild(this._el);
    this._draw();
  };
  Polyline.prototype._draw = function(){
    if (!this._map || !this._el) return;
    var pts = this._points.map(function(p){ var q = this._map._project(p[0], p[1]); return q.x + ',' + q.y; }, this);
    this._el.setAttribute('points', pts.join(' '));
  };
  Polyline.prototype._onRemove = function(){ if (this._el && this._el.parentNode) this._el.parentNode.removeChild(this._el); };

  function CircleMarker(latlng, opts){ Layer.call(this); this._latlng = latlng; this._opts = opts || {}; this._el = null; }
  CircleMarker.prototype = Object.create(Layer.prototype);
  CircleMarker.prototype._onAdd = function(map){
    this._el = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    map._overlayPane.appendChild(this._el);
    this._draw();
  };
  CircleMarker.prototype._draw = function(){
    if (!this._map || !this._el) return;
    var p = this._map._project(this._latlng[0], this._latlng[1]);
    this._el.setAttribute('cx', p.x); this._el.setAttribute('cy', p.y);
    this._el.setAttribute('r', this._opts.radius || 4);
    this._el.setAttribute('stroke', this._opts.color || '#3388ff');
    this._el.setAttribute('fill', this._opts.fillColor || this._opts.color || '#3388ff');
    this._el.setAttribute('fill-opacity', clamp(this._opts.fillOpacity == null ? 0.8 : this._opts.fillOpacity, 0, 1));
  };
  CircleMarker.prototype.setLatLng = function(latlng){ this._latlng = latlng; this._draw(); return this; };
  CircleMarker.prototype._onRemove = function(){ if (this._el && this._el.parentNode) this._el.parentNode.removeChild(this._el); };

  function Marker(latlng, opts){ Layer.call(this); this._latlng = latlng; this._opts = opts || {}; this._el = null; }
  Marker.prototype = Object.create(Layer.prototype);
  Marker.prototype._onAdd = function(map){
    this._el = document.createElement('div');
    this._el.style.position = 'absolute';
    var icon = this._opts.icon || {};
    this._el.className = icon.className || '';
    this._el.innerHTML = icon.html || '';
    map._markerPane.appendChild(this._el);
    this._draw();
  };
  Marker.prototype._draw = function(){
    if (!this._map || !this._el) return;
    var p = this._map._project(this._latlng[0], this._latlng[1]);
    this._el.style.left = p.x + 'px';
    this._el.style.top = p.y + 'px';
    this._el.style.transform = 'translate(-50%, -50%)';
  };
  Marker.prototype._onRemove = function(){ if (this._el && this._el.parentNode) this._el.parentNode.removeChild(this._el); };

  global.L = {
    map: function(id){ return new Map(id); },
    layerGroup: function(){ return new LayerGroup(); },
    tileLayer: function(url, opts){ return new TileLayer(url, opts); },
    polyline: function(points, opts){ return new Polyline(points, opts); },
    circleMarker: function(latlng, opts){ return new CircleMarker(latlng, opts); },
    marker: function(latlng, opts){ return new Marker(latlng, opts); },
    divIcon: function(opts){ return opts || {}; }
  };
})(window);
