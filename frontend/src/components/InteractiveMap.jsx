import { useMemo, useState, useEffect, useRef, useCallback } from 'react';
import { MapContainer, GeoJSON, TileLayer, useMap, CircleMarker, Popup, useMapEvents } from 'react-leaflet';
import wellknown from 'wellknown';
import { indicatorColor } from '../utils/scoreColors';
import { INDICATOR_OPTIONS } from './Sidebar';

// Tuiles sombres CartoDB — cohérentes avec le thème dark du dashboard
const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
  'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

// API BAN (Base Adresse Nationale) — géocodage inverse
const BAN_REVERSE_URL = 'https://api-adresse.data.gouv.fr/reverse/';
// IRIS contours depuis Paris Open Data
const IRIS_API = 'https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/iris-demographie/exports/geojson?limit=500';

// GeoJSON statique des 80 quartiers de Paris
// Source : Paris OpenData — quartier_paris
// Le fichier est chargé depuis /data/paris-quartiers.geojson (public/)
// OU téléchargé depuis l'API Paris OpenData en fallback.
const QUARTIERS_LOCAL = '/data/paris-quartiers.geojson';
const QUARTIERS_API =
  'https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/quartier_paris/exports/geojson';

// ─────────────────────────────────────────────────────────────────
// Sous-composant : contrôleur de vue (fitBounds / flyTo)
// Doit être enfant de MapContainer pour accéder à useMap()
// ─────────────────────────────────────────────────────────────────
function MapViewController({ fitBounds, resetSignal }) {
  const map = useMap();

  useEffect(() => {
    if (fitBounds) {
      map.fitBounds(fitBounds, { padding: [40, 40], maxZoom: 15, duration: 0.6 });
    }
  }, [fitBounds, map]);

  useEffect(() => {
    if (resetSignal > 0) {
      map.flyTo([48.8566, 2.3522], 12, { duration: 0.8 });
    }
  }, [resetSignal, map]);

  return null;
}

