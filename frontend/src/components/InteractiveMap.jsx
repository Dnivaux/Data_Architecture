import { useMemo, useState, useEffect, useRef } from 'react';
import { MapContainer, GeoJSON, TileLayer, useMap, CircleMarker, Popup } from 'react-leaflet';
import L from 'leaflet';
import wellknown from 'wellknown';
import { indicatorColor } from '../utils/scoreColors';
import { INDICATOR_OPTIONS } from './Sidebar';

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
  'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

const QUARTIERS_LOCAL = '/data/paris-quartiers.geojson';
const QUARTIERS_API   = 'https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/quartier_paris/exports/geojson';
const BAN_REVERSE_URL = 'https://api-adresse.data.gouv.fr/reverse/';

const INDICATOR_ICONS = {
  livability_score: 'assistant_navigation',
  connectivity_score: 'wifi',
  mobility_score: 'directions_bike',
  health_env_score: 'eco',
  tranquility_score: 'shield',
  anime_score: 'theater_comedy',
  calme_score: 'volume_off',
  median_price: 'payments',
};

// ─────────────────────────────────────────────────────────────────
// Contrôleur de vue (fitBounds / flyTo) — doit être dans MapContainer
// ─────────────────────────────────────────────────────────────────
function MapViewController({ fitBounds, resetSignal }) {
  const map = useMap();
  useEffect(() => {
    if (fitBounds) map.fitBounds(fitBounds, { padding: [40, 40], maxZoom: 15, duration: 0.6 });
  }, [fitBounds, map]);
  useEffect(() => {
    if (resetSignal > 0) map.flyTo([48.8566, 2.3522], 12, { duration: 0.8 });
  }, [resetSignal, map]);
  return null;
}

