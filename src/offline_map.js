/* GSI pale-map layer with an on-disk-first, online-fallback policy. */
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
    var Layer = L.TileLayer.extend({
      createTile: function (coords, done) {
        var tile = document.createElement("img");
        tile.alt = "";
        tile.setAttribute("role", "presentation");
        var localUrl = L.Util.template(localTemplate, coords);
        var remoteUrl = L.Util.template(remoteTemplate, coords);
        var finished = false;
        function complete(error) {
          if (finished) return;
          finished = true;
          done(error || null, tile);
        }
        tile.onload = function () { complete(); };
        tile.onerror = function () {
          if (tile.src.indexOf(remoteUrl) === -1 && navigator.onLine !== false) {
            tile.src = remoteUrl;
          } else {
            tile.src = transparentPixel();
            complete();
          }
        };
        tile.src = localUrl;
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
