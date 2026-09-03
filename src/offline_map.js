/* GSI pale-map layer with online-first, on-disk-fallback, white-outside policy. */
(function (root) {
  "use strict";

  function transparentPixel() {
    return "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=";
  }

  function addGsiOfflineLayer(map, options) {
    options = options || {};
    var localTemplate = options.localUrl || "tiles/gsi_pale/{z}/{x}/{y}.png";
    var remoteTemplate = options.remoteUrl ||
      "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png";
    var preferLocal = !!options.preferLocal;
    var Layer = L.TileLayer.extend({
      createTile: function (coords, done) {
        var tile = document.createElement("img");
        tile.alt = "";
        tile.setAttribute("role", "presentation");
        var localUrl = L.Util.template(localTemplate, coords);
        var remoteUrl = L.Util.template(remoteTemplate, coords);
        var triedRemote = false;
        var triedLocal = false;
        var finished = false;
        function complete(error) {
          if (finished) return;
          finished = true;
          done(error || null, tile);
        }
        function loadRemote() {
          triedRemote = true;
          tile.src = remoteUrl;
        }
        function loadLocal() {
          triedLocal = true;
          tile.src = localUrl;
        }
        tile.onload = function () { complete(); };
        tile.onerror = function () {
          if (preferLocal && !triedRemote && navigator.onLine !== false) return loadRemote();
          if (!preferLocal && !triedRemote && navigator.onLine !== false) return loadRemote();
          if (!triedLocal) return loadLocal();
          tile.src = transparentPixel();
          complete();
        };
        if (preferLocal) loadLocal();
        else if (navigator.onLine !== false) loadRemote();
        else loadLocal();
        return tile;
      }
    });
    return new Layer("", {
      minZoom: options.minZoom || 5,
      maxZoom: options.maxZoom || 18,
      attribution: options.attribution ||
        '<a href="https://maps.gsi.go.jp/development/ichiran.html">地理院タイル</a>'
    }).addTo(map);
  }

  root.addGsiOfflineLayer = addGsiOfflineLayer;
})(window);