// ─────────────────────────────────────────────────────────────────
// Composant principal
// ─────────────────────────────────────────────────────────────────
export default function InteractiveMap({
  indicators,
  selectedIndicator,
  selectedArrondissement,
  onSelectArrondissement,
  chantiers,
  showChantiers,
}) {
  const [quartiersGeoJSON, setQuartiersGeoJSON] = useState(null);
  const [fitBounds, setFitBounds]               = useState(null);
  const [resetSignal, setResetSignal]           = useState(0);
  // Popup pour un quartier cliqué : {lat, lon, nom, address, loadingBan}
  const [quartierPopup, setQuartierPopup]       = useState(null);

  const geoJSONRef = useRef(null);

  // ── Chargement quartiers (local → API fallback) ───────────────
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const r = await fetch(QUARTIERS_LOCAL);
        if (!r.ok) throw new Error('local absent');
        const data = await r.json();
        if (!cancelled) setQuartiersGeoJSON(data);
      } catch {
        try {
          const r = await fetch(QUARTIERS_API);
          if (!r.ok) return;
          const data = await r.json();
          if (!cancelled) setQuartiersGeoJSON(data);
        } catch { /* silencieux */ }
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // ── Fermer la popup quartier si on change d'arrondissement ───
  useEffect(() => { setQuartierPopup(null); }, [selectedArrondissement]);

  // ── BAN reverse geocoding ─────────────────────────────────────
  async function fetchBanAddress(lat, lon) {
    try {
      const r = await fetch(`${BAN_REVERSE_URL}?lon=${lon}&lat=${lat}`);
      if (!r.ok) return null;
      const data = await r.json();
      const props = data?.features?.[0]?.properties;
      return props?.label ?? null;
    } catch { return null; }
  }

  // ── GeoJSON arrondissements (WKT → Features) ─────────────────
  const arrGeoJSON = useMemo(() => {
    if (!indicators?.length) return null;
    const features = indicators
      .map((d) => {
        if (!d.geometry_wkt) return null;
        const geometry = wellknown.parse(d.geometry_wkt);
        if (!geometry) return null;
        return {
          type: 'Feature',
          geometry,
          properties: {
            arrondissement: d.arrondissement,
            nom:            d.nom_arrondissement,
            value:          d[selectedIndicator] ?? null,
          },
        };
      })
      .filter(Boolean);
    return features.length ? { type: 'FeatureCollection', features } : null;
  }, [indicators, selectedIndicator]);

  const allValues = useMemo(
    () => indicators?.map((d) => d[selectedIndicator]).filter((v) => v != null) ?? [],
    [indicators, selectedIndicator],
  );

  // ── Style choroplèthe arrondissements ─────────────────────────
  function styleFeature(feature) {
    const { arrondissement, value } = feature.properties;
    const isSelected = arrondissement === selectedArrondissement;
    return {
      fillColor:   indicatorColor(selectedIndicator, value, allValues),
      fillOpacity: isSelected ? 0.9 : 0.75,
      color:       isSelected ? '#0284C7' : '#9AA6B2',
      weight:      isSelected ? 3 : 1.25,
    };
  }

  // Re-style sans re-monter (sélection change)
  useEffect(() => {
    if (!geoJSONRef.current) return;
    geoJSONRef.current.eachLayer((layer) => {
      const { arrondissement, value } = layer.feature.properties;
      const isSelected = arrondissement === selectedArrondissement;
      layer.setStyle({
        fillColor:   indicatorColor(selectedIndicator, value, allValues),
        fillOpacity: isSelected ? 0.9 : 0.75,
        color:       isSelected ? '#0284C7' : '#9AA6B2',
        weight:      isSelected ? 3 : 1.25,
      });
    });
  }, [selectedArrondissement]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Interactions arrondissements ──────────────────────────────
  function onEachArrFeature(feature, layer) {
    const { arrondissement, nom } = feature.properties;
    layer.on('click', (e) => {
      L.DomEvent.stopPropagation(e);
      setQuartierPopup(null);
      onSelectArrondissement(arrondissement);
      setFitBounds(layer.getBounds());
    });
    layer.on('mouseover', () => {
      if (arrondissement !== selectedArrondissement)
        layer.setStyle({ fillOpacity: 0.88, weight: 2.25, color: '#64748B' });
    });
    layer.on('mouseout', () => {
      const isSel = arrondissement === selectedArrondissement;
      layer.setStyle({
        fillOpacity: isSel ? 0.9 : 0.75,
        color:       isSel ? '#0284C7' : '#9AA6B2',
        weight:      isSel ? 3 : 1.25,
      });
    });
    layer.bindTooltip(
      `<div style="font-size:12px;font-weight:600">${nom ?? `Paris ${arrondissement}e`}</div>
       <div style="font-size:11px;color:#64748B">${formatIndicatorValue(selectedIndicator, feature.properties.value)}</div>`,
      { sticky: true, className: 'leaflet-tooltip-urban' },
    );
  }

  // ── Quartiers filtrés pour l'arrondissement sélectionné ───────
  const quartiersFiltered = useMemo(() => {
    if (!quartiersGeoJSON || !selectedArrondissement) return null;
    const features = (quartiersGeoJSON.features ?? []).filter((f) => {
      const val = f.properties?.c_ar ?? f.properties?.n_sq_ar ?? f.properties?.arrondissement;
      return parseInt(val, 10) === selectedArrondissement;
    });
    return features.length ? { type: 'FeatureCollection', features } : null;
  }, [quartiersGeoJSON, selectedArrondissement]);

  // ── Interactions quartiers — clic → précision IRIS + BAN ─────
  function onEachQuartierFeature(feature, layer) {
    const nom = feature.properties?.l_qu ?? feature.properties?.libelle ?? 'Quartier';
    // fillOpacity minuscule pour rendre la zone cliquable partout
    layer.setStyle({ fillColor: '#0EA5E9', fillOpacity: 0.03 });

    layer.on('click', async (e) => {
      L.DomEvent.stopPropagation(e); // empêche le clic arrondissement
      const { lat, lng } = e.latlng;
      setQuartierPopup({ lat, lon: lng, nom, address: null, loadingBan: true });
      const address = await fetchBanAddress(lat, lng);
      setQuartierPopup((prev) =>
        prev && prev.nom === nom ? { ...prev, address, loadingBan: false } : prev,
      );
    });

    layer.on('mouseover', () => layer.setStyle({ fillOpacity: 0.2, color: '#0284C7' }));
    layer.on('mouseout',  () => layer.setStyle({ fillOpacity: 0.03, color: '#0EA5E9' }));

    layer.bindTooltip(
      `<div style="font-size:11px;color:#0284C7;font-weight:600">
         <span class="map-icon" style="font-size:14px;vertical-align:-2px;margin-right:4px">pin_drop</span>
         ${nom}
       </div>
       <div style="font-size:10px;color:#64748B">Cliquer pour l'adresse exacte</div>`,
      { sticky: true, className: 'leaflet-tooltip-urban' },
    );
  }

  const indicatorLabel = INDICATOR_OPTIONS.find((o) => o.id === selectedIndicator)?.label ?? selectedIndicator;

  function handleBackToGlobal() {
    onSelectArrondissement(null);
    setFitBounds(null);
    setResetSignal((n) => n + 1);
  }

  return (
    <div className="relative w-full h-full min-h-[420px] rounded-xl overflow-hidden border border-[#B6C0CC]">

      {/* Badge indicateur actif */}
      <div className="absolute top-3 right-3 z-[1000]">
        <span className="badge bg-[#F4F6F9]/90 border border-[#B6C0CC] text-[#1E293B] backdrop-blur-sm">
          <span className="map-icon">
            {INDICATOR_ICONS[selectedIndicator] ?? 'insights'}
          </span>
          <span>{indicatorLabel}</span>
        </span>
      </div>

      {/* Bouton retour vue globale */}
      {selectedArrondissement && (
        <button
          className="absolute top-3 left-3 z-[1000] btn-primary text-xs shadow-lg backdrop-blur-sm flex items-center gap-1.5"
          onClick={handleBackToGlobal}
        >
          <span className="map-icon">arrow_back</span>
          Vue globale
        </button>
      )}

      {/* Légende couleur */}
      <ColorLegend indicatorId={selectedIndicator} />

      {/* Badge quartiers actifs */}
      {selectedArrondissement && quartiersFiltered && (
        <div className="absolute bottom-5 right-3 z-[1000] bg-[#F4F6F9]/90 border border-[#38BDF8]/60 rounded-lg px-2 py-1 text-xs backdrop-blur-sm flex items-center gap-1.5 text-[#0284C7]">
          <span className="map-icon" style={{ fontSize: 14 }}>pin_drop</span>
          <span>Quartiers cliquables</span>
        </div>
      )}

      <MapContainer
        center={[48.8566, 2.3522]}
        zoom={12}
        className="w-full h-full"
        zoomControl={false}
        scrollWheelZoom
      >
        <TileLayer url={TILE_URL} attribution={TILE_ATTR} />

        {/* Choroplèthe arrondissements */}
        {arrGeoJSON && (
          <GeoJSON
            key={selectedIndicator}
            ref={geoJSONRef}
            data={arrGeoJSON}
            style={styleFeature}
            onEachFeature={onEachArrFeature}
          />
        )}

        {/* Quartiers du drill-down (cliquables pour précision IRIS + BAN) */}
        {selectedArrondissement && quartiersFiltered && (
          <GeoJSON
            key={`q-${selectedArrondissement}`}
            data={quartiersFiltered}
            style={{
              fillColor:   '#0EA5E9',
              fillOpacity: 0.03,
              color:       '#0EA5E9',
              weight:      1.5,
              dashArray:   '6 4',
            }}
            onEachFeature={onEachQuartierFeature}
          />
        )}

        {/* Popup résultat clic quartier (BAN + nom) */}
        {quartierPopup && (
          <Popup
            position={[quartierPopup.lat, quartierPopup.lon]}
            eventHandlers={{ remove: () => setQuartierPopup(null) }}
          >
            <div style={{ minWidth: 180 }}>
              <p style={{ fontWeight: 700, marginBottom: 4, color: '#0284C7' }}>
                <span
                  className="map-icon"
                  style={{ fontSize: 16, verticalAlign: '-2px', marginRight: 4 }}
                >
                  pin_drop
                </span>
                {quartierPopup.nom}
              </p>
              {quartierPopup.loadingBan ? (
                <p style={{ fontSize: 11, color: '#64748B' }}>Géocodage…</p>
              ) : quartierPopup.address ? (
                <p style={{ fontSize: 12 }}>{quartierPopup.address}</p>
              ) : (
                <p style={{ fontSize: 11, color: '#64748B' }}>
                  {quartierPopup.lat.toFixed(5)}, {quartierPopup.lon.toFixed(5)}
                </p>
              )}
            </div>
          </Popup>
        )}

        {/* Chantiers (marqueurs orange) */}
        {showChantiers && chantiers?.map((c) =>
          c.lat && c.lon ? (
            <CircleMarker
              key={c.id || `${c.lat}-${c.lon}`}
              center={[c.lat, c.lon]}
              radius={5}
              pathOptions={{ color: '#F59E0B', fillColor: '#F59E0B', fillOpacity: 0.85, weight: 1.5 }}
            >
              <Popup>
                <div style={{ minWidth: 190 }}>
                  <p style={{ fontWeight: 600, marginBottom: 4 }}>
                    <span
                      className="map-icon"
                      style={{ fontSize: 16, verticalAlign: '-2px', marginRight: 4 }}
                    >
                      construction
                    </span>
                    {c.titre}
                  </p>
                  {c.categorie && (
                    <p style={{ fontSize: 10, color: '#F59E0B', marginBottom: 4 }}>{c.categorie}</p>
                  )}
                  {c.description && (
                    <p style={{ fontSize: 11, color: '#64748B', marginBottom: 4 }}>{c.description}</p>
                  )}
                  {(c.date_debut || c.date_fin) && (
                    <p style={{ fontSize: 11 }}>
                      {c.date_debut ? `Début : ${String(c.date_debut).slice(0, 10)}` : ''}
                      {c.date_fin   ? ` → ${String(c.date_fin).slice(0, 10)}` : ''}
                    </p>
                  )}
                  {c.maitre_ouvrage && (
                    <p style={{ fontSize: 10, color: '#64748B', marginTop: 4 }}>{c.maitre_ouvrage}</p>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          ) : null,
        )}

        <MapViewController fitBounds={fitBounds} resetSignal={resetSignal} />
      </MapContainer>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Légende choroplèthe
// ─────────────────────────────────────────────────────────────────
function ColorLegend({ indicatorId }) {
  const isPrice = indicatorId === 'median_price';
  return (
    <div className="absolute bottom-5 left-3 z-[1000] bg-[#F4F6F9]/95 border border-[#B6C0CC] rounded-lg px-3 py-2 text-xs backdrop-blur-sm">
      <p className="text-[#64748B] mb-1.5 font-medium flex items-center gap-1">
        <span className="map-icon" style={{ fontSize: 14, verticalAlign: '-2px' }}>
          {isPrice ? 'payments' : 'insights'}
        </span>
        <span>{isPrice ? 'Prix m²' : 'Score'}</span>
      </p>
      <div className="flex items-center gap-1.5">
        <div
          className="w-16 h-2 rounded-full"
          style={{
            background: isPrice
              ? 'linear-gradient(to right, #22C55E, #F43F5E)'
              : 'linear-gradient(to right, #F43F5E, #F59E0B, #22C55E)',
          }}
        />
        <span className="text-[#64748B]">{isPrice ? 'Bas → Élevé' : '0 → 100'}</span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Formatage tooltip
// ─────────────────────────────────────────────────────────────────
function formatIndicatorValue(indicatorId, value) {
  if (value == null) return 'Donnée manquante';
  if (indicatorId === 'median_price')
    return `${new Intl.NumberFormat('fr-FR').format(Math.round(value))} €/m²`;
  return `${value.toFixed(1)} / 100`;
}
