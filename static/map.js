(() => {
const map = L.map('map', { zoomControl: true, maxZoom: 19 }).setView([-41.47, -72.94], 7);
const incidentsLayer = L.layerGroup();
const geoLayers = L.layerGroup();
const mapNotice = document.querySelector('#mapNotice');

window.appMap = map;
window.incidentsLayer = incidentsLayer;
window.geoLayers = geoLayers;

let mapNoticeTimer;

function showMapNotice(message, persistent = false) {
  window.clearTimeout(mapNoticeTimer);
  mapNotice.textContent = message;
  mapNotice.classList.toggle('hidden', !message);
  if (message && !persistent) {
    mapNoticeTimer = window.setTimeout(() => {
      mapNotice.textContent = '';
      mapNotice.classList.add('hidden');
    }, 8000);
  }
}

function createOfflineLayer() {
  const OfflineGridLayer = L.GridLayer.extend({
    createTile(coords, done) {
      const tile = document.createElement('canvas');
      const size = this.getTileSize();
      tile.width = size.x;
      tile.height = size.y;
      const context = tile.getContext('2d');

      context.fillStyle = '#eaf1ef';
      context.fillRect(0, 0, size.x, size.y);
      context.strokeStyle = '#d6e2de';
      context.lineWidth = 1;
      for (let position = 0; position <= size.x; position += 64) {
        context.beginPath();
        context.moveTo(position, 0);
        context.lineTo(position, size.y);
        context.moveTo(0, position);
        context.lineTo(size.x, position);
        context.stroke();
      }

      const northwest = map.unproject(L.point(coords.x * size.x, coords.y * size.y), coords.z);
      context.fillStyle = '#71827e';
      context.font = '12px sans-serif';
      context.fillText(`${northwest.lat.toFixed(2)}°, ${northwest.lng.toFixed(2)}°`, 12, 22);
      done(null, tile);
      return tile;
    }
  });
  return new OfflineGridLayer({ maxZoom: 19, attribution: 'Cuadrícula local' });
}

function createResilientLayer(providers) {
  const layerGroup = L.layerGroup();
  let activeLayer = null;

  function activateProvider(index) {
    if (activeLayer) layerGroup.removeLayer(activeLayer);

    if (index >= providers.length) {
      activeLayer = createOfflineLayer();
      layerGroup.addLayer(activeLayer);
      showMapNotice('No se pudo conectar con los proveedores del mapa. Se muestra una cuadrícula local; los reportes y la selección de ubicación siguen disponibles.', true);
      return;
    }

    const provider = providers[index];
    activeLayer = L.tileLayer(provider.url, {
      attribution: provider.attribution,
      subdomains: provider.subdomains || 'abc',
      maxZoom: provider.maxZoom || 19,
      // Los proveedores de mosaicos exigen Referer; se envía solo el origen.
      referrerPolicy: 'strict-origin-when-cross-origin'
    });
    const layer = activeLayer;

    // Se cambia de proveedor solo si fallan varios mosaicos seguidos y ninguno
    // ha cargado, para no descartar un proveedor por un error aislado.
    let failures = 0;
    let loaded = false;
    const onTileError = () => {
      if (loaded || !map.hasLayer(layerGroup) || activeLayer !== layer) return;
      failures += 1;
      if (failures < 3) return;
      layer.off('tileerror', onTileError);
      activateProvider(index + 1);
      if (index + 1 < providers.length) {
        showMapNotice(`El mapa cambió automáticamente al proveedor ${providers[index + 1].name}.`);
      }
    };
    layer.on('tileerror', onTileError);
    layer.once('tileload', () => {
      loaded = true;
      if (activeLayer === layer) showMapNotice('');
    });
    layerGroup.addLayer(layer);
  }

  activateProvider(0);
  return layerGroup;
}

const openStreetMapAttribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const streetLayer = createResilientLayer([
  { name: 'OpenStreetMap', url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', subdomains: 'abc', attribution: openStreetMapAttribution },
  { name: 'CARTO', url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', subdomains: 'abcd', attribution: `${openStreetMapAttribution} &copy; <a href="https://carto.com/attributions">CARTO</a>` },
  { name: 'Esri World Street Map', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', subdomains: '', attribution: 'Tiles &copy; Esri' }
]);

streetLayer.addTo(map);
incidentsLayer.addTo(map);
geoLayers.addTo(map);
})();