// ─────────────────────────────────────────────────────────────────
// Composant principal
// ─────────────────────────────────────────────────────────────────
export default function InteractiveMap({
  indicators,          // ArrondissementDetail[]
  selectedIndicator,   // string (clé de score)
  selectedArrondissement,
  onSelectArrondissement,
  chantiers,           // Chantier[] (depuis useChantiers)
  showChantiers,       // bool
}) {
  const [quartiersGeoJSON, setQuartiersGeoJSON] = useState(null);
  const [irisGeoJSON, setIrisGeoJSON]         = useState(null);
  const [fitBounds, setFitBounds]             = useState(null);
  const [resetSignal, setResetSignal]         = useState(0);
  const [banPopup, setBanPopup]               = useState(null); // {lat, lon, label, iris}

  const geoJSONRef = useRef(null);  // ref vers la couche Leaflet GeoJSON

  // ── Chargement IRIS (quand arrondissement sélectionné) ───────
  useEffect(() => {
    if (!selectedArrondissement) { setIrisGeoJSON(null); return; }
    let cancelled = false;
    async function loadIris() {
      try {
        const url = `${IRIS_API}&where=c_ar%3D${selectedArrondissement}`;
        const r = await fetch(url);
        if (!r.ok) return;
        const data = await r.json();
        if (!cancelled && data?.features?.length) setIrisGeoJSON(data);
      } catch { /* IRIS optionnel, échec silencieux */ }
    }
    loadIris();
    return () => { cancelled = true; };
  }, [selectedArrondissement]);

  // ── Chargement des quartiers (local → fallback API) ──────────
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
        } catch { /* drill-down désactivé silencieusement */ }
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // ── Conversion WKT → GeoJSON FeatureCollection ───────────────
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
    return features.length
      ? { type: 'FeatureCollection', features }
      : null;
  }, [indicators, selectedIndicator]);

  // Valeurs pour l'interpolation de couleur (min/max du dataset)
  const allValues = useMemo(
    () => indicators?.map((d) => d[selectedIndicator]).filter((v) => v != null) ?? [],
    [indicators, selectedIndicator]
  );

  // ── Style choroplèthe ─────────────────────────────────────────
  function styleFeature(feature) {
    const { arrondissement, value } = feature.properties;
    const isSelected = arrondissement === selectedArrondissement;
    return {
      fillColor:   indicatorColor(selectedIndicator, value, allValues),
      fillOpacity: isSelected ? 0.85 : 0.65,
      color:       isSelected ? '#818CF8' : '#0F172A',
      weight:      isSelected ? 2.5 : 1,
    };
  }

  // ── Mise à jour des styles sans re-monter la couche ───────────
  // (seulement quand selectedArrondissement change, pas quand l'indicateur change)
  useEffect(() => {
    if (!geoJSONRef.current) return;
    geoJSONRef.current.eachLayer((layer) => {
      const { arrondissement, value } = layer.feature.properties;
      const isSelected = arrondissement === selectedArrondissement;
      layer.setStyle({
        fillColor:   indicatorColor(selectedIndicator, value, allValues),
        fillOpacity: isSelected ? 0.85 : 0.65,
        color:       isSelected ? '#818CF8' : '#0F172A',
        weight:      isSelected ? 2.5 : 1,
      });
    });
  }, [selectedArrondissement]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Interactions par feature ──────────────────────────────────
  function onEachFeature(feature, layer) {
    const { arrondissement, nom } = feature.properties;

    layer.on('click', () => {
      onSelectArrondissement(arrondissement);
      setFitBounds(layer.getBounds());
    });

    layer.on('mouseover', () => {
      if (arrondissement !== selectedArrondissement) {
        layer.setStyle({ fillOpacity: 0.85, weight: 2, color: '#475569' });
      }
    });

    layer.on('mouseout', () => {
      const isSelected = arrondissement === selectedArrondissement;
      layer.setStyle({
        fillOpacity: isSelected ? 0.85 : 0.65,
        color:       isSelected ? '#818CF8' : '#0F172A',
        weight:      isSelected ? 2.5 : 1,
      });
    });

    layer.bindTooltip(
      `<div style="font-size:12px;font-weight:600">${nom ?? `Paris ${arrondissement}e`}</div>
       <div style="font-size:11px;color:#94A3B8">${formatIndicatorValue(selectedIndicator, feature.properties.value)}</div>`,
      { sticky: true, className: 'leaflet-tooltip-urban' }
    );
  }

  // ── Géocodage inverse BAN (clic sur la carte) ────────────────
  const handleMapClick = useCallback(async (lat, lon) => {
    try {
      const r = await fetch(`${BAN_REVERSE_URL}?lon=${lon}&lat=${lat}`);
      if (!r.ok) return;
      const data = await r.json();
      const feat = data?.features?.[0];
      if (!feat) return;
      const props = feat.properties;
      setBanPopup({
        lat,
        lon,
        label: props.label ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`,
        postcode: props.postcode,
        city: props.city,
        score: props.score,
      });
    } catch { /* géocodage optionnel */ }
  }, []);

  // ── Quartiers du drill-down ───────────────────────────────────
  const quartiersFiltered = useMemo(() => {
    if (!quartiersGeoJSON || !selectedArrondissement) return null;
    const features = (quartiersGeoJSON.features ?? []).filter((f) => {
      // Le champ arrondissement peut être c_ar (int) ou n_sq_ar (string)
      const val = f.properties?.c_ar ?? f.properties?.n_sq_ar ?? f.properties?.arrondissement;
      return parseInt(val, 10) === selectedArrondissement;
    });
    return features.length ? { type: 'FeatureCollection', features } : null;
  }, [quartiersGeoJSON, selectedArrondissement]);

  // ── Nom de l'indicateur affiché ───────────────────────────────
  const indicatorLabel =
    INDICATOR_OPTIONS.find((o) => o.id === selectedIndicator)?.label ?? selectedIndicator;

  function handleBackToGlobal() {
    onSelectArrondissement(null);
    setFitBounds(null);
    setResetSignal((n) => n + 1);
  }

  return (
    <div className="relative w-full h-full min-h-[420px] rounded-xl overflow-hidden border border-slate-700">

      {/* Badge indicateur actif */}
      <div className="absolute top-3 right-3 z-[1000]">
        <span className="badge bg-slate-900/90 border border-slate-600 text-slate-300 backdrop-blur-sm">
          {INDICATOR_OPTIONS.find((o) => o.id === selectedIndicator)?.icon} {indicatorLabel}
        </span>
      </div>

      {/* Bouton retour vue globale */}
      {selectedArrondissement && (
        <button
          className="absolute top-3 left-3 z-[1000] btn-primary text-xs shadow-lg backdrop-blur-sm"
          onClick={handleBackToGlobal}
        >
          ← Vue globale
        </button>
      )}

      {/* Légende couleur */}
      <ColorLegend indicatorId={selectedIndicator} />

      {/* Badge IRIS actif */}
      {selectedArrondissement && irisGeoJSON && (
        <div className="absolute bottom-5 right-3 z-[1000] bg-slate-900/90 border border-orange-500/40 rounded-lg px-2 py-1 text-xs backdrop-blur-sm flex items-center gap-1">
          <span className="w-3 h-0.5 bg-orange-400 opacity-70 inline-block" style={{ borderTop: '1px dashed #F97316' }} />
          <span className="text-orange-400">IRIS</span>
        </div>
      )}

      <MapContainer
        center={[48.8566, 2.3522]}
        zoom={12}
        className="w-full h-full"
        zoomControl={false}
        scrollWheelZoom
      >
        {/* Tuiles sombres */}
        <TileLayer url={TILE_URL} attribution={TILE_ATTR} />

        {/* Choroplèthe arrondissements (clé = indicateur → re-monte si indicateur change) */}
        {arrGeoJSON && (
          <GeoJSON
            key={selectedIndicator}
            ref={geoJSONRef}
            data={arrGeoJSON}
            style={styleFeature}
            onEachFeature={onEachFeature}
          />
        )}

        {/* Contours quartiers du drill-down */}
        {selectedArrondissement && quartiersFiltered && (
          <GeoJSON
            key={`q-${selectedArrondissement}`}
            data={quartiersFiltered}
            style={{
              fillColor:   'transparent',
              fillOpacity: 0,
              color:       '#A5B4FC',
              weight:      1.5,
              dashArray:   '6 4',
            }}
          />
        )}

        {/* Contours IRIS (précision fine — chargé si arrondissement sélectionné) */}
        {selectedArrondissement && irisGeoJSON && (
          <GeoJSON
            key={`iris-${selectedArrondissement}`}
            data={irisGeoJSON}
            style={{
              fillColor:   'transparent',
              fillOpacity: 0,
              color:       '#FB923C',
              weight:      1,
              dashArray:   '3 3',
            }}
            onEachFeature={(feature, layer) => {
              const irisCode = feature.properties?.iris_code
                ?? feature.properties?.dcomiris
                ?? feature.properties?.code_iris
                ?? '';
              const irisLabel = feature.properties?.nom_iris
                ?? feature.properties?.libiris
                ?? irisCode;
              if (irisLabel) {
                layer.bindTooltip(
                  `<div style="font-size:11px;color:#FB923C">IRIS : ${irisLabel}</div>`,
                  { sticky: true, className: 'leaflet-tooltip-urban' }
                );
              }
            }}
          />
        )}

        {/* Chantiers (marqueurs orange) */}
        {showChantiers && chantiers?.map((c) =>
          c.lat && c.lon ? (
            <CircleMarker
              key={c.id || `${c.lat}-${c.lon}`}
              center={[c.lat, c.lon]}
              radius={6}
              pathOptions={{
                color: '#EA580C',
                fillColor: '#F97316',
                fillOpacity: 0.85,
                weight: 1.5,
              }}
            >
              <Popup>
                <div style={{ minWidth: 180 }}>
                  <p style={{ fontWeight: 600, marginBottom: 4 }}>🚧 {c.titre}</p>
                  {c.adresse && <p style={{ fontSize: 11, color: '#64748B' }}>{c.adresse}</p>}
                  {(c.date_debut || c.date_fin) && (
                    <p style={{ fontSize: 11, marginTop: 4 }}>
                      {c.date_debut ? `Début : ${c.date_debut.slice(0, 10)}` : ''}
                      {c.date_fin   ? ` — Fin : ${c.date_fin.slice(0, 10)}` : ''}
                    </p>
                  )}
                  {c.statut && <p style={{ fontSize: 11, color: '#94A3B8', marginTop: 2 }}>{c.statut}</p>}
                </div>
              </Popup>
            </CircleMarker>
          ) : null
        )}

        {/* Popup géocodage BAN */}
        {banPopup && (
          <Popup
            position={[banPopup.lat, banPopup.lon]}
            eventHandlers={{ remove: () => setBanPopup(null) }}
          >
            <div style={{ minWidth: 160 }}>
              <p style={{ fontWeight: 600, marginBottom: 4 }}>📍 Adresse</p>
              <p style={{ fontSize: 12 }}>{banPopup.label}</p>
              {banPopup.postcode && (
                <p style={{ fontSize: 11, color: '#64748B', marginTop: 2 }}>
                  {banPopup.postcode} {banPopup.city}
                </p>
              )}
            </div>
          </Popup>
        )}

        {/* Listener clic carte → BAN geocoding */}
        <MapClickHandler onMapClick={handleMapClick} />

        {/* Contrôleur de vue */}
        <MapViewController fitBounds={fitBounds} resetSignal={resetSignal} />
      </MapContainer>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Listener clic carte (enfant de MapContainer)
// ─────────────────────────────────────────────────────────────────
function MapClickHandler({ onMapClick }) {
  useMapEvents({
    click(e) {
      onMapClick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

// ─────────────────────────────────────────────────────────────────
// Légende choroplèthe (bas-gauche de la carte)
// ─────────────────────────────────────────────────────────────────
function ColorLegend({ indicatorId }) {
  const isPrice = indicatorId === 'median_price';
  return (
    <div className="absolute bottom-5 left-3 z-[1000] bg-slate-900/90 border border-slate-700 rounded-lg px-3 py-2 text-xs backdrop-blur-sm">
      <p className="text-slate-400 mb-1.5 font-medium">{isPrice ? 'Prix m²' : 'Score'}</p>
      <div className="flex items-center gap-1.5">
        <div
          className="w-16 h-2 rounded-full"
          style={{
            background: isPrice
              ? 'linear-gradient(to right, #10B981, #EF4444)'
              : 'linear-gradient(to right, #EF4444, #F59E0B, #10B981)',
          }}
        />
        <span className="text-slate-500">
          {isPrice ? 'Bas → Élevé' : '0 → 100'}
        </span>
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
